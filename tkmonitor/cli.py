"""Unified command-line interface; failures never silently become valid runs."""

import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from .analysis import build_report
from .config import load_config
from .migration import import_history
from .model import MonitorError, local_day, today
from .reports import export_report
from .sources import collect_external
from .store import Store


def _print(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def parser():
    result = argparse.ArgumentParser(description="Local Short Drama Monitor (Asia/Shanghai)")
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
    collect.add_argument("--metric", choices=("display", "frontend"), default="display")
    collect.add_argument("--xlsx", action="store_true", help="Also invoke the optional Node exporter after reporting")
    xlsx = commands.add_parser("export-xlsx", help="Invoke the separate optional Node workbook integration")
    xlsx.add_argument("--day", default=None)
    xlsx.add_argument("--metric", choices=("display", "frontend"), required=True)
    xlsx.add_argument("--folder", default=None, help="Folder already containing generated workbook_configs.json")
    return result


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
    try:
        with (folder / "xlsx.stdout.log").open("wb") as out, (folder / "xlsx.stderr.log").open("wb") as err:
            node_args = [node, "--expose-gc", "--v8-pool-size=1", "--max-old-space-size=1024",
                         "--no-wasm-tier-up", "--wasm-num-compilation-tasks=1", str(script), str(folder)]
            result = subprocess.run(node_args, cwd=script.parent.parent,
                                    env=env, stdout=out, stderr=err, check=False)
    except OSError as exc:
        raise MonitorError("Cannot start the XLSX runtime; set TKMONITOR_NODE and TKMONITOR_NODE_MODULES. Valid collection data is preserved.") from exc
    if result.returncode:
        raise MonitorError("Optional XLSX export failed; inspect xlsx.stderr.log in the report folder. Valid collection data is preserved.")
    return {"xlsx_export": "completed", "output": str(folder)}


def run(args):
    config = load_config(args.config)
    with Store(config.state_dir) as store:
        if args.command == "import-history":
            _print(import_history(store, config, args.source))
        elif args.command == "status":
            status = store.status()
            latest = {snapshot.account for snapshot in store.snapshots() if local_day(snapshot.captured_at) == today()}
            status["today"] = today()
            status["missing_today"] = sorted(config.handles - latest)
            status["state_dir"] = str(config.state_dir)
            _print(status)
        elif args.command == "report":
            report = build_report(store, config, args.day or today(), args.metric)
            output = export_report(store, config, report, args.output, not args.no_covers)
            _print({"output": str(output), "metric": report["metric"], "complete": report["complete"],
                    "missing_accounts": report["missing_accounts"], "summary": report["summary"]})
            return 0 if report["complete"] else 3
        elif args.command == "export-xlsx":
            folder = config.resolve(args.folder) if args.folder else config.report_dir / (args.day or today()) / args.metric
            _print(export_xlsx(config, folder))
        elif args.command == "collect":
            selected = set(args.account or config.handles)
            if not selected <= config.handles:
                raise MonitorError("Requested collection account is not configured.")
            expected_day = args.day or today()
            attempt = store.start_attempt()
            try:
                snapshots = []
                for account in config.accounts:
                    if account["handle"] in selected:
                        print("Collecting " + account["handle"] + " for Beijing day " + expected_day + ".", flush=True)
                        raw_dir = config.state_dir / "raw" / "attempts" / attempt / account["handle"]
                        snapshots.append(collect_external(config, account, expected_day, raw_dir))
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
            _print({"collection": "valid", "attempt": attempt, "imported_snapshots": added,
                    "accounts": sorted(selected), "day": expected_day})
            # Export follows the commit. An export failure can never roll back valid collection.
            if args.report or args.xlsx:
                report = build_report(store, config, expected_day, args.metric)
                folder = export_report(store, config, report)
                _print({"report": str(folder), "metric": args.metric, "complete": report["complete"]})
                if args.xlsx:
                    _print(export_xlsx(config, folder))
                return 0 if report["complete"] else 3
    return 0


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return run(args)
    except MonitorError as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        # External errors can contain signed URLs or headers. Never echo them.
        print("ERROR: Local configuration, storage or export failed; verify local paths, schema and runtime. No failed collection is reported as valid.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: Interrupted; incomplete source results were not committed as valid snapshots.", file=sys.stderr)
        return 130
