"""Unified command-line interface; failures never silently become valid runs."""

import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import shutil
from contextlib import contextmanager
from functools import partial
import uuid

from . import __version__
from .analysis import build_report
from .config import load_config
from .delivery import package_report, verify_workbook, workbook_name
from .media import finalize_report
from .migration import import_history
from .model import MonitorError, SourceError, local_day, today
from .network import load_network_config
from .reports import export_report
from .sources import collect_external
from .store import Store


FALLBACK_CODES = {"DNS_PUBLIC_UNAVAILABLE", "PROXY_UNAVAILABLE", "NETWORK_ERROR",
                  "COVER_NETWORK_UNAVAILABLE", "COVER_SOURCE_UNAVAILABLE"}


def _print(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _report_status(report):
    return {key: report[key] for key in ("metric", "complete", "account_coverage_complete", "missing_accounts", "metric_coverage", "warnings")}


def parser():
    result = argparse.ArgumentParser(description="Local Short Drama Monitor (Asia/Shanghai)")
    result.add_argument("--version", action="version", version=f"tkmonitor {__version__}")
    result.add_argument("--config", default="config.local.json", help="Portable local config path")
    commands = result.add_subparsers(dest="command", required=True)
    migrate = commands.add_parser("import-history", help="Import original local history atomically and idempotently")
    migrate.add_argument("--source", required=True)
    report = commands.add_parser("report", help="Export full-digit CSV/JSON/offline HTML and XLSX compatibility input")
    report.add_argument("--day", default=None, help="Beijing calendar day; defaults to today")
    report.add_argument("--metric", choices=("display", "frontend"), required=True)
    report.add_argument("--output", default=None)
    report.add_argument("--no-covers", action="store_true")
    commands.add_parser("status", help="Show valid history, today's coverage and collection attempts")
    collect = commands.add_parser("collect", help="Collect complete fresh snapshots and commit the whole selected batch")
    collect.add_argument("--day", default=None, help="Expected Beijing day; defaults to today, never inferred from an old file")
    collect.add_argument("--account", action="append", help="Restrict to configured account(s)")
    collect.add_argument("--report", action="store_true", help="Export a report after the valid collection commits")
    collect.add_argument("--metric", choices=("display", "frontend"), default="frontend",
                         help="Report metric; defaults to frontend because HTTP supplies precise counts")
    collect.add_argument("--xlsx", action="store_true", help="Also invoke the optional Node exporter after reporting")
    collect.add_argument("--finalize", action="store_true", help="Complete thumbnails, label synopsis provenance and verify report coverage")
    collect.add_argument("--delivery", default=None, help="After finalization and XLSX export, verify and copy the complete delivery here")
    collect.add_argument("--recover-request", action="store_true", help="On HTTP 401/403, prepare one resumable Chrome recovery task instead of repeatedly opening pages")
    collect.add_argument("--browser-fallback", action="store_true", help="Prepare a full Chrome capture task after an eligible network failure")
    collect.add_argument("--allow-scroll-fallback", action="store_true", help="Allow at most one scroll in an explicitly requested browser recovery")
    collect.add_argument("--browser-timeout", type=float, default=90, help="Browser preparation budget, 5..300 seconds")
    onboard = commands.add_parser("onboard", help="Reuse an account request or prepare a resumable authorized-Chrome capture")
    target = onboard.add_mutually_exclusive_group(required=True)
    target.add_argument("--url", help="HTTPS TikTok account homepage")
    target.add_argument("--resume", help="Private onboarding manifest returned by a previous invocation")
    onboard.add_argument("--name", default=None)
    onboard.add_argument("--day", default=None, help="Defaults to the actual Beijing collection day")
    onboard.add_argument("--dry-run", action="store_true", help="Plan only: no requests, browser actions or writes")
    onboard.add_argument("--new-task", action="store_true", help="Explicitly prepare a new task instead of reusing an interrupted pending capture")
    onboard.add_argument("--allow-scroll-fallback", action="store_true", help="Allow one optional scroll; default is no scrolling")
    onboard.add_argument("--browser-fallback", action="store_true", help="Prepare a full Chrome capture task after an eligible network failure")
    onboard.add_argument("--browser-timeout", type=float, default=90, help="Browser preparation budget, 5..300 seconds")
    onboard.add_argument("--report", action="store_true")
    onboard.add_argument("--metric", choices=("display", "frontend"), default="frontend")
    onboard.add_argument("--finalize", action="store_true")
    onboard.add_argument("--xlsx", action="store_true")
    onboard.add_argument("--delivery", default=None)
    finalize = commands.add_parser("finalize", help="Complete media and verify an already collected day without recollecting")
    finalize.add_argument("--day", default=None)
    finalize.add_argument("--metric", choices=("display", "frontend"), default="frontend")
    finalize.add_argument("--output", default=None)
    finalize.add_argument("--no-download", action="store_true", help="Use saved local covers only")
    package = commands.add_parser("package", help="Verify saved XLSX files and package a complete report")
    package.add_argument("--day", default=None)
    package.add_argument("--metric", choices=("display", "frontend"), default="frontend")
    package.add_argument("--folder", default=None)
    package.add_argument("--output", default=None)
    xlsx = commands.add_parser("export-xlsx", help="Invoke the separate optional Node workbook integration")
    xlsx.add_argument("--day", default=None)
    xlsx.add_argument("--metric", choices=("display", "frontend"), required=True)
    xlsx.add_argument("--folder", default=None, help="Folder already containing generated workbook_configs.json")
    for command in (report, collect, onboard, finalize, xlsx, package):
        language = command.add_mutually_exclusive_group()
        language.add_argument("--translate", action="store_true", default=None,
                              help="Explicitly require Chinese titles and synopses; the default uses original text")
        language.add_argument("--allow-untranslated", dest="translate", action="store_false",
                              help="Compatibility option for the default original-text delivery")
    translation = commands.add_parser("translation-tasks", help="Export only missing or changed-source Chinese translation work")
    translation.add_argument("--day", default=None)
    translation.add_argument("--metric", choices=("display", "frontend"), default="frontend")
    translation.add_argument("--output", default=None)
    translated = commands.add_parser("import-translations", help="Validate exact saved originals and import Chinese companion metadata without changing observations")
    translated.add_argument("--input", required=True)
    return result


@contextmanager
def _xlsx_staging(folder):
    # Normal mkdir inherits Windows workspace access, including for Node children.
    staging = (folder / (".xlsx-" + uuid.uuid4().hex)).resolve()
    if staging.parent != folder.resolve():
        raise MonitorError("XLSX staging escaped its report folder.")
    staging.mkdir()
    try:
        yield staging
    finally:
        if staging.resolve().parent != folder.resolve():
            raise MonitorError("Refusing to remove an XLSX staging folder outside its report folder.")
        shutil.rmtree(staging)


def export_xlsx(config, folder):
    folder = Path(folder).resolve()
    if not (folder / "workbook_configs.json").is_file():
        raise MonitorError("Generate a report first: the XLSX folder needs workbook_configs.json.")
    script = Path(__file__).resolve().parent.parent / "integrations" / "export_workbooks.mjs"
    if not script.is_file():
        raise MonitorError("Optional XLSX integration is unavailable; CSV, JSON and HTML remain valid.")
    node = os.environ.get("TKMONITOR_NODE", "node")
    env = os.environ.copy()
    if env.get("TKMONITOR_NODE_MODULES"):
        env["NODE_PATH"] = env["TKMONITOR_NODE_MODULES"]
    accounts = json.loads((folder / "workbook_configs.json").read_text(encoding="utf-8-sig"))
    if not isinstance(accounts, list) or not accounts:
        raise MonitorError("XLSX export needs nonempty workbook configurations.")
    manifests, verified = [], []
    for account in accounts:
        filename = workbook_name(account)
        prefix = account["prefix"]
        # Separate processes release the spreadsheet engine's entire heap between accounts.
        with _xlsx_staging(folder) as staging:
            (staging / "workbook_configs.json").write_text(json.dumps([account], ensure_ascii=False), encoding="utf-8")
            try:
                with (folder / (prefix + "_xlsx.stdout.log")).open("wb") as out, (folder / (prefix + "_xlsx.stderr.log")).open("wb") as err:
                    node_args = [node, "--expose-gc", "--no-opt", "--v8-pool-size=1", "--max-old-space-size=384",
                                 "--no-wasm-tier-up", "--wasm-num-compilation-tasks=1", str(script), str(staging)]
                    result = subprocess.run(node_args, cwd=script.parent.parent,
                                            env=env, stdout=out, stderr=err, check=False)
            except OSError as exc:
                raise MonitorError("Cannot start the XLSX runtime; set TKMONITOR_NODE and TKMONITOR_NODE_MODULES. Valid collection data is preserved.") from exc
            if result.returncode:
                raise MonitorError("Optional XLSX export failed; inspect the account's _xlsx.stderr.log in the report folder. Valid collection data is preserved.")
            verified.append(verify_workbook(staging / filename, account))
            manifests.extend(json.loads((staging / "export_manifest.json").read_text(encoding="utf-8")))
            for path in staging.iterdir():
                if path.suffix in {".xlsx", ".png", ".ndjson"}:
                    shutil.copy2(path, folder / path.name)
    for filename, value in (("export_manifest.json", manifests), ("workbooks_verified.json", verified)):
        (folder / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"xlsx_export": "completed", "output": str(folder), "workbooks": verified}


def export_collection(config, store, args, *, network=None):
    """Export separately so a resumable onboarding never refetches committed data."""
    expected_day = args.day or today()
    translate = bool(getattr(args, "translate", False))
    if args.report or args.xlsx or args.finalize or args.delivery or translate:
        if translate:
            from .translations import require_chinese
            require_chinese(store, config, expected_day, args.metric)
        if args.finalize or args.delivery:
            folder, report = finalize_report(store, config, expected_day, args.metric, include_translations=translate,
                                             network=network)
        else:
            report = build_report(store, config, expected_day, args.metric, include_translations=translate)
            folder = export_report(store, config, report)
        _print({"report": str(folder), **_report_status(report)})
        if args.xlsx or args.delivery:
            _print(export_xlsx(config, folder))
        if (args.finalize and args.xlsx) or args.delivery:
            _print(package_report(folder, config.resolve(args.delivery) if args.delivery else None))
        return 0 if report["complete"] else 3
    return 0


def collect_and_export(config, store, args, progress=None, *, network=None):
    selected = set(args.account or config.handles)
    if not selected <= config.handles:
        raise MonitorError("Requested collection account is not configured.")
    expected_day = args.day or today()
    attempt = store.start_attempt()
    try:
        if progress:
            progress("collecting", {"attempt": attempt, "collected_day": expected_day})
        snapshots = []
        for account in config.accounts:
            if account["handle"] in selected:
                print("Collecting " + account["handle"] + " for Beijing day " + expected_day + ".", flush=True)
                raw_dir = config.state_dir / "raw" / "attempts" / attempt / account["handle"]
                try:
                    snapshots.append(collect_external(config, account, expected_day, raw_dir, network=network))
                except SourceError as exc:
                    exc.account = account["handle"]
                    raise
        if progress:
            progress("prepared", {"prepared_snapshots": [{"id": s.id, "fingerprint": s.digest} for s in snapshots]})
        added = store.commit_batch(snapshots, selected)
    except KeyboardInterrupt:
        store.finish_attempt(attempt, "failed", "Collection interrupted; incomplete batch was not committed.")
        raise
    except Exception as exc:
        message = str(exc) if isinstance(exc, MonitorError) else "Collection failed; inspect the local source and runtime. No partial batch was committed."
        store.finish_attempt(attempt, "failed", message)
        if isinstance(exc, MonitorError):
            raise
        raise MonitorError(message) from exc
    store.finish_attempt(attempt, "valid", "Complete validated batch committed.")
    if progress:
        progress("collected", {"imported_snapshots": added})
    _print({"collection": "valid", "attempt": attempt, "imported_snapshots": added,
            "accounts": sorted(selected), "day": expected_day})
    # The export remains strictly after the whole-batch transaction.
    return export_collection(config, store, args, network=network)


def _check_translated_report(store, config, folder):
    from .config import read_json
    from .translations import require_chinese

    saved = read_json(folder / "merged.json")
    if not saved.get("include_translations", True):
        raise MonitorError("Regenerate this original-text report with finalize --translate before exporting or packaging it in Chinese.")
    require_chinese(store, config, saved["report_day"], saved["metric"], report=saved)


def _browser_fallback_enabled(args, network):
    return getattr(args, "browser_fallback", False) or getattr(network, "browser_fallback", False)


def _offer_browser_fallback(base, config, args, exc, *, origin_manifest=None):
    from .browser_fallback import create_fallback

    phase = "covers" if exc.code in {"COVER_NETWORK_UNAVAILABLE", "COVER_SOURCE_UNAVAILABLE"} else "collect"
    _print(create_fallback(base, config, args, selected=set(args.account or config.handles),
                           reason=exc.code, phase=phase, origin_manifest=origin_manifest))
    return 4


def run(args):
    config = load_config(args.config)
    if args.command in {"onboard", "collect"} and not 5 <= args.browser_timeout <= 300:
        raise MonitorError("Browser preparation budget must be between 5 and 300 seconds.")
    if args.command == "onboard":
        if args.resume and (args.dry_run or args.new_task or args.name or args.day or args.allow_scroll_fallback or args.browser_fallback or args.report or args.finalize or args.xlsx or args.delivery or args.metric != "frontend" or args.browser_timeout != 90 or args.translate is not None):
            raise MonitorError("Resume uses the saved task options; do not supply new target or execution options.")
        from .onboarding import run_onboard
        if args.dry_run:
            return run_onboard(config, args, collect_and_export, export_collection, _print)
        from .browser_fallback import is_fallback, linked_fallback, resume_fallback
        network = load_network_config(config.path.parent)
        export = partial(export_collection, network=network)
        if args.resume and is_fallback(args.resume, config):
            return resume_fallback(config, args.resume, export, _print)
        if args.resume:
            linked = linked_fallback(config, args.resume)
            if linked:
                return resume_fallback(config, linked, export, _print)
        try:
            return run_onboard(config, args, partial(collect_and_export, network=network), export, _print)
        except SourceError as exc:
            if exc.code not in FALLBACK_CODES:
                raise
            from argparse import Namespace
            from .onboarding import account_plan, load_task, profile_handle
            if args.resume:
                task, path = load_task(config, args.resume)
                options = dict(task["options"], account=task["selected_accounts"],
                               browser_timeout=task["browser_timeout_seconds"])
                if task.get("collected_day"):
                    options["day"] = task["collected_day"]
                saved_args = Namespace(**options)
                if not _browser_fallback_enabled(saved_args, network):
                    raise
                return _offer_browser_fallback(config, load_config(task["account_config"]), saved_args, exc,
                                               origin_manifest=path)
            if not _browser_fallback_enabled(args, network):
                raise
            plan = account_plan(config, profile_handle(args.url), args.name)
            options = Namespace(**vars(args), account=[plan["handle"]])
            return _offer_browser_fallback(config, load_config(plan["config_path"]), options, exc)
    with Store(config.state_dir) as store:
        if args.command == "translation-tasks":
            from .translations import translation_tasks
            result = translation_tasks(store, config, args.day or today(), args.metric, output=args.output)
            _print(result)
            return 5 if result["pending"] else 0
        elif args.command == "import-translations":
            from .translations import import_translations
            _print(import_translations(store, config, args.input))
        elif args.command == "import-history":
            _print(import_history(store, config, args.source))
        elif args.command == "status":
            status = store.status()
            latest = {snapshot.account for snapshot in store.snapshots() if local_day(snapshot.captured_at) == today()}
            status["today"] = today()
            status["missing_today"] = sorted(config.handles - latest)
            status["state_dir"] = str(config.state_dir)
            _print(status)
        elif args.command == "report":
            report = build_report(store, config, args.day or today(), args.metric, include_translations=bool(args.translate))
            if args.translate:
                from .translations import require_chinese
                require_chinese(store, config, report["report_day"], args.metric, report=report)
            output = export_report(store, config, report, args.output, not args.no_covers)
            _print({"output": str(output), **_report_status(report), "summary": report["summary"]})
            return 0 if report["complete"] else 3
        elif args.command == "export-xlsx":
            folder = config.resolve(args.folder) if args.folder else config.report_dir / (args.day or today()) / args.metric
            if args.translate:
                _check_translated_report(store, config, folder)
            _print(export_xlsx(config, folder))
        elif args.command == "finalize":
            if args.translate:
                from .translations import require_chinese
                require_chinese(store, config, args.day or today(), args.metric)
            output = config.resolve(args.output) if args.output else None
            folder, report = finalize_report(store, config, args.day or today(), args.metric, output, not args.no_download,
                                             include_translations=bool(args.translate),
                                             network=load_network_config(config.path.parent) if not args.no_download else None)
            _print({"output": str(folder), "media_coverage": report["media_coverage"], **_report_status(report)})
        elif args.command == "package":
            folder = config.resolve(args.folder) if args.folder else config.report_dir / (args.day or today()) / args.metric
            if args.translate:
                _check_translated_report(store, config, folder)
            output = config.resolve(args.output) if args.output else None
            _print(package_report(folder, output))
        elif args.command == "collect":
            network = load_network_config(config.path.parent)
            try:
                return collect_and_export(config, store, args, network=network)
            except SourceError as exc:
                if exc.code in FALLBACK_CODES and _browser_fallback_enabled(args, network):
                    selected = set(args.account or config.handles)
                    if exc.code in {"COVER_NETWORK_UNAVAILABLE", "COVER_SOURCE_UNAVAILABLE"} or all(
                            account.get("source", {}).get("type", "http") == "http"
                            for account in config.accounts if account["handle"] in selected):
                        return _offer_browser_fallback(config, config, args, exc)
                if not args.recover_request or exc.code != "AUTH_REQUIRED":
                    raise
                from .onboarding import account_lock, account_plan, create_browser_task, pending_result
                with account_lock(config.path.parent, exc.account):
                    plan = account_plan(config, exc.account, use_default_source=True)
                    task, path = create_browser_task(config, plan, args, scope="batch", selected=set(args.account or config.handles))
                    _print(pending_result(task, path))
                return 4
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return run(args)
    except MonitorError as exc:
        if getattr(exc, "code", None) == "NEEDS_TRANSLATION":
            _print(exc.result)
            return 5
        code = getattr(exc, "code", None)
        print(f"ERROR [{code}]: {exc}" if code else "ERROR: " + str(exc), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        # External errors can contain signed URLs or headers. Never echo them.
        print("ERROR: Local configuration, storage or export failed; verify local paths, schema and runtime. No failed collection is reported as valid.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: Interrupted; incomplete source results were not committed as valid snapshots.", file=sys.stderr)
        return 130
