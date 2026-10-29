"""Immutable valid snapshots and transactional SQLite writes."""

import json
from pathlib import Path
import sqlite3
import uuid

from .model import MonitorError, Snapshot, Record, canonical, iso_time, local_day, now_utc, validate_snapshot

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY, account TEXT NOT NULL, captured_at TEXT NOT NULL,
    day TEXT NOT NULL, fingerprint TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_daily ON snapshots(account, day, captured_at);
CREATE TABLE IF NOT EXISTS baselines (
    account TEXT NOT NULL, drama_id TEXT NOT NULL, metric TEXT NOT NULL,
    value INTEGER NOT NULL, captured_at TEXT NOT NULL, snapshot_id TEXT NOT NULL,
    PRIMARY KEY(account,drama_id,metric)
);
CREATE TABLE IF NOT EXISTS metadata (
    account TEXT NOT NULL, drama_id TEXT NOT NULL, captured_at TEXT NOT NULL,
    payload TEXT NOT NULL, PRIMARY KEY(account,drama_id,captured_at)
);
CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL, message TEXT NOT NULL
);
"""


class Store:
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.state_dir / "monitor.sqlite3")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def commit_batch(self, snapshots, allowed_accounts=None, baselines=(), metadata=()):
        """Validate the entire batch before writes; conflicting IDs roll everything back."""
        snapshots = tuple(snapshots)
        seen = {}
        for snapshot in snapshots:
            validate_snapshot(snapshot, allowed_accounts)
            if snapshot.id in seen and seen[snapshot.id] != snapshot.digest:
                raise MonitorError("Batch contains conflicting versions of the same snapshot ID.")
            seen[snapshot.id] = snapshot.digest
        added = 0
        with self.connection:
            for snapshot in snapshots:
                existing = self.connection.execute("SELECT fingerprint FROM snapshots WHERE id=?", (snapshot.id,)).fetchone()
                if existing:
                    if existing[0] != snapshot.digest:
                        raise MonitorError("Immutable snapshot conflict: this ID already exists with different content.")
                    continue
                self.connection.execute("INSERT INTO snapshots VALUES(?,?,?,?,?,?)", (
                    snapshot.id, snapshot.account, iso_time_from_string(snapshot.captured_at), local_day(snapshot.captured_at),
                    snapshot.digest, canonical(snapshot.as_dict())))
                added += 1
                for record in snapshot.records:
                    if record.metadata:
                        self._metadata(snapshot.account, record.id, snapshot.captured_at, record.metadata)
            for baseline in baselines:
                key = (baseline["account"], baseline["id"], "display")
                value = (baseline["views"], baseline["capturedAt"], baseline["snapshotId"])
                existing = self.connection.execute("SELECT value,captured_at,snapshot_id FROM baselines WHERE account=? AND drama_id=? AND metric=?", key).fetchone()
                if existing and tuple(existing) != value:
                    raise MonitorError("Immutable initial baseline conflict; original values cannot be reset.")
                self.connection.execute("INSERT OR IGNORE INTO baselines VALUES(?,?,?,?,?,?)", (*key, *value))
            for account, drama_id, captured_at, payload in metadata:
                self._metadata(account, drama_id, captured_at, payload)
        return added

    def _metadata(self, account, drama_id, captured_at, payload):
        captured_at = iso_time_from_string(captured_at)
        encoded = canonical(payload)
        existing = self.connection.execute("SELECT payload FROM metadata WHERE account=? AND drama_id=? AND captured_at=?", (account, drama_id, captured_at)).fetchone()
        if existing and existing[0] != encoded:
            # A snapshot may contain partial metadata, with a richer companion workbook.
            merged = {**json.loads(existing[0]), **payload}
            self.connection.execute("UPDATE metadata SET payload=? WHERE account=? AND drama_id=? AND captured_at=?", (canonical(merged), account, drama_id, captured_at))
        else:
            self.connection.execute("INSERT OR IGNORE INTO metadata VALUES(?,?,?,?)", (account, drama_id, captured_at, encoded))

    def snapshots(self):
        rows = self.connection.execute("SELECT payload,fingerprint FROM snapshots ORDER BY captured_at,id").fetchall()
        result = []
        for row in rows:
            value = json.loads(row[0])
            result.append(Snapshot(id=value["id"], account=value["account"], captured_at=value["captured_at"],
                source=value["source"], records=tuple(Record(**item) for item in value["records"]),
                collection_started_at=value["collection_started_at"], complete=value["complete"], has_more=value["has_more"],
                original_fingerprint=row["fingerprint"]))
        return result

    def baseline(self, account, drama_id, metric):
        row = self.connection.execute("SELECT value,captured_at,snapshot_id FROM baselines WHERE account=? AND drama_id=? AND metric=?", (account, drama_id, metric)).fetchone()
        return dict(row) if row else None

    def metadata_at(self, account, drama_id, captured_at):
        rows = self.connection.execute("SELECT payload FROM metadata WHERE account=? AND drama_id=? AND captured_at<=? ORDER BY captured_at", (account, drama_id, iso_time_from_string(captured_at))).fetchall()
        merged = {}
        for row in rows:
            update = json.loads(row[0])
            if "source_title" in update and "source_title" in merged and update["source_title"] != merged["source_title"]:
                merged.pop("title_zh", None)
            # Retain inherited translations only when the saved source proves equality.
            # Explicit translations in this update are applied after invalidation.
            if "description_en" in update and (
                not update["description_en"] or update["description_en"] != merged.get("description_en")
            ):
                merged.pop("description_zh", None)
                merged.pop("genre", None)
            merged.update(update)
        return merged

    def start_attempt(self):
        attempt = uuid.uuid4().hex
        with self.connection:
            self.connection.execute("INSERT INTO attempts VALUES(?,?,NULL,'running','Collection started.')", (attempt, iso_time(now_utc())))
        return attempt

    def finish_attempt(self, attempt, status, message):
        with self.connection:
            self.connection.execute("UPDATE attempts SET finished_at=?,status=?,message=? WHERE id=?", (iso_time(now_utc()), status, message, attempt))

    def status(self):
        counts = self.connection.execute("SELECT account,COUNT(*) snapshots,MAX(captured_at) latest FROM snapshots GROUP BY account ORDER BY account").fetchall()
        attempts = self.connection.execute("SELECT * FROM attempts ORDER BY started_at DESC LIMIT 5").fetchall()
        dramas = {(snapshot.account, record.id) for snapshot in self.snapshots() for record in snapshot.records}
        return {"snapshots": sum(row["snapshots"] for row in counts), "dramas": len(dramas),
                "accounts": [dict(row) for row in counts], "recent_attempts": [dict(row) for row in attempts]}


def iso_time_from_string(value):
    from .model import parse_time
    return iso_time(parse_time(value))
