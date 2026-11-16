"""Incremental Chinese metadata tasks, tied to exact saved original text.

The collector never guesses a plot or needs a paid translation API. Codex (or an
explicit provider) fills a private task; imports only update companion metadata.
"""

from pathlib import Path
import re

from .analysis import build_report
from .config import read_json
from .model import MonitorError, fingerprint, iso_time, now_utc, valid_day
from .onboarding import atomic_json

TASK_SCHEMA = "tkmonitor.translation-tasks/v1"
CJK = re.compile(r"[\u3400-\u9fff]")
PLACEHOLDERS = ("待翻译", "待补", "中文名待", "中文简介待", "【中文待翻译", "【此前保存的中文资料")


class TranslationNeeded(MonitorError):
    code = "NEEDS_TRANSLATION"

    def __init__(self, result):
        super().__init__("Chinese titles or synopses are incomplete; fill and import the saved translation task before delivery.")
        self.result = result


def source_hash(title, description):
    return fingerprint({"title": title, "description_en": description or "", "target_language": "zh-CN"})


def chinese_text(value):
    return (isinstance(value, str) and bool(value.strip()) and bool(CJK.search(value))
            and not value.strip().startswith(PLACEHOLDERS))


def row_translated(row):
    digest = source_hash(row["title"], row.get("description_en"))
    return (chinese_text(row.get("title_zh")) and chinese_text(row.get("description_zh"))
            and row.get("metadata_status") != "historical_reference"
            and row.get("translation_source_hash", digest) == digest)


def translation_tasks(store, config, day, metric="frontend", *, report=None, output=None):
    valid_day(day)
    report = report or build_report(store, config, day, metric)
    pending = []
    for row in report["rows"]:
        if row_translated(row):
            continue
        pending.append({"account": row["account"], "id": row["id"], "observed_at": row["recorded_at"],
                        "title": row["title"], "description_en": row.get("description_en") or "",
                        "source_hash": source_hash(row["title"], row.get("description_en")),
                        "platform_themes": row.get("platform_themes") or [],
                        "existing_title_zh": row.get("title_zh") if chinese_text(row.get("title_zh")) else None,
                        "title_zh": "", "description_zh": "", "genre": ""})
    if not pending:
        return {"status": "TRANSLATIONS_COMPLETE", "day": day, "records": len(report["rows"]), "pending": 0}
    task = {"schema": TASK_SCHEMA, "target_language": "zh-CN", "day": day, "metric": metric,
            "created_at": iso_time(now_utc()), "translator": "Codex", "records": pending}
    # Identical work is reused; created_at never forces a repeat translation.
    ident = fingerprint(pending)[:20]
    path = config.resolve(output) if output else store.state_dir / "translation_tasks" / (day + "-" + ident + ".local.json")
    if path.suffixes[-2:] != [".local", ".json"]:
        raise MonitorError("Translation tasks must be saved to a private .local.json file.")
    if not path.exists():
        atomic_json(path, task)
    return {"status": "NEEDS_TRANSLATION", "day": day, "pending": len(pending), "task": str(path),
            "collection_preserved": True, "next_action": "Fill Chinese fields, import-translations, then rerun finalize/export without recollecting."}


def require_chinese(store, config, day, metric="frontend", *, report=None):
    report = report or build_report(store, config, day, metric)
    if not report["complete"]:
        return {"status": "REPORT_INCOMPLETE", "pending": 0}
    result = translation_tasks(store, config, day, metric, report=report)
    if result["pending"]:
        raise TranslationNeeded(result)
    return result


def import_translations(store, config, path):
    task = read_json(path)
    if (not isinstance(task, dict) or task.get("schema") != TASK_SCHEMA or task.get("target_language") != "zh-CN"
            or not isinstance(task.get("records"), list) or not task["records"]):
        raise MonitorError("Expected a nonempty Chinese translation task file.")
    valid_day(task.get("day"))
    snapshots = store.snapshots()
    saved = {(s.account, r.id, s.captured_at): r for s in snapshots for r in s.records if s.account in config.handles}
    seen, updates = set(), []
    for item in task["records"]:
        if not isinstance(item, dict):
            raise MonitorError("Translation records must be objects.")
        key = (item.get("account"), item.get("id"), item.get("observed_at"))
        if any(not isinstance(v, str) for v in key) or key in seen or key not in saved:
            raise MonitorError("Translation account, drama ID or saved observation does not match this configuration.")
        seen.add(key)
        record = saved[key]
        metadata = store.metadata_at(*key)
        title, description = record.title, metadata.get("description_en") or ""
        digest = source_hash(title, description)
        if (item.get("title") != title or item.get("description_en", "") != description
                or item.get("source_hash") != digest):
            raise MonitorError("Translation source text changed or does not match the saved original; prepare a fresh task.")
        if not chinese_text(item.get("title_zh")) or not chinese_text(item.get("description_zh")):
            raise MonitorError("Every imported record needs a real Chinese title and synopsis, without pending markers.")
        if not description:
            raise MonitorError("The source synopsis is missing; obtain it before translating, never invent a plot.")
        genre = item.get("genre")
        if genre and not chinese_text(genre):
            raise MonitorError("An optional Chinese genre must be text, without pending markers.")
        update = {"source_title": title, "description_en": description, "title_zh": item["title_zh"].strip(),
                  "description_zh": item["description_zh"].strip(), "translation_source_hash": digest,
                  "translation_provider": str(task.get("translator") or "provided translation"),
                  "translation_created_at": iso_time(now_utc())}
        if genre:
            update["genre"] = genre.strip()
        updates.append((*key, update))
    before = [tuple(r) for r in store.connection.execute("SELECT id,fingerprint FROM snapshots ORDER BY id")]
    baselines = [tuple(r) for r in store.connection.execute("SELECT * FROM baselines ORDER BY account,drama_id,metric")]
    store.commit_batch((), metadata=updates)
    if (before != [tuple(r) for r in store.connection.execute("SELECT id,fingerprint FROM snapshots ORDER BY id")]
            or baselines != [tuple(r) for r in store.connection.execute("SELECT * FROM baselines ORDER BY account,drama_id,metric")]):
        raise MonitorError("Translation import changed immutable observations or initial values.")
    return {"status": "TRANSLATIONS_IMPORTED", "records": len(updates), "snapshots_unchanged": True, "baselines_unchanged": True}
