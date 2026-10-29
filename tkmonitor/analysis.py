"""Calendar-day comparisons with separate display and precise-count series."""

from datetime import date, timedelta

from .model import MonitorError, beijing, local_day, parse_time, valid_day

METRICS = {"display": "display_views", "frontend": "frontend_integer"}
METRIC_LABELS = {"display": "页面显示折算整数", "frontend": "前端整数"}


def difference(after, before):
    return after - before if after is not None and before is not None else None


def rate(delta, base):
    return delta / base if delta is not None and base not in (None, 0) else None


def _sum(values):
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def _asset_path(state_dir, relative):
    if not isinstance(relative, str):
        return None
    target = (state_dir / relative).resolve()
    if not target.is_relative_to((state_dir / "assets").resolve()) or target.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".avif"}:
        return None
    return str(target)


def summarize(rows):
    summary = {"count": len(rows), "new_today": sum(row["status"] == "新增" for row in rows),
               "frontend_integer_count": sum(row["today_frontend_integer"] is not None for row in rows),
               "negative_changes": sum(row["day_delta"] is not None and row["day_delta"] < 0 for row in rows)}
    for label in ("initial", "yesterday", "today", "last"):
        summary[label + "_count"] = sum(row[label + "_views"] is not None for row in rows)
        summary[label + "_total"] = _sum(row[label + "_views"] for row in rows)
    for prefix, earlier, later in (("day", "yesterday", "today"), ("yesterday_from_initial", "initial", "yesterday"),
                                    ("today_from_initial", "initial", "today"), ("last", "last", "today")):
        pairs = [row for row in rows if row[earlier + "_views"] is not None and row[later + "_views"] is not None]
        delta = _sum(row[later + "_views"] - row[earlier + "_views"] for row in pairs)
        denominator = _sum(row[earlier + "_views"] for row in pairs)
        summary[prefix + "_delta"] = delta
        summary[prefix + "_rate"] = rate(delta, denominator)
        summary[prefix + "_comparable_count"] = len(pairs)
        summary[prefix + "_denominator"] = denominator
    summary["previous_count"] = summary["last_count"]
    summary["new_since_last"] = sum(row["last_recorded_at"] is None for row in rows)
    summary["new_drama_views_total"] = _sum(row["today_views"] for row in rows if row["status"] == "新增")
    return summary


def build_report(store, config, day, metric="display"):
    valid_day(day)
    if metric not in METRICS:
        raise MonitorError("Metric must be explicitly selected as display or frontend.")
    metric_field = METRICS[metric]
    previous_day = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    snapshots = [snapshot for snapshot in store.snapshots() if snapshot.account in config.handles and local_day(snapshot.captured_at) <= day]
    if not any(local_day(snapshot.captured_at) == day for snapshot in snapshots):
        raise MonitorError("No valid snapshot exists for this Beijing calendar day; collect or import that day first. Missing dates are never filled forward.")
    reports, all_rows, missing = [], [], []
    for account in config.accounts:
        handle = account["handle"]
        history = sorted((snapshot for snapshot in snapshots if snapshot.account == handle), key=lambda item: (parse_time(item.captured_at), item.id))
        daily = [snapshot for snapshot in history if local_day(snapshot.captured_at) == day]
        if not daily:
            missing.append(handle)
            continue
        current = daily[-1]
        # Compare daily-last to the most recent actual earlier capture, including same-day captures.
        before = [snapshot for snapshot in history if parse_time(snapshot.captured_at) < parse_time(current.captured_at)]
        yesterday_snapshots = [snapshot for snapshot in history if local_day(snapshot.captured_at) == previous_day]
        yesterday = yesterday_snapshots[-1] if yesterday_snapshots else None
        yesterday_map = {record.id: record for record in yesterday.records} if yesterday else {}
        earliest, precise_earliest, last_records = {}, {}, {}
        for snapshot in history:
            if parse_time(snapshot.captured_at) > parse_time(current.captured_at):
                continue
            for record in snapshot.records:
                if getattr(record, metric_field) is not None:
                    earliest.setdefault(record.id, (getattr(record, metric_field), snapshot.captured_at, snapshot.id))
                if record.frontend_integer is not None:
                    precise_earliest.setdefault(record.id, (record.frontend_integer, snapshot.captured_at))
                if parse_time(snapshot.captured_at) < parse_time(current.captured_at):
                    # Last actual observation, including a missing selected metric: no backfilling metric gaps.
                    last_records[record.id] = (record, snapshot.captured_at, snapshot.id)
        rows = []
        for index, record in enumerate(current.records, 1):
            metadata = store.metadata_at(handle, record.id, current.captured_at)
            if metadata.get("source_title") and metadata["source_title"] != record.title:
                metadata.pop("title_zh", None)
            initial = earliest.get(record.id)
            legacy_initial = store.baseline(handle, record.id, metric)
            if legacy_initial and parse_time(legacy_initial["captured_at"]) <= parse_time(current.captured_at):
                initial = (legacy_initial["value"], legacy_initial["captured_at"], legacy_initial["snapshot_id"])
            precise = precise_earliest.get(record.id)
            y_record = yesterday_map.get(record.id)
            last = last_records.get(record.id)
            initial_value = initial[0] if initial else None
            yesterday_value = getattr(y_record, metric_field) if y_record else None
            current_value = getattr(record, metric_field)
            last_value = getattr(last[0], metric_field) if last else None
            first_observation = next(snapshot for snapshot in history if any(item.id == record.id for item in snapshot.records))
            row = {**metadata, "account": handle, "account_name": account["name"], "order": index,
                "id": record.id, "title": record.title, "title_zh": metadata.get("title_zh") or "待翻译",
                "description_zh": metadata.get("description_zh") or "待翻译／待补充原始简介",
                "genre": metadata.get("genre") or "待归纳", "episodes": metadata.get("episodes"),
                "metadata_status": "available" if metadata.get("title_zh") and metadata.get("description_zh") else "pending",
                "metric": metric, "metric_label": METRIC_LABELS[metric], "display": record.views_raw,
                "views_display": record.views_raw, "views_approx": record.display_views, "approx": record.display_views,
                "initial_views": initial_value, "initial_recorded_at": initial[1] if initial else None,
                "initial_snapshot_id": initial[2] if initial else None,
                "yesterday_views": yesterday_value, "yesterday_recorded_at": yesterday.captured_at if y_record else None,
                "today_views": current_value, "today_recorded_at": current.captured_at,
                "today_frontend_integer": record.frontend_integer, "frontend": record.frontend_integer,
                "views_frontend": record.frontend_integer, "today_display_views": record.display_views,
                "first_precise_views": precise[0] if precise else None,
                "first_precise_recorded_at": precise[1] if precise else None,
                "last_views": last_value, "last_recorded_at": last[1] if last else None,
                "last_snapshot_id": last[2] if last else None,
                "status": "新增" if local_day(first_observation.captured_at) == day else "已有",
                "recorded_at": current.captured_at, "recorded_at_beijing": beijing(current.captured_at),
                "collection_started_at": current.collection_started_at,
                "source": current.source, "snapshot_id": current.id,
                "precision_note": "页面缩写折算值，历史低位不可恢复" if metric == "display" else "仅使用已保存的前端整数；未知不以页面折算值填充"}
            for prefix, after, base in (("day", current_value, yesterday_value),
                    ("yesterday_from_initial", yesterday_value, initial_value), ("today_from_initial", current_value, initial_value),
                    ("last", current_value, last_value)):
                delta = difference(after, base)
                row[prefix + "_delta"] = delta
                row[prefix + "_rate"] = rate(delta, base)
            hours = (parse_time(current.captured_at) - parse_time(last[1])).total_seconds() / 3600 if last else None
            row["last_elapsed_hours"] = hours
            row["last_delta_per_24h"] = row["last_delta"] * 24 / hours if hours and row["last_delta"] is not None else None
            row["last_rate_per_24h"] = row["last_rate"] * 24 / hours if hours and row["last_rate"] is not None else None
            row["initial_recorded_at_beijing"] = beijing(row["initial_recorded_at"])
            row["yesterday_recorded_at_beijing"] = beijing(row["yesterday_recorded_at"])
            row["last_recorded_at_beijing"] = beijing(row["last_recorded_at"])
            row["thumbnail"] = metadata.get("thumbnail_local")
            row["image"] = _asset_path(store.state_dir, metadata.get("thumbnail_local"))
            rows.append(row)
        summary = {**summarize(rows), "account": handle, "name": account["name"], "captured_at": current.captured_at,
                   "captured_at_beijing": beijing(current.captured_at), "collection_started_at": current.collection_started_at,
                   "last_recorded_at": before[-1].captured_at if before else None}
        reports.append({"name": account["name"], "account": handle, "prefix": account["prefix"],
            "source": "https://www.tiktok.com/@" + handle, "afterTime": current.captured_at,
            "beforeTime": before[-1].captured_at if before else None, "report_day": day, "previous_day": previous_day,
            "last_day": local_day(before[-1].captured_at) if before else None, "metric": metric,
            "summary": summary, "rows": rows, "records": rows})
        all_rows.extend(rows)
    available_days = sorted({local_day(snapshot.captured_at) for snapshot in store.snapshots() if snapshot.account in config.handles})
    return {"schema": "tkmonitor.report/v1", "timezone": "Asia/Shanghai", "report_day": day,
            "previous_day": previous_day, "metric": metric, "metric_label": METRIC_LABELS[metric],
            "complete": not missing, "missing_accounts": missing, "available_days": available_days,
            "summary": summarize(all_rows), "accounts": reports, "rows": all_rows}
