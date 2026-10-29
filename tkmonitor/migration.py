"""Import the original local history without changing its files or baselines."""

import hashlib
from pathlib import Path
import shutil

from .config import read_json
from .model import (ID_PATTERN, MonitorError, Record, Snapshot, fingerprint, integer,
                    parse_time, validate_snapshot)

METADATA_FIELDS = (
    "title_zh", "description_zh", "description_en", "genre", "platform_themes", "episodes",
    "video_id", "video_url", "create_time", "is_preview", "time_type", "metadata_note",
)


def _history_snapshot(value):
    try:
        records = tuple(Record(
            id=item["id"], title=item["title"], display_views=item.get("views"),
            frontend_integer=item.get("frontendInteger"), views_raw=item.get("viewsRaw"),
        ) for item in value["records"])
        snapshot = Snapshot(id=value["id"], account=value["account"], captured_at=value["capturedAt"],
            collection_started_at=value.get("collectionStartedAt"), source=value.get("source", "legacy history"),
            records=records, original_fingerprint=fingerprint(value))
    except (KeyError, TypeError) as exc:
        raise MonitorError("Legacy history snapshot is missing required fields.") from exc
    return validate_snapshot(snapshot)


def _copy_thumbnail(source, row, state_dir, account, drama_id):
    candidates = []
    for field in ("thumbnail", "image"):
        if row.get(field):
            path = Path(row[field])
            candidates.extend((path, source / path, source / "full_numbers" / path))
    for path in candidates:
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".avif"}:
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()[:16]
            relative = Path("assets") / account / (drama_id + "-" + digest + path.suffix.lower())
            target = state_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_bytes(content)
            return relative.as_posix()
    return None


def import_history(store, config, source):
    source = Path(source).resolve()
    history_file, baseline_file = source / "history_snapshots.json", source / "initial_baselines.json"
    history = read_json(history_file)
    baseline_document = read_json(baseline_file)
    if not isinstance(history, dict) or not isinstance(history.get("snapshots"), list):
        raise MonitorError("history_snapshots.json needs a snapshots array.")
    if not isinstance(baseline_document, dict) or not isinstance(baseline_document.get("records"), list):
        raise MonitorError("initial_baselines.json needs a records array.")
    snapshots = tuple(_history_snapshot(value) for value in history["snapshots"])
    for snapshot in snapshots:
        validate_snapshot(snapshot, config.handles)
    earliest = {}
    for snapshot in sorted(snapshots, key=lambda item: parse_time(item.captured_at)):
        for record in snapshot.records:
            if record.display_views is not None:
                earliest.setdefault((snapshot.account, record.id), (record.display_views, snapshot.captured_at, snapshot.id))
    baselines = baseline_document["records"]
    baseline_keys = set()
    for baseline in baselines:
        try:
            key = (baseline["account"], baseline["id"])
            if not isinstance(key[1], str) or not ID_PATTERN.fullmatch(key[1]) or key in baseline_keys:
                raise MonitorError("Invalid or duplicated initial baseline identity.")
            baseline_keys.add(key)
            integer(baseline["views"], "initial baseline")
            expected = earliest.get(key)
            if expected is None or expected[0] != baseline["views"] or parse_time(expected[1]) != parse_time(baseline["capturedAt"]) or expected[2] != baseline["snapshotId"]:
                raise MonitorError("Initial baseline differs from the source's earliest visible observation.")
            if baseline.get("metric", "page_display_equivalent_integer") != "page_display_equivalent_integer":
                raise MonitorError("Legacy initial baselines must use the display-equivalent metric.")
        except (KeyError, TypeError) as exc:
            raise MonitorError("Initial baseline is missing required fields.") from exc
    if baseline_keys != set(earliest):
        raise MonitorError("Initial baseline file does not cover every historical drama.")
    metadata = []
    workbook = source / "workbook_configs.json"
    if workbook.is_file():
        configs = read_json(workbook)
        if not isinstance(configs, list):
            raise MonitorError("Legacy workbook_configs.json must be a list.")
        for item in configs:
            for row in item.get("rows", item.get("records", [])):
                account, drama_id = row.get("account"), row.get("id")
                if (account, drama_id) not in earliest:
                    raise MonitorError("Workbook metadata references an unknown historical drama.")
                captured_at = row.get("today_recorded_at") or row.get("recorded_at") or item.get("afterTime")
                parse_time(captured_at)
                fields = {key: row[key] for key in METADATA_FIELDS if key in row}
                if row.get("title"):
                    fields["source_title"] = row["title"]
                thumbnail = _copy_thumbnail(source, row, store.state_dir, account, drama_id)
                if thumbnail:
                    fields["thumbnail_local"] = thumbnail
                metadata.append((account, drama_id, captured_at, fields))
    # Retain exact original files. Content-addressed copies cannot overwrite an old archive.
    for path in (history_file, baseline_file, workbook):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            target = store.state_dir / "raw" / "imports" / digest / path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(path, target)
    added = store.commit_batch(snapshots, config.handles, baselines, metadata)
    return {"imported_snapshots": added, "unchanged_snapshots": len(snapshots) - added,
            "initial_baselines": len(baselines), **store.status()}
