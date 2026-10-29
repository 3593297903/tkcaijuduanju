"""Snapshot contracts and validation shared by every source adapter."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")
UTC = timezone.utc
ID_PATTERN = re.compile(r"[0-9]{19}\Z")
HANDLE_PATTERN = re.compile(r"[A-Za-z0-9_.-]+\Z")


class MonitorError(Exception):
    """An actionable, safe-to-print error; never include private request data."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def parse_time(value):
    if not isinstance(value, str):
        raise MonitorError("A snapshot timestamp must be an ISO timestamp with a timezone.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MonitorError("Invalid snapshot timestamp; use ISO 8601 with a timezone.") from exc
    if result.tzinfo is None:
        raise MonitorError("Snapshot timestamps must include a timezone.")
    return result.astimezone(UTC)


def iso_time(value):
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def now_utc():
    return datetime.now(UTC)


def local_day(value):
    return parse_time(value).astimezone(SHANGHAI).date().isoformat()


def today():
    return now_utc().astimezone(SHANGHAI).date().isoformat()


def beijing(value):
    return parse_time(value).astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M:%S") if value else None


def valid_day(value):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise MonitorError("Use a calendar day in YYYY-MM-DD format.") from exc
    return value


def integer(value, label="play count"):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 9223372036854775807:
        raise MonitorError(f"Invalid {label}: expected a nonnegative SQLite integer or null.")
    return value


def parse_display(value):
    """Convert the saved visible abbreviation, never a precise-count fallback."""
    if value is None or value == "":
        return None
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([KMB]?)", str(value).strip().replace(",", ""), re.I)
    if not match:
        raise MonitorError("Unrecognized visible play count; save the displayed number explicitly.")
    try:
        number = Decimal(match[1]) * {"": 1, "K": 1000, "M": 1000000, "B": 1000000000}[match[2].upper()]
    except InvalidOperation as exc:
        raise MonitorError("Invalid visible play count.") from exc
    if number != number.to_integral_value():
        raise MonitorError("Visible play count does not resolve to an integer.")
    return int(number)


@dataclass(frozen=True)
class Record:
    id: str
    title: str
    display_views: int | None
    frontend_integer: int | None = None
    views_raw: str | None = None
    metadata: dict = field(default_factory=dict)

    def as_dict(self):
        return {"id": self.id, "title": self.title, "display_views": self.display_views,
                "frontend_integer": self.frontend_integer, "views_raw": self.views_raw,
                "metadata": self.metadata}


@dataclass(frozen=True)
class Snapshot:
    id: str
    account: str
    captured_at: str
    source: str
    records: tuple[Record, ...]
    collection_started_at: str | None = None
    complete: bool = True
    has_more: bool = False
    original_fingerprint: str | None = None

    def as_dict(self):
        return {"schema": "tkmonitor.snapshot/v1", "id": self.id, "account": self.account,
                "captured_at": self.captured_at, "collection_started_at": self.collection_started_at,
                "source": self.source, "complete": self.complete, "has_more": self.has_more,
                "records": [record.as_dict() for record in self.records]}

    @property
    def digest(self):
        return self.original_fingerprint or fingerprint(self.as_dict())


def validate_snapshot(snapshot, allowed_accounts=None, expected_day=None, not_before=None):
    if not isinstance(snapshot.account, str) or not HANDLE_PATTERN.fullmatch(snapshot.account):
        raise MonitorError("Snapshot account handle is invalid.")
    if allowed_accounts is not None and snapshot.account not in allowed_accounts:
        raise MonitorError("Snapshot belongs to an account outside the configuration.")
    if not isinstance(snapshot.id, str) or not snapshot.id:
        raise MonitorError("Snapshot ID must be a nonempty string.")
    captured = parse_time(snapshot.captured_at)
    if snapshot.collection_started_at:
        started = parse_time(snapshot.collection_started_at)
        if started > captured:
            raise MonitorError("Collection start is later than its completion timestamp.")
    else:
        started = captured
    if captured > now_utc() + timedelta(minutes=5):
        raise MonitorError("Snapshot timestamp is in the future; check the collector clock.")
    if expected_day is not None and local_day(snapshot.captured_at) != valid_day(expected_day):
        raise MonitorError("Checkpoint is stale or belongs to another Beijing calendar day; collect a fresh checkpoint.")
    if not_before is not None and (captured < not_before or started < not_before):
        raise MonitorError("Collector did not produce a fresh checkpoint for this invocation.")
    if snapshot.complete is not True or snapshot.has_more is not False:
        raise MonitorError("Collection is incomplete: a verified terminal page (hasMore=false) is required.")
    if not snapshot.records:
        raise MonitorError("Collection returned no dramas; check authentication and the Short dramas response.")
    seen = set()
    for record in snapshot.records:
        if not isinstance(record.id, str) or not ID_PATTERN.fullmatch(record.id):
            raise MonitorError("Drama IDs must be 19-digit strings.")
        if record.id in seen:
            raise MonitorError("Duplicate drama ID in a snapshot; pagination cannot be committed.")
        seen.add(record.id)
        if not isinstance(record.title, str) or not record.title.strip():
            raise MonitorError("A drama title is missing.")
        integer(record.display_views, "display-equivalent count")
        integer(record.frontend_integer, "frontend integer")
        if record.display_views is None and record.frontend_integer is None:
            raise MonitorError("A drama has neither a visible play count nor a frontend integer.")
        if record.views_raw is not None and record.display_views != parse_display(record.views_raw):
            raise MonitorError("Visible play count disagrees with its saved abbreviation.")
        if not isinstance(record.metadata, dict):
            raise MonitorError("Drama metadata must be an object.")
    return snapshot
