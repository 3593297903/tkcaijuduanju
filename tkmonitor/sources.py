"""HTTP and browser/checkpoint adapters with one complete-snapshot contract.

Transport injection keeps pagination, retries and authentication tests fully offline.
No request URL, query string, headers or exception text is emitted to public logs.
"""

from dataclasses import dataclass
from datetime import timedelta
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, unquote_plus, urlsplit, urlunsplit, urljoin
from urllib.request import Request, HTTPRedirectHandler, build_opener

from .config import read_json
from .model import (ID_PATTERN, MonitorError, Record, Snapshot, iso_time, now_utc, parse_display,
                    parse_time, validate_snapshot)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict
    body: bytes
    url: str


def validate_url(url):
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        valid = (parts.scheme == "https" and (host == "tiktok.com" or host.endswith(".tiktok.com"))
                 and parts.username is None and parts.password is None and parts.port in (None, 443)
                 and not parts.fragment)
    except (ValueError, TypeError) as exc:
        raise MonitorError("Request URL must be HTTPS on tiktok.com or its subdomains.") from exc
    if not valid:
        raise MonitorError("Request URL must be HTTPS on tiktok.com or its subdomains, without user info or fragments.")
    return host


def _public_host(host):
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise MonitorError("TikTok DNS lookup failed; check network access.") from exc
    if not addresses or any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
        raise MonitorError("TikTok request resolved to a non-public network address; request blocked.")


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        target = urljoin(request.full_url, newurl)
        if validate_url(target) != validate_url(request.full_url):
            raise MonitorError("HTTP redirect changed hostname; refresh the private request specification.")
        _public_host(validate_url(target))
        return super().redirect_request(request, fp, code, message, headers, target)


def urllib_transport(url, headers, timeout):
    _public_host(validate_url(url))
    request = Request(url, headers=headers, method="GET")
    try:
        response = build_opener(_SafeRedirect()).open(request, timeout=timeout)
    except HTTPError as exc:
        response = exc
    with response:
        body = response.read(25 * 1024 * 1024 + 1)
        if len(body) > 25 * 1024 * 1024:
            raise MonitorError("HTTP response exceeds the safe size limit; inspect the request specification.")
        return HttpResponse(response.status, dict(response.headers), body, response.geturl())


def _field(value, dotted):
    for key in dotted.split("."):
        if not isinstance(value, dict) or key not in value:
            raise MonitorError("Response schema changed; update the local request specification field mapping.")
        value = value[key]
    return value


def _precise(value):
    if value is None:
        return None
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MonitorError("Frontend play count is not a nonnegative integer.")
    return value


def _first(item, names):
    for key in names:
        if key in item:
            return item[key]
    return None


def _thumbnail_url(item, card=None):
    zoom = item.get("zoomCover")
    cover = item.get("cover")
    candidates = [zoom.get("240") if isinstance(zoom, dict) else None,
                  cover.get("240") if isinstance(cover, dict) else None,
                  card.get("thumbnail_url") if card else None, item.get("thumbnail_url")]
    if isinstance(cover, dict) and isinstance(cover.get("urlList"), list):
        candidates.extend(cover["urlList"])
    elif isinstance(cover, str):
        candidates.append(cover)
    return next((url for url in candidates if isinstance(url, str) and url), None)


def _api_record(item, mapping=None, card=None):
    if not isinstance(item, dict):
        raise MonitorError("A drama response item is not an object.")
    mapping = mapping or {}
    def get(label, aliases):
        return _field(item, mapping[label]) if label in mapping else _first(item, aliases)
    drama_id = get("id", ("dramaID", "id"))
    title = get("title", ("dramaName", "title"))
    frontend = _precise(get("frontend_integer", ("frontend_integer", "frontendInteger", "numWatched")))
    display = get("display_views", ("display_views", "views"))
    raw = get("views_raw", ("views_raw", "viewsRaw"))
    if card:
        # Actual visible card text is the authority for the display series.
        raw = card.get("views_raw") or card.get("viewsRaw") or card.get("display") or raw
        if raw is None:
            lines = [line.strip() for line in card.get("text", "").splitlines() if line.strip()]
            if len(lines) >= 2 and re.fullmatch(r"[0-9,.]+[KMBkmb]?", lines[-2]):
                raw = lines[-2]
        display = parse_display(raw) if raw is not None else display
    elif raw is not None and display is None:
        display = parse_display(raw)
    metadata = {key: item[key] for key in ("title_zh", "description_zh", "genre", "video_id", "video_url", "create_time", "is_preview") if key in item}
    metadata["source_title"] = title
    if item.get("description") is not None:
        metadata["description_en"] = item["description"]
    if item.get("numVideos") is not None:
        metadata["episodes"] = item["numVideos"]
    if item.get("themes") is not None:
        metadata["platform_themes"] = [theme.get("tagVal") for theme in item["themes"] if isinstance(theme, dict)]
    if isinstance(item.get("metadata"), dict):
        metadata.update(item["metadata"])
    thumbnail = _thumbnail_url(item, card)
    if thumbnail:
        metadata["thumbnail_url"] = thumbnail
    return Record(drama_id, title, display, frontend, raw, metadata)


def _page(value, fields=None):
    fields = fields or {}
    if not isinstance(value, dict):
        raise MonitorError("Expected a JSON response object.")
    statuses = [value[key] for key in ("status_code", "statusCode") if key in value]
    if "status" in fields:
        statuses.append(_field(value, fields["status"]))
    if any(status not in (0, "0") or isinstance(status, bool) for status in statuses):
        raise MonitorError("TikTok returned an API error; refresh the authorized browser session and private request specification.")
    items = _field(value, fields.get("items", "dramaList"))
    has_more = _field(value, fields.get("has_more", "hasMore"))
    if not isinstance(items, list) or not isinstance(has_more, bool):
        raise MonitorError("Response needs an items array and a boolean hasMore flag.")
    cursor = _field(value, fields.get("cursor", "cursor"))
    if not isinstance(cursor, (str, int)) or isinstance(cursor, bool):
        raise MonitorError("Pagination cursor must be a string or integer.")
    _numeric_cursor(str(cursor))
    return items, has_more, str(cursor)


def _numeric_cursor(value):
    """Signed integer strings remain numeric; genuine opaque tokens stay opaque."""
    if not re.fullmatch(r"[+-]?[0-9]+", value.strip()):
        return None
    number = int(value)
    if number < 0:
        raise MonitorError("Pagination cursor cannot be a negative number; no partial snapshot was committed.")
    return number


def _advance(cursor, following, seen):
    current_number, following_number = _numeric_cursor(cursor), _numeric_cursor(following)
    if following in seen or following == cursor:
        raise MonitorError("Pagination cursor did not advance; no partial snapshot was committed.")
    if current_number is not None and following_number is not None and following_number <= current_number:
        raise MonitorError("Pagination cursor moved backward; no partial snapshot was committed.")


def _drama_offset(cursor, following, item_count):
    """TikTok user/drama_list cursors are offsets, not arbitrary forward tokens."""
    current_offset, following_offset = _numeric_cursor(cursor), _numeric_cursor(following)
    if current_offset is None or following_offset is None or following_offset != current_offset + item_count:
        raise MonitorError("TikTok drama_list pagination offset does not match its returned record count; no whole-account snapshot was committed.")


def _retry_delay(headers, attempt, maximum):
    value = next((value for key, value in headers.items() if key.lower() == "retry-after"), None)
    try:
        delay = float(value)
    except (TypeError, ValueError):
        delay = 2 ** attempt
    return min(maximum, max(0, delay))


def _cursor_url(parts, parameter, cursor):
    """Change only the cursor; retain opaque signed query bytes exactly."""
    segments, found = [], False
    for segment in parts.query.split("&") if parts.query else []:
        key, separator, value = segment.partition("=")
        if unquote_plus(key) == parameter:
            if found:
                raise MonitorError("Private request URL contains more than one cursor parameter.")
            found = True
            segments.append(segment if unquote_plus(value) == cursor else key + "=" + quote(cursor, safe=""))
        else:
            segments.append(segment)
    if not found:
        segments.append(quote(parameter, safe="") + "=" + quote(cursor, safe=""))
    return urlunsplit(parts._replace(query="&".join(segments)))


def collect_http(account, spec_path, transport=None, raw_dir=None, sleep=time.sleep, clock=now_utc):
    """Read a private GET specification and collect all pages, or raise MonitorError.

    transport(url, headers, timeout) -> HttpResponse. URLs and headers are private.
    Fields defaults match TikTok's dramaList/hasMore/cursor/status_code response.
    """
    spec = read_json(spec_path)
    if not isinstance(spec, dict):
        raise MonitorError("HTTP request specification must be a JSON object.")
    url, headers = spec.get("url"), spec.get("headers", {})
    host = validate_url(url)
    if spec.get("method", "GET").upper() != "GET":
        raise MonitorError("Only ordinary authorized GET request specifications are supported.")
    if not isinstance(headers, dict) or any(not isinstance(k, str) or not isinstance(v, str) or "\r" in k + v or "\n" in k + v for k, v in headers.items()):
        raise MonitorError("Private request headers must be plain string pairs.")
    if any(key.lower() in ("host", "proxy-authorization") for key in headers):
        raise MonitorError("Host and proxy authorization overrides are not permitted.")
    try:
        maximum = int(spec.get("max_pages", 100))
        retries = int(spec.get("retries", 2))
        timeout = float(spec.get("timeout_seconds", 30))
        backoff = float(spec.get("max_backoff_seconds", 30))
        if not 1 <= maximum <= 10000 or not 0 <= retries <= 5 or not 0 < timeout <= 300 or not 0 <= backoff <= 60:
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise MonitorError("Invalid HTTP limits: max_pages 1..10000, retries 0..5, timeout 0..300, backoff 0..60.") from exc
    cursor_param = spec.get("cursor_param", "cursor")
    if not isinstance(cursor_param, str) or not cursor_param:
        raise MonitorError("cursor_param must be a nonempty query parameter name.")
    parts = urlsplit(url)
    drama_list = parts.path.rstrip("/") == "/api/drama/user/drama_list"
    if drama_list and cursor_param != "cursor":
        raise MonitorError("TikTok drama_list requires its actual cursor query parameter for whole-account collection.")
    query = parse_qsl(parts.query, keep_blank_values=True)
    cursor = str(spec.get("initial_cursor", next((v for k, v in query if k == cursor_param), "0")))
    initial_offset = _numeric_cursor(cursor)
    if drama_list and initial_offset != 0:
        raise MonitorError("TikTok drama_list collection must start at numeric cursor zero to cover the whole account.")
    records, identifiers, seen, references = [], set(), {cursor}, set()
    started = clock()
    transport = transport or urllib_transport
    for page_number in range(1, maximum + 1):
        request_url = _cursor_url(parts, cursor_param, cursor)
        for attempt in range(retries + 1):
            try:
                response = transport(request_url, headers, timeout)
            except (TimeoutError, socket.timeout, URLError, ConnectionError, OSError) as exc:
                if attempt == retries:
                    raise MonitorError("HTTP request timed out or failed after bounded retries; check network access and retry collection.") from exc
                sleep(min(backoff, 2 ** attempt))
                continue
            if validate_url(response.url) != host:
                raise MonitorError("HTTP response changed hostname; no snapshot was committed.")
            if raw_dir:
                target = Path(raw_dir) / f"page-{page_number:04d}-attempt-{attempt}.body"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(response.body)
            if response.status in (401, 403):
                raise MonitorError("TikTok authentication failed (401/403); refresh the authorized browser session and private request specification.")
            if response.status == 429 or 500 <= response.status <= 599:
                if attempt == retries:
                    raise MonitorError("TikTok rate limit or server failure persisted after bounded retries; try again later.")
                sleep(_retry_delay(response.headers, attempt, backoff))
                continue
            if not 200 <= response.status <= 299:
                raise MonitorError("TikTok returned an unsuccessful HTTP status; inspect the private request specification.")
            break
        content_type = next((value for key, value in response.headers.items() if key.lower() == "content-type"), "")
        if "application/json" not in content_type.lower() and "+json" not in content_type.lower():
            raise MonitorError("TikTok returned non-JSON content, possibly a login or verification page; open the authorized browser session.")
        try:
            value = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise MonitorError("TikTok response is not valid JSON; check login or verification in the browser.") from exc
        items, has_more, following = _page(value, spec.get("fields"))
        if drama_list:
            _drama_offset(cursor, following, len(items))
        for item in items:
            # drama_list can include an ID-only reference alongside its complete
            # item. It occupies a raw pagination slot, but contains no play count.
            # Resolve it within this full run; never turn it into a blank record
            # or silently drop an ID whose complete data was not returned.
            if drama_list and isinstance(item, dict) and set(item) == {"dramaID"}:
                reference = item["dramaID"]
                if not isinstance(reference, str) or not ID_PATTERN.fullmatch(reference):
                    raise MonitorError("ID-only drama references must contain valid 19-digit strings.")
                references.add(reference)
                continue
            record = _api_record(item, spec.get("record_fields"))
            if record.id in identifiers:
                raise MonitorError("Duplicate drama ID across pages; no partial snapshot was committed.")
            identifiers.add(record.id)
            records.append(record)
        if not has_more:
            if references - identifiers:
                raise MonitorError("Unresolved ID-only drama reference; no incomplete snapshot was committed.")
            captured = iso_time(clock())
            return validate_snapshot(Snapshot(account + ":" + captured, account, captured,
                "authorized TikTok HTTP response", tuple(records), iso_time(started)))
        if not items:
            raise MonitorError("Pagination returned an empty intermediate page; collection is incomplete.")
        _advance(cursor, following, seen)
        seen.add(following)
        cursor = following
    raise MonitorError("Maximum pagination pages reached before hasMore=false; increase the local bound only after checking the response.")


def collect_checkpoint(path, account, expected_day, not_before=None):
    value = read_json(path)
    if not isinstance(value, dict):
        raise MonitorError("Checkpoint must be a JSON object.")
    if value.get("schema") == "tkmonitor.snapshot/v1":
        try:
            captured = value["captured_at"]
            snapshot = Snapshot(id=value.get("id", account + ":" + captured), account=value["account"],
                captured_at=captured, source=value.get("source", "browser checkpoint"),
                collection_started_at=value.get("collection_started_at"), complete=value.get("complete"),
                has_more=value.get("has_more"), records=tuple(Record(**row) for row in value["records"]))
        except (KeyError, TypeError) as exc:
            raise MonitorError("Canonical checkpoint is missing required snapshot/record fields.") from exc
    else:
        run = value.get("run", {})
        pages, cards = run.get("pages", []), value.get("cards", [])
        if run.get("account") != account or not isinstance(pages, list) or not pages:
            raise MonitorError("Browser checkpoint has no complete response pages for the requested account.")
        if not isinstance(cards, list):
            raise MonitorError("Browser checkpoint cards must be an array.")
        card_map = {card.get("id"): card for card in cards}
        if len(card_map) != len(cards):
            raise MonitorError("Browser checkpoint contains duplicate visible card IDs.")
        cursor, seen, records = "0", {"0"}, []
        previous_observed = parse_time(run.get("start_at"))
        for index, page in enumerate(pages):
            observed = parse_time(page.get("observed_at"))
            if observed < previous_observed:
                raise MonitorError("Browser page timestamps are out of order.")
            previous_observed = observed
            items, more, following = _page(page.get("response"))
            _drama_offset(cursor, following, len(items))
            if more != (index < len(pages) - 1):
                raise MonitorError("Browser checkpoint is incomplete or has pages after a terminal response.")
            records.extend(_api_record(item, card=card_map.get(item.get("dramaID"))) for item in items)
            if more:
                _advance(cursor, following, seen)
                seen.add(following)
                cursor = following
        if card_map and set(card_map) != {record.id for record in records}:
            raise MonitorError("Visible browser cards do not match the complete response ID set.")
        captured = run.get("last_read_at") or pages[-1]["observed_at"]
        if parse_time(captured) < previous_observed:
            raise MonitorError("Browser completion time precedes its last response page.")
        snapshot = Snapshot(account + ":" + captured, account, captured, "authorized browser response checkpoint",
            tuple(records), run.get("start_at"))
    if snapshot.account != account:
        raise MonitorError("Checkpoint account does not match the requested account.")
    return validate_snapshot(snapshot, {account}, expected_day, not_before)


def collect_external(config, account, expected_day, raw_dir):
    source = account.get("source", {})
    if not isinstance(source, dict):
        raise MonitorError("Account source must be an object.")
    handle = account["handle"]
    kind = source.get("type", "http")
    if kind == "http":
        if not source.get("request_spec"):
            raise MonitorError("HTTP source needs a private request_spec file; capture an authorized request first.")
        snapshot = collect_http(handle, config.resolve(source["request_spec"]), raw_dir=raw_dir)
        return validate_snapshot(snapshot, {handle}, expected_day)
    if kind not in ("checkpoint", "browser"):
        raise MonitorError("Source type must be http, checkpoint or browser.")
    path = config.resolve(source["path"]) if source.get("path") else config.state_dir / "checkpoints" / (handle + ".json")
    command = source.get("command")
    not_before = None
    if command:
        if not isinstance(command, list) or any(not isinstance(item, str) for item in command):
            raise MonitorError("Browser command must be an argv array, never a shell command string.")
        not_before = now_utc()
        path.parent.mkdir(parents=True, exist_ok=True)
        raw_dir = Path(raw_dir)
        raw_dir.mkdir(parents=True, exist_ok=True)
        substitutions = {"account": handle, "output": str(path), "day": expected_day}
        try:
            argv = [item.format_map(substitutions) for item in command]
            timeout = float(source.get("timeout_seconds", 1800))
            if not 0 < timeout <= 7200:
                raise ValueError()
            with (raw_dir / "command.stdout").open("wb") as out, (raw_dir / "command.stderr").open("wb") as err:
                result = subprocess.run(argv, cwd=config.path.parent, stdout=out, stderr=err, timeout=timeout, check=False)
        except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
            raise MonitorError("Browser command could not finish; check its argv, runtime and local command logs.") from exc
        if result.returncode:
            raise MonitorError("Browser command failed; inspect private command.stdout/command.stderr and reconnect the authorized browser.")
    elif kind == "browser":
        raise MonitorError("Browser source needs a command that writes a fresh checkpoint; configure the browser helper first.")
    return collect_checkpoint(path, handle, expected_day, not_before)
