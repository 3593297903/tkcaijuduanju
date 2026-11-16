"""Resumable account onboarding. Browser access is explicitly supplied by Codex.

No browser library, token generator, alternate HTTP collector, or database schema
is introduced here. A captured request is preparation, never a complete snapshot.
"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, urlsplit
import uuid

from .config import load_config, read_json
from .model import (HANDLE_PATTERN, ID_PATTERN, MonitorError, SourceError, integer,
                    iso_time, now_utc, parse_time, today, valid_day)
from .sources import _api_record, _drama_offset, _page
from .store import Store

SCHEMA = "tkmonitor.onboarding/v1"
HEADERS = {"accept", "accept-language", "referer", "user-agent"}
TASK_PATTERN = re.compile(r"[a-f0-9]{32}\Z")
RUN_FLAGS = ("report", "metric", "finalize", "xlsx", "delivery", "allow_untranslated")


def file_digest(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                                prefix=".onboard-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def profile_handle(url):
    try:
        parts = urlsplit(url)
        match = re.fullmatch(r"/@([A-Za-z0-9_.-]+)/?", parts.path)
        if (parts.scheme != "https" or parts.hostname not in {"www.tiktok.com", "tiktok.com"}
                or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443) or not match
                or not HANDLE_PATTERN.fullmatch(match[1]) or match[1] in {".", ".."}):
            raise ValueError()
        return match[1]
    except (TypeError, ValueError) as exc:
        raise MonitorError("Use an HTTPS TikTok account homepage URL, without user info or a video path.") from exc


def _relative(path, parent):
    return os.path.relpath(Path(path).resolve(), Path(parent).resolve()).replace("\\", "/")


@contextmanager
def account_lock(root, handle):
    """An OS lock is released even if the process crashes; no stale PID guessing."""
    if not HANDLE_PATTERN.fullmatch(handle) or handle in {".", ".."}:
        raise MonitorError("Invalid account lock target.")
    path = Path(root) / "_local/onboarding/locks" / (handle + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise MonitorError("This account already has an active onboarding operation; retry after it finishes.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def account_plan(base, handle, name=None, *, use_default_source=False):
    """Resolve real existing sources; never mutate the default monitoring list."""
    root = base.path.parent
    if use_default_source:
        account = next((a for a in base.accounts if a["handle"] == handle), None)
        if not account or account.get("source", {}).get("type", "http") != "http" or not account.get("source", {}).get("request_spec"):
            raise MonitorError("The failed batch account has no recoverable HTTP request configuration.")
        spec = base.resolve(account["source"]["request_spec"])
        return {"handle": handle, "name": account["name"], "config_path": base.path,
                "config_data": read_json(base.path), "request_spec": spec,
                "config_fingerprint": file_digest(base.path), "spec_fingerprint": file_digest(spec)}
    own_path = root / ("config." + handle + ".local.json")
    if own_path.is_file():
        own = load_config(own_path)
        if own.handles != {handle}:
            raise MonitorError("The account-specific configuration contains a different monitoring scope.")
        account = deepcopy(own.accounts[0])
        source_config = own
        data = read_json(own_path)
    else:
        existing = next((a for a in base.accounts if a["handle"] == handle), None)
        account = deepcopy(existing) if existing else {"handle": handle, "name": name or handle,
                    "prefix": handle.replace(".", "_")}
        source_config = base
        data = {"state_dir": _relative(base.state_dir, root),
                "report_dir": _relative(base.state_dir / "account-reports" / handle, root)}
    source = account.get("source", {})
    if not isinstance(source, dict) or source.get("type", "http") != "http":
        raise MonitorError("Account onboarding requires an HTTP source; existing browser/checkpoint sources were not changed.")
    spec = source_config.resolve(source["request_spec"]) if source.get("request_spec") else root / "request-specs" / (handle + ".local.json")
    account["source"] = {**source, "type": "http", "request_spec": _relative(spec, root)}
    data["accounts"] = [account]
    return {"handle": handle, "name": account["name"], "config_path": own_path,
            "config_data": data, "request_spec": spec.resolve(),
            "config_fingerprint": file_digest(own_path), "spec_fingerprint": file_digest(spec)}


def _activate_config(plan):
    path = plan["config_path"]
    if file_digest(path) != plan["config_fingerprint"]:
        raise MonitorError("Account configuration changed during onboarding; prepare a fresh operation.")
    if not path.is_file():
        atomic_json(path, plan["config_data"])
    return load_config(path)


def _options(args, config):
    result = {key: getattr(args, key, False if key == "allow_untranslated" else None) for key in RUN_FLAGS}
    result["metric"] = result["metric"] or "frontend"
    if result["delivery"]:
        result["delivery"] = str(config.resolve(result["delivery"]))
    result["day"] = getattr(args, "day", None)
    if result["day"]:
        valid_day(result["day"])
    return result


def create_browser_task(base, plan, args, *, scope="single", selected=None):
    root = base.path.parent / "_local/onboarding"
    root.mkdir(parents=True, exist_ok=True)
    options = _options(args, base)
    pending = [] if getattr(args, "new_task", False) else sorted(root.glob("*.local.json"))
    for path in pending:
        if not TASK_PATTERN.fullmatch(path.name.removesuffix(".local.json")):
            continue
        prior = read_json(path)
        if (prior.get("schema") == SCHEMA and prior.get("state") == "needs_browser"
                and prior.get("base_config") == str(base.path) and prior.get("account") == plan["handle"]
                and prior.get("scope") == scope and prior.get("options") == options
                and prior.get("base_fingerprint") == file_digest(base.path)
                and prior.get("account_config_fingerprint") == plan["config_fingerprint"]
                and prior.get("selected_accounts") == sorted(selected or {plan["handle"]})
                and prior.get("allow_scroll_fallback") == bool(getattr(args, "allow_scroll_fallback", False))
                and prior.get("browser_timeout_seconds") == getattr(args, "browser_timeout", 90)
                and prior.get("spec_fingerprint") == plan["spec_fingerprint"]
                and not Path(prior.get("failure_path", "")).is_file()):
            return prior, path
    ident = uuid.uuid4().hex
    path = root / (ident + ".local.json")
    task = {"schema": SCHEMA, "task_id": ident, "state": "needs_browser", "scope": scope,
            "account": plan["handle"], "name": plan["name"],
            "profile_url": "https://www.tiktok.com/@" + plan["handle"],
            "base_config": str(base.path), "base_fingerprint": file_digest(base.path),
            "account_config": str(plan["config_path"]),
            "account_config_fingerprint": plan["config_fingerprint"],
            "request_spec": str(plan["request_spec"]), "spec_fingerprint": plan["spec_fingerprint"],
            "candidate_path": str(root / (ident + ".request.local.json")),
            "evidence_path": str(root / (ident + ".evidence.local.json")),
            "response_path": str(root / (ident + ".response.local.json")),
            "failure_path": str(root / (ident + ".failure.local.json")),
            "created_at": iso_time(now_utc()), "browser_rounds": 1,
            "allow_scroll_fallback": bool(getattr(args, "allow_scroll_fallback", False)),
            "browser_timeout_seconds": getattr(args, "browser_timeout", 90),
            "selected_accounts": sorted(selected or {plan["handle"]}), "options": options}
    atomic_json(path, task)
    return task, path


def pending_result(task, path):
    ready = Path(task["candidate_path"]).is_file() and Path(task["evidence_path"]).is_file()
    attempted = path.with_name(task["task_id"] + ".capture-attempt.local.json").is_file()
    failure = Path(task["failure_path"]).is_file() or attempted
    return {"status": "READY_TO_RESUME" if ready else "NEEDS_BROWSER_ACTION" if failure else "NEEDS_BROWSER",
            "account": task["account"], "manifest": str(path), "collection": "not_completed",
            "next_action": "onboard --resume MANIFEST" if ready else "wait_for_active_capture_or_explicitly_start_with_--new-task" if failure else "captureOnboarding in the authorized Chrome runtime",
            "browser_rounds": task["browser_rounds"], "allow_scroll_fallback": task["allow_scroll_fallback"]}


def load_task(base, path):
    path = base.resolve(path)
    root = (base.path.parent / "_local/onboarding").resolve()
    task = read_json(path)
    ident = task.get("task_id") if isinstance(task, dict) else None
    if (not isinstance(ident, str) or not TASK_PATTERN.fullmatch(ident)
            or path != root / (ident + ".local.json") or task.get("schema") != SCHEMA
            or task.get("base_config") != str(base.path)
            or task.get("base_fingerprint") != file_digest(base.path)
            or not isinstance(task.get("account"), str)
            or profile_handle(task.get("profile_url")) != task["account"]
            or task.get("browser_rounds") != 1 or task.get("scope") not in {"single", "batch"}
            or task.get("state") not in {"needs_browser", "activating", "activated", "collecting", "prepared", "collected", "complete"}):
        raise MonitorError("Onboarding manifest or its base configuration changed; prepare a fresh operation.")
    for key, suffix in (("candidate_path", ".request.local.json"), ("evidence_path", ".evidence.local.json"),
                        ("response_path", ".response.local.json"), ("failure_path", ".failure.local.json")):
        if task.get(key) != str(root / (ident + suffix)):
            raise MonitorError("Onboarding output escaped its private task directory.")
    options = task.get("options")
    if (not isinstance(options, dict) or options.get("metric") not in {"frontend", "display"}
            or any(not isinstance(options.get(k), bool) for k in ("report", "finalize", "xlsx"))
            or options.get("delivery") is not None and not isinstance(options["delivery"], str)
            or not isinstance(task.get("selected_accounts"), list)
            or set(task["selected_accounts"]) != ({task["account"]} if task["scope"] == "single" else set(task["selected_accounts"]))):
        raise MonitorError("Onboarding execution options are invalid.")
    if options.get("day"):
        valid_day(options["day"])
    options.setdefault("allow_untranslated", False)
    if task["scope"] == "batch" and (not set(task["selected_accounts"]) <= base.handles or task["account"] not in task["selected_accounts"]):
        raise MonitorError("Onboarding recovery batch is outside the configured accounts.")
    return task, path


def validate_candidate(task):
    """Recheck browser evidence and exact bytes before activating a private request."""
    candidate = Path(task["candidate_path"])
    evidence = read_json(task["evidence_path"])
    spec = read_json(candidate)
    response_path = Path(task["response_path"])
    if response_path.stat().st_size > 25 * 1024 * 1024:
        raise MonitorError("Captured response exceeds the size limit.")
    response = read_json(response_path)
    try:
        parts = urlsplit(spec["url"])
        query = parse_qs(parts.query, keep_blank_values=True)
        captured = parse_time(evidence["captured_at"])
        identity = evidence["identity"]
        valid = (spec.get("initial_cursor") == "0" and spec.get("cursor_param") == "cursor"
                 and spec.get("method", "GET") == "GET" and parts.scheme == "https"
                 and parts.netloc == "www.tiktok.com" and parts.path == "/api/drama/user/drama_list/"
                 and not parts.fragment and len(query.get("secUid", [])) == 1 and bool(query["secUid"][0])
                 and len(query.get("cursor", [])) == 1 and query["cursor"][0].isdigit()
                 and evidence.get("schema") == "tkmonitor.browser-capture/v1"
                 and evidence.get("task_id") == task["task_id"]
                 and evidence.get("account") == task["account"]
                 and evidence.get("visible_account") == task["account"]
                 and identity.get("handle") == task["account"]
                 and identity.get("secUid") == query["secUid"][0]
                 and identity.get("source") in {"profile_document", "profile_response", "profile_dom_json"}
                 and evidence.get("http_status") == 200 and evidence.get("request_method") == "GET"
                 and evidence.get("loading_finished") is True
                 and evidence.get("capture_source") in {"first_page", "recorded_request", "scroll_fallback"}
                 and isinstance(evidence.get("request_id"), str) and bool(evidence["request_id"])
                 and evidence.get("request_spec_sha256") == file_digest(candidate)
                 and evidence.get("response_sha256") == file_digest(response_path)
                 and parse_time(task["created_at"]) <= captured <= now_utc() + timedelta(minutes=5)
                 and isinstance(spec.get("headers"), dict)
                 and all(isinstance(k, str) and k.lower() in HEADERS and isinstance(v, str)
                         and "\r" not in v and "\n" not in v for k, v in spec["headers"].items()))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise MonitorError("Captured request identity or response evidence is invalid.") from exc
    if not valid:
        raise MonitorError("Captured request identity or response evidence is invalid.")
    items, _, following = _page(response)
    _drama_offset(query["cursor"][0], following, len(items))
    seen = set()
    full = 0
    for item in items:
        if isinstance(item, dict) and set(item) == {"dramaID"}:
            if not isinstance(item["dramaID"], str) or not ID_PATTERN.fullmatch(item["dramaID"]):
                raise MonitorError("Captured drama reference is invalid.")
            continue
        record = _api_record(item)
        if (not isinstance(record.id, str) or not ID_PATTERN.fullmatch(record.id) or record.id in seen
                or not isinstance(record.title, str) or not record.title.strip() or record.frontend_integer is None):
            raise MonitorError("Captured drama response has invalid IDs, titles or precise counts.")
        integer(record.frontend_integer)
        seen.add(record.id)
        full += 1
    if not full:
        raise MonitorError("Captured request has no complete drama records; it cannot activate onboarding.")
    if (type(evidence.get("scroll_count")) is not int or evidence["scroll_count"] not in (0, 1)
            or type(evidence.get("user_page_refreshes")) is not int or evidence["user_page_refreshes"] != 0
            or type(evidence.get("temporary_tabs")) is not int or not 0 <= evidence["temporary_tabs"] <= 2
            or type(evidence.get("duration_ms")) not in (int, float) or not math.isfinite(evidence["duration_ms"])
            or not 0 <= evidence["duration_ms"] <= 600000
            or evidence.get("scroll_count") == 1 and not task["allow_scroll_fallback"]):
        raise MonitorError("Captured browser actions exceed this task's allowed scope.")
    return spec, evidence


def activate_candidate(task, path, plan):
    spec, evidence = validate_candidate(task)
    target = Path(task["request_spec"])
    expected_config = task.get("activated_config_sha256", task["account_config_fingerprint"])
    if (target != plan["request_spec"] or str(plan["config_path"]) != task["account_config"]
            or plan["config_fingerprint"] not in {task["account_config_fingerprint"], expected_config}):
        raise MonitorError("Account paths changed while the browser was preparing its request.")
    current_hash = file_digest(target)
    if current_hash != task["spec_fingerprint"]:
        # A crash may occur between the per-file atomic replacement and journaling.
        if current_hash != task.get("activation_sha256"):
            raise MonitorError("The request configuration changed during onboarding; refusing to overwrite it.")
    previous_path = path.with_name(task["task_id"] + ".previous-request.local.json")
    if target.is_file() and current_hash == task["spec_fingerprint"]:
        old = read_json(target)
        atomic_json(previous_path, old)
    elif previous_path.is_file():
        old = read_json(previous_path)
    else:
        old = {}
    if old:
        for key in ("max_pages", "timeout_seconds", "retries", "max_backoff_seconds", "fields", "record_fields"):
            if key in old:
                spec[key] = old[key]
    spec.update(account=task["account"], captured_at=evidence["captured_at"],
                capture_source=evidence["capture_source"], identity=evidence["identity"])
    encoded = json.dumps(spec, ensure_ascii=False, indent=2) + "\n"
    task["activation_sha256"] = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    if task["account_config_fingerprint"] is None:
        encoded_config = json.dumps(plan["config_data"], ensure_ascii=False, indent=2) + "\n"
        task["activated_config_sha256"] = hashlib.sha256(encoded_config.encode("utf-8")).hexdigest()
    task["state"] = "activating"
    atomic_json(path, task)
    # atomic_json uses the same encoding; a restart can identify this exact write.
    atomic_json(target, spec)
    config = _activate_config(plan)
    task.update(state="activated", capture_summary={key: evidence.get(key) for key in
                ("capture_source", "scroll_count", "user_page_refreshes", "temporary_tabs", "duration_ms")})
    atomic_json(path, task)
    return config


def _save_progress(task, path):
    def progress(stage, details):
        task.update(details)
        task["state"] = stage
        atomic_json(path, task)
    return progress


def _committed(task, store):
    prepared = task.get("prepared_snapshots", [])
    if not prepared:
        return False
    if (not isinstance(prepared, list) or len(prepared) != len(task["selected_accounts"])
            or any(not isinstance(s, dict) or not isinstance(s.get("id"), str)
                   or not isinstance(s.get("fingerprint"), str) or not re.fullmatch(r"[a-f0-9]{64}", s["fingerprint"]) for s in prepared)):
        raise MonitorError("Onboarding checkpoint contains invalid snapshot evidence.")
    found = [store.connection.execute("SELECT fingerprint,account,day FROM snapshots WHERE id=?", (s["id"],)).fetchone() for s in prepared]
    complete = all(row is not None and row[0] == s["fingerprint"] and row[2] == task.get("collected_day") for row, s in zip(found, prepared))
    if any(row is not None for row in found) and (not complete or {row[1] for row in found if row} != set(task["selected_accounts"])):
        raise MonitorError("Onboarding checkpoint conflicts with committed snapshot evidence.")
    return complete


def run_onboard(base, args, collect, export, emit):
    from argparse import Namespace
    if args.resume:
        task, path = load_task(base, args.resume)
        handle = task["account"]
    else:
        handle = profile_handle(args.url)
        plan = account_plan(base, handle, args.name)
        if args.dry_run:
            emit({"status": "DRY_RUN", "account": handle, "request_spec_available": plan["request_spec"].is_file(),
                  "next_action": "reuse_http" if plan["request_spec"].is_file() else "needs_browser",
                  "account_config": str(plan["config_path"]), "default_scope_unchanged": True})
            return 0
        task, path = None, None
    with account_lock(base.path.parent, handle):
        if task is not None:
            plan = account_plan(base, handle, task["name"], use_default_source=task["scope"] == "batch")
            if str(plan["config_path"]) != task["account_config"] or str(plan["request_spec"]) != task["request_spec"]:
                raise MonitorError("Onboarding account paths no longer match the saved task.")
            if task["state"] == "complete":
                config = load_config(task["account_config"])
                if not (config.state_dir / "monitor.sqlite3").is_file():
                    raise MonitorError("The completed onboarding receipt has no committed database.")
                with Store(config.state_dir) as store:
                    if not _committed(task, store):
                        raise MonitorError("The completed onboarding receipt has no matching snapshots.")
                emit({"status": "COMPLETED", "account": handle, "manifest": str(path), "reused_receipt": True,
                      "day": task.get("collected_day"), "account_config": task["account_config"]})
                return 0
            if task["state"] in {"needs_browser", "activating"}:
                if not Path(task["candidate_path"]).is_file() or not Path(task["evidence_path"]).is_file():
                    emit(pending_result(task, path))
                    return 4
                config = activate_candidate(task, path, plan)
            else:
                config = load_config(task["account_config"]) if task["scope"] == "single" else base
            if task["scope"] == "batch":
                config = base
            options = Namespace(**task["options"], account=task["selected_accounts"])
            with Store(config.state_dir) as store:
                if _committed(task, store):
                    attempt = store.connection.execute("SELECT status FROM attempts WHERE id=?", (task.get("attempt"),)).fetchone()
                    if attempt and attempt[0] == "running":
                        store.finish_attempt(task["attempt"], "valid", "Recovered an already committed complete batch from snapshot fingerprints.")
                    options.day = task["collected_day"]
                    result = export(config, store, options)
                else:
                    if task.get("activation_sha256") != file_digest(plan["request_spec"]):
                        raise MonitorError("The activated request changed before collection; prepare a fresh operation.")
                    # No historical response is used; resume always requests fresh pages.
                    if options.day and options.day != today():
                        emit({"status": "STATISTICS_DAY_CHANGED", "previous_day": options.day, "day": today()})
                        options.day = today()
                    try:
                        result = collect(config, store, options, progress=_save_progress(task, path))
                    except MonitorError as exc:
                        task["last_error_code"] = getattr(exc, "code", "COLLECTION_OR_EXPORT_ERROR")
                        atomic_json(path, task)
                        raise
            if result == 0:
                task["state"] = "complete"
                atomic_json(path, task)
            emit({"status": "COMPLETED" if result == 0 else "REPORT_INCOMPLETE", "account": handle,
                  "manifest": str(path), "account_config": task["account_config"],
                  "browser_rounds": 1, "capture": task.get("capture_summary", {}), "day": task.get("collected_day")})
            return result
        plan = account_plan(base, handle, args.name)
        if not plan["request_spec"].is_file():
            task, path = create_browser_task(base, plan, args)
            emit(pending_result(task, path))
            return 4
        config = _activate_config(plan)
        options = Namespace(**_options(args, base), account=[handle])
        try:
            with Store(config.state_dir) as store:
                result = collect(config, store, options)
        except SourceError as exc:
            if exc.code != "AUTH_REQUIRED":
                raise
            task, path = create_browser_task(base, account_plan(base, handle, args.name), args)
            emit(pending_result(task, path))
            return 4
        emit({"status": "COMPLETED" if result == 0 else "REPORT_INCOMPLETE", "account": handle,
              "account_config": str(config.path), "spec_reused": True, "browser_required": False})
        return result
