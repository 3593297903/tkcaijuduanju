"""Resumable browser checkpoints for classified network failures."""

from argparse import Namespace
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from .config import load_config, read_json
from .media import MAX_BYTES, _check_image, _save_png, cover_host
from .model import HANDLE_PATTERN, ID_PATTERN, MonitorError, SourceError, iso_time, local_day, now_utc, parse_time, today, valid_day
from .onboarding import TASK_PATTERN, _options, account_lock, atomic_json, file_digest, load_task
from .sources import HttpResponse, collect_checkpoint, collect_http
from .store import Store


SCHEMA = "tkmonitor.browser-fallback/v1"
CAPTURE_SCHEMA = "tkmonitor.browser-checkpoint/v1"
SUFFIX = ".browser.local.json"
MAX_PAGES = 100
MAX_PAGE_BYTES = 25 * 1024 * 1024
COVER_ERRORS = {"COVER_NETWORK_UNAVAILABLE", "COVER_SOURCE_UNAVAILABLE"}
COVER_RECOVERY_CODES = {"DNS_PUBLIC_UNAVAILABLE", "PROXY_UNAVAILABLE", "NETWORK_ERROR", "COVER_SOURCE_UNAVAILABLE"}
_IDENTITY_SOURCES = {"profile_document", "profile_response", "profile_dom_json"}


def _root(base):
    return (base.path.parent / "_local/onboarding").resolve()


def is_fallback(path, base):
    path = base.resolve(path)
    return path.parent == _root(base) and bool(TASK_PATTERN.fullmatch(path.name.removesuffix(SUFFIX))) and path.name.endswith(SUFFIX)


def _latest_receipts(store, accounts, day):
    receipts = []
    for account in accounts:
        row = store.connection.execute(
            "SELECT id,fingerprint FROM snapshots WHERE account=? AND day=? ORDER BY captured_at DESC,id DESC LIMIT 1",
            (account, day)).fetchone()
        if row:
            receipts.append({"account": account, "id": row[0], "fingerprint": row[1]})
    return receipts


def _stored_receipts_match(store, receipts, day):
    if not receipts:
        return False
    keys = [(entry["account"], entry["fingerprint"], day) for entry in receipts]
    rows = [store.connection.execute("SELECT account,fingerprint,day FROM snapshots WHERE id=?", (entry["id"],)).fetchone()
            for entry in receipts]
    return all(row is not None and tuple(row) == key for row, key in zip(rows, keys))


def _verify_receipts(store, receipts, day):
    if not receipts:
        raise MonitorError("Browser recovery has no committed snapshot receipts.")
    current = _latest_receipts(store, [entry["account"] for entry in receipts], day)
    if receipts != current:
        raise MonitorError("A different snapshot now owns the report. Restart from the current snapshot.")


def _cover_plan(config, options):
    folder = config.report_dir / options["day"] / options["metric"]
    validation = read_json(folder / "media_validation.json")
    report = read_json(folder / "merged.json")
    with Store(config.state_dir) as store:
        receipts = _latest_receipts(store, sorted(config.handles), options["day"])
        snapshot_ids = {entry["id"] for entry in receipts}
        if (not snapshot_ids or snapshot_ids != set(validation.get("snapshot_ids", ()))
                or report.get("report_day") != options["day"] or report.get("metric") != options["metric"]):
            raise MonitorError("Cover recovery requires the latest verified report snapshot evidence.")
        rows = {(item["account"], item["id"]): item for item in report["rows"]}
        wanted = {}
        for entry in validation["downloads"]:
            if entry["status"] != "no_source" and (
                    entry["status"] != "unavailable"
                    or entry.get("error_code") not in COVER_RECOVERY_CODES):
                continue
            account, drama_id = entry["account"], entry["id"]
            row = rows.get((account, drama_id))
            if account not in config.handles or not row or row["snapshot_id"] not in snapshot_ids:
                raise MonitorError("Cover recovery evidence does not match its report rows.")
            saved = store.metadata_at(account, drama_id, row["recorded_at"], include_translations=False)
            wanted.setdefault(account, []).append({"id": drama_id, "title": row["title"],
                                                   "thumbnail_url": saved.get("thumbnail_url")})
    if not wanted:
        raise MonitorError("No unavailable cover was listed for this report.")
    return wanted, receipts


def _pending(task, path):
    output = Path(task["output_dir"])
    receipt = output / "ready.local.json"
    ready = False
    if not (output / "capture.lock").exists() and receipt.is_file():
        try:
            marker = json.loads(_read_artifact(receipt, 65536))
        except (ValueError, UnicodeError) as exc:
            raise MonitorError("Browser checkpoint completion receipt is not valid JSON.") from exc
        ready = (marker == {"task_id": task["task_id"], "accounts": task["selected_accounts"]}
                 and all((_capture_dir(task, handle) / "capture.local.json").is_file()
                         for handle in task["selected_accounts"]))
    return {"status": "READY_TO_RESUME" if ready else "NEEDS_BROWSER",
            "purpose": "covers" if task["phase"] == "covers" else "full_collection",
            "manifest": str(path), "accounts": task["selected_accounts"],
            "reason": task["reason"], "day": task["options"]["day"],
            "collection": "valid" if task["phase"] == "covers" or task["state"] == "collected" else "not_completed",
            "next_action": "onboard --resume MANIFEST" if ready else "captureCheckpoint in the authorized Chrome runtime"}


def _origin(base, origin_manifest, target_config, selected):
    if origin_manifest is None:
        return None, None
    task, path = load_task(base, origin_manifest)
    if task["account_config"] != str(target_config.path) or set(task["selected_accounts"]) != set(selected):
        raise MonitorError("Browser recovery does not match the original onboarding task.")
    return task, path


def create_fallback(base, target_config, args, *, selected, reason, phase="collect", origin_manifest=None):
    if phase == "cover":
        phase = "covers"
    selected = sorted(set(selected))
    if not selected or not set(selected) <= target_config.handles:
        raise MonitorError("Browser recovery account selection is outside the original collection scope.")
    if phase not in {"collect", "covers"}:
        raise MonitorError("Unknown browser recovery phase.")
    options = _options(args, target_config)
    options["day"] = options["day"] or today()
    if phase == "collect" and options["day"] != today():
        raise MonitorError("Browser recovery must capture fresh pages for the current Beijing day.")
    original, origin_path = _origin(base, origin_manifest, target_config, selected)
    if original and original.get("browser_fallback_manifest"):
        linked = base.resolve(original["browser_fallback_manifest"])
        return _pending(_read_task(base, linked)[0], linked)
    wanted, receipts = _cover_plan(target_config, options) if phase == "covers" else ({}, [])
    ident = uuid4().hex
    root = _root(base)
    path = root / (ident + SUFFIX)
    task = {"schema": SCHEMA, "task_id": ident, "state": "needs_browser", "phase": phase,
            "reason": reason, "base_config": str(base.path), "base_fingerprint": file_digest(base.path),
            "target_config": str(target_config.path), "target_fingerprint": file_digest(target_config.path),
            "options": options, "collect_accounts": selected, "selected_accounts": sorted(wanted) if wanted else selected,
            "started_at": iso_time(now_utc()), "browser_timeout_seconds": getattr(args, "browser_timeout", 90),
            "max_pages": MAX_PAGES, "missing_covers": wanted, "snapshot_receipts": receipts,
            "origin_manifest": str(origin_path) if origin_path is not None else None,
            "output_dir": str(root / (ident + ".browser"))}
    atomic_json(path, task)
    if original is not None:
        original["browser_fallback_manifest"] = str(path)
        atomic_json(origin_path, original)
    return _pending(task, path)


def _handles(value, allowed):
    return (isinstance(value, list) and bool(value)
            and all(isinstance(handle, str) and HANDLE_PATTERN.fullmatch(handle) and handle not in {".", ".."}
                    for handle in value) and value == sorted(set(value)) and set(value) <= allowed)


def _read_task(base, path):
    path = base.resolve(path)
    if not is_fallback(path, base):
        raise MonitorError("Browser recovery manifest is outside the private onboarding directory.")
    task = read_json(path)
    ident = path.name.removesuffix(SUFFIX)
    if (not isinstance(task, dict) or task.get("schema") != SCHEMA or task.get("task_id") != ident
            or task.get("phase") not in {"collect", "covers"}
            or task.get("state") not in {"needs_browser", "prepared", "collected", "complete"}
            or task.get("base_config") != str(base.path) or task.get("base_fingerprint") != file_digest(base.path)
            or task.get("output_dir") != str(_root(base) / (ident + ".browser"))):
        raise MonitorError("Browser recovery manifest or its base configuration changed.")
    target_path = Path(task.get("target_config", ""))
    if (target_path != base.path and target_path.parent != base.path.parent
            or not target_path.is_file() or task.get("target_fingerprint") != file_digest(target_path)):
        raise MonitorError("Browser recovery target configuration changed.")
    config = load_config(target_path)
    if (not _handles(task.get("collect_accounts"), config.handles)
            or not _handles(task.get("selected_accounts"), config.handles)):
        raise MonitorError("Browser recovery account selection is invalid.")
    if (task["phase"] == "collect" and task["collect_accounts"] != task["selected_accounts"]
            or task["phase"] == "covers" and set(task.get("missing_covers", {})) != set(task["selected_accounts"])):
        raise MonitorError("Browser recovery evidence does not match its account scope.")
    options = task.get("options")
    if (not isinstance(options, dict) or options.get("metric") not in {"frontend", "display"}
            or not all(isinstance(options.get(key), bool) for key in ("report", "finalize", "xlsx", "translate"))
            or options.get("delivery") is not None and not isinstance(options["delivery"], str)):
        raise MonitorError("Browser recovery report options are invalid.")
    valid_day(options.get("day"))
    parse_time(task.get("started_at"))
    if not isinstance(task.get("browser_timeout_seconds"), (int, float)) or not 5 <= task["browser_timeout_seconds"] <= 300:
        raise MonitorError("Browser recovery timeout is outside the supported range.")
    return task, config, path


def linked_fallback(base, origin_manifest):
    original, path = load_task(base, origin_manifest)
    linked = original.get("browser_fallback_manifest")
    if not linked:
        return None
    task, config, linked_path = _read_task(base, linked)
    if task.get("origin_manifest") != str(path) or str(config.path) != original["account_config"]:
        raise MonitorError("Browser recovery manifest does not match its originating task.")
    return linked_path


def _capture_dir(task, handle):
    path = Path(task["output_dir"]) / handle
    if path.resolve() != path:
        raise MonitorError("Browser checkpoint directory is outside its private task path.")
    return path


def _read_artifact(path, maximum):
    if not path.is_file() or path.resolve() != path or path.stat().st_size > maximum:
        raise MonitorError("A required browser checkpoint file is missing, too large, or outside its task directory.")
    with path.open("rb") as stream:
        body = stream.read(maximum + 1)
    if len(body) > maximum:
        raise MonitorError("Browser checkpoint file exceeds its size limit.")
    return body


def _capture(task, handle):
    folder = _capture_dir(task, handle)
    try:
        capture = json.loads(_read_artifact(folder / "capture.local.json", 2 * 1024 * 1024))
    except (ValueError, UnicodeError) as exc:
        raise MonitorError("Browser checkpoint manifest is not valid JSON.") from exc
    identity = capture.get("identity") if isinstance(capture, dict) else None
    if (not isinstance(identity, dict) or capture.get("schema") != CAPTURE_SCHEMA
            or capture.get("task_id") != task["task_id"] or capture.get("account") != handle
            or capture.get("visible_account") != handle or identity.get("handle") != handle
            or not isinstance(identity.get("secUid"), str) or not identity["secUid"]
            or identity.get("source") not in _IDENTITY_SOURCES):
        raise MonitorError("Browser checkpoint identity does not match the requested account.")
    start, finish = parse_time(capture.get("start_at")), parse_time(capture.get("last_read_at"))
    if not parse_time(task["started_at"]) <= start <= finish <= now_utc() + timedelta(minutes=5):
        raise MonitorError("Browser checkpoint timestamps are outside the task window.")
    return folder, capture, start, finish


def _snapshot(task, handle, folder, capture, started, finished):
    if today() != task["options"]["day"] or local_day(iso_time(started)) != local_day(iso_time(finished)):
        raise MonitorError("Browser collection crossed a Beijing date boundary. Start a fresh collection.")
    pages = capture.get("pages")
    if not isinstance(pages, list) or not 1 <= len(pages) <= MAX_PAGES:
        raise MonitorError("Browser collection has no bounded set of response pages.")
    cursors, previous = [], started
    for page in pages:
        cursor = page.get("cursor") if isinstance(page, dict) else None
        if type(cursor) is not int or cursor < 0 or page.get("secUid") != capture["identity"]["secUid"]:
            raise MonitorError("Browser pagination cursor or account identity is invalid.")
        observed = parse_time(page.get("observed_at"))
        if not previous <= observed <= finished:
            raise MonitorError("Browser pagination timestamps are out of order.")
        cursors.append(cursor)
        previous = observed

    spec = folder / "replay.local.json"
    atomic_json(spec, {"url": "https://www.tiktok.com/api/drama/user/drama_list/?cursor=0",
                       "initial_cursor": "0", "retries": 0, "max_pages": len(pages)})
    entries = iter(zip(cursors, pages))

    def local_transport(url, headers, timeout):
        expected = int(parse_qs(urlsplit(url).query)["cursor"][0])
        entry = next(entries, None)
        if entry is None or entry[0] != expected:
            raise MonitorError("Browser checkpoint pagination is not continuous from cursor zero.")
        body = _read_artifact(folder / f"page-{expected}.local.json", MAX_PAGE_BYTES)
        if sha256(body).hexdigest() != entry[1].get("sha256"):
            raise MonitorError("Browser checkpoint page bytes do not match its capture manifest.")
        return HttpResponse(200, {"Content-Type": "application/json"}, body, url)

    clock = iter((started, finished)).__next__
    snapshot = collect_http(handle, spec, transport=local_transport, clock=clock)
    if next(entries, None) is not None or any(record.frontend_integer is None for record in snapshot.records):
        raise MonitorError("Browser checkpoint has extra pages or missing exact play counts.")
    canonical = folder / "snapshot.local.json"
    atomic_json(canonical, replace(snapshot, source="authorized browser response checkpoint").as_dict())
    return collect_checkpoint(canonical, handle, task["options"]["day"], parse_time(task["started_at"]))


def _cover_key(url):
    # The two US hosts also appear as platform-observed cover assets.
    hosts_us = {"p16-common-sign.tiktokcdn-us.com", "p19-common-sign.tiktokcdn-us.com"}
    try:
        parts = urlsplit(url)
        if parts.hostname in hosts_us:
            if parts.scheme != "https" or parts.port not in {None, 443} or parts.username is not None or parts.password is not None or parts.fragment:
                raise ValueError()
        else:
            cover_host(url)
        key = parts.path.rsplit("/", 1)[-1].split("~", 1)[0]
        return key.lower() if re.fullmatch(r"[a-fA-F0-9]{32,64}", key) else parts.hostname + parts.path
    except (MonitorError, ValueError, TypeError, AttributeError):
        return None


def _cover_bytes(entries, expected, folder):
    if not isinstance(entries, list):
        raise MonitorError("Browser checkpoint cover evidence must be a list.")
    seen = set()
    for entry in entries:
        drama_id = entry.get("id") if isinstance(entry, dict) else None
        fixture = expected.get(drama_id) if isinstance(drama_id, str) else None
        if (not isinstance(drama_id, str) or not ID_PATTERN.fullmatch(drama_id) or drama_id in seen
                or not fixture or entry.get("title") != fixture["title"]):
            raise MonitorError("Browser cover evidence does not match a requested drama.")
        key = _cover_key(entry.get("url"))
        expected_key = _cover_key(fixture.get("thumbnail_url"))
        source = entry.get("identity_source")
        if (not key or source not in {"card_id", "response_url"}
                or source == "card_id" and entry.get("card_id") != drama_id
                or source == "response_url" and key != (expected_key or _cover_key(entry.get("matched_url")))):
            raise MonitorError("Browser cover evidence must identify the exact observed drama asset.")
        seen.add(drama_id)
        asset = _read_artifact(folder / f"cover-{drama_id}.bin", MAX_BYTES)
        if sha256(asset).hexdigest() != entry.get("sha256"):
            raise MonitorError("Browser cover bytes do not match the capture manifest.")
        yield drama_id, folder / f"cover-{drama_id}.bin", entry["sha256"]


def _cover_destination(store, handle, drama_id):
    if not isinstance(drama_id, str) or not ID_PATTERN.fullmatch(drama_id):
        raise MonitorError("Browser cover task contains an invalid drama ID.")
    destination = store.state_dir / "assets/report_covers" / handle / (drama_id + ".png")
    if destination.resolve() != destination:
        raise MonitorError("Browser cover cache path escaped the configured state directory.")
    return destination


def _cover_cached(store, handle, drama_id):
    destination = _cover_destination(store, handle, drama_id)
    if not destination.is_file():
        return False
    try:
        _check_image(destination)
    except (OSError, ValueError):
        return False
    return True


def _save_covers(store, assets):
    for handle, drama_id, path, digest in assets:
        destination = _cover_destination(store, handle, drama_id)
        try:
            asset = _read_artifact(path, MAX_BYTES)
            if sha256(asset).hexdigest() != digest:
                raise MonitorError("Browser cover bytes changed after validation.")
            _save_png(asset, destination)
        except (OSError, ValueError) as exc:
            raise MonitorError("A captured browser cover is not a valid report image.") from exc


def _update_origin(base, task, config, *, completed=False):
    original, path = _origin(base, task.get("origin_manifest"), config, task["collect_accounts"])
    if original is None:
        return
    if task["phase"] == "collect":
        original["prepared_snapshots"] = [{"id": item["id"], "fingerprint": item["fingerprint"]}
                                          for item in task["snapshot_receipts"]]
        original["collected_day"] = task["options"]["day"]
        original["attempt"] = task["attempt"]
    if completed:
        original["state"] = "complete"
    elif original["state"] != "complete":
        original["state"] = "collected"
    atomic_json(path, original)


def _collect(task, path, store, emit):
    snapshots, assets = [], []
    for handle in task["selected_accounts"]:
        folder, capture, started, finished = _capture(task, handle)
        snapshot = _snapshot(task, handle, folder, capture, started, finished)
        snapshots.append(snapshot)
        titles = {record.id: {"title": record.title, "thumbnail_url": record.metadata.get("thumbnail_url")}
                  for record in snapshot.records}
        assets.extend((handle, *cover) for cover in _cover_bytes(capture.get("covers", []), titles, folder))
    receipts = [{"account": snapshot.account, "id": snapshot.id, "fingerprint": snapshot.digest} for snapshot in snapshots]
    if task["state"] == "prepared" and receipts != task["snapshot_receipts"]:
        raise MonitorError("Browser checkpoint changed after preparation.")
    if task["state"] != "prepared":
        task.update(state="prepared", snapshot_receipts=receipts, attempt=store.start_attempt())
        atomic_json(path, task)
    try:
        added = store.commit_batch(snapshots, set(task["collect_accounts"]))
    except (OSError, ValueError, MonitorError):
        store.finish_attempt(task["attempt"], "failed", "Browser checkpoint commit did not complete.")
        raise
    store.finish_attempt(task["attempt"], "valid", "Complete validated browser batch committed.")
    task["state"] = "collected"
    atomic_json(path, task)
    emit({"collection": "valid", "attempt": task["attempt"], "imported_snapshots": added,
          "accounts": task["collect_accounts"], "day": task["options"]["day"], "source": "browser_checkpoint"})
    _save_covers(store, assets)


def _recover_covers(task, path, store, browser_ready):
    _verify_receipts(store, task["snapshot_receipts"], task["options"]["day"])
    assets = []
    remaining = False
    for handle in task["selected_accounts"]:
        expected = {item["id"]: item for item in task["missing_covers"][handle]}
        uncovered = set(expected) - {key for key in expected if _cover_cached(store, handle, key)}
        if not uncovered:
            continue
        if not browser_ready:
            remaining = True
            continue
        folder, capture, _, _ = _capture(task, handle)
        if capture.get("pages") != []:
            raise MonitorError("Cover recovery must use the original committed snapshot.")
        verified = list(_cover_bytes(capture.get("covers"), expected, folder))
        assets.extend((handle, *cover) for cover in verified if cover[0] in uncovered)
        if uncovered - {cover[0] for cover in verified}:
            remaining = True
    _save_covers(store, assets)
    if remaining:
        return False
    task["state"] = "collected"
    atomic_json(path, task)
    return True


def resume_fallback(base, path, export, emit):
    task, config, path = _read_task(base, path)
    with account_lock(base.path.parent, "browser-" + task["task_id"]):
        task, config, path = _read_task(base, path)
        day = task["options"]["day"]
        if task.get("cover_manifest") and task["state"] != "complete":
            child, child_config, child_path = _read_task(base, task["cover_manifest"])
            if child["phase"] != "covers" or child_config.path != config.path or child_path == path:
                raise MonitorError("Browser cover continuation does not match its collection task.")
            result = resume_fallback(base, child_path, export, emit)
            if result:
                return result
            _update_origin(base, task, config, completed=True)
            task["state"] = "complete"
            atomic_json(path, task)
            return 0
        with Store(config.state_dir) as store:
            if task["state"] == "prepared" and _stored_receipts_match(store, task["snapshot_receipts"], day):
                store.finish_attempt(task["attempt"], "valid", "Recovered committed browser snapshot receipts.")
                task["state"] = "collected"
                atomic_json(path, task)
                _update_origin(base, task, config)
            if task["state"] in {"needs_browser", "prepared"}:
                if task["phase"] == "collect" and day != today():
                    raise MonitorError("Browser collection belongs to another Beijing day. Start a fresh collection.")
                pending = _pending(task, path)
                browser_ready = pending["status"] == "READY_TO_RESUME"
                if task["phase"] == "collect":
                    if not browser_ready:
                        emit(pending)
                        return 4
                    _collect(task, path, store, emit)
                elif not _recover_covers(task, path, store, browser_ready):
                    pending.update(status="NEEDS_BROWSER", next_action="captureCheckpoint in the authorized Chrome runtime")
                    emit(pending)
                    return 4
                _update_origin(base, task, config)
            if task["state"] == "complete":
                if not _stored_receipts_match(store, task["snapshot_receipts"], day):
                    raise MonitorError("The completed browser task no longer matches its committed snapshots.")
                emit({"status": "COMPLETED", "manifest": str(path), "day": day, "reused_receipt": True})
                return 0
            _verify_receipts(store, task["snapshot_receipts"], day)
            options = Namespace(**task["options"], account=task["collect_accounts"],
                                browser_timeout=task["browser_timeout_seconds"])
            try:
                result = export(config, store, options)
            except SourceError as exc:
                if exc.code not in COVER_ERRORS or task["phase"] != "collect":
                    raise
                pending = create_fallback(base, config, options, selected=task["collect_accounts"],
                                          reason=exc.code, phase="covers")
                task["cover_manifest"] = pending["manifest"]
                atomic_json(path, task)
                emit(pending)
                return 4
            if result == 0:
                _update_origin(base, task, config, completed=True)
                task["state"] = "complete"
                atomic_json(path, task)
        emit({"status": "COMPLETED" if result == 0 else "REPORT_INCOMPLETE", "manifest": str(path), "day": day})
        return result
