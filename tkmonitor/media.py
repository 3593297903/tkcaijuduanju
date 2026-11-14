"""Optional thumbnail completion and explicitly labeled saved synopsis references."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from tempfile import NamedTemporaryFile
import time
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .analysis import build_report
from .config import read_json
from .model import MonitorError, valid_day
from .reports import export_report, _json_file
from .sources import _public_host

HISTORICAL = "【此前保存的中文资料，尚未核对本次英文原文】\n"
PENDING = "【中文待翻译；以下为本次采集的英文简介】\n"
MAX_BYTES = 8_000_000


def cover_host(url):
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        allowed = (parts.scheme == "https" and host.endswith(".tiktokcdn.com")
                   and parts.username is None and parts.password is None
                   and parts.port in (None, 443) and not parts.fragment)
    except (TypeError, ValueError):
        allowed = False
    if not allowed:
        raise MonitorError("Thumbnail source must be an HTTPS TikTok CDN URL.")
    return host


class _CoverRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        target = urljoin(request.full_url, newurl)
        if cover_host(target) != cover_host(request.full_url):
            raise MonitorError("Thumbnail redirect changed hostname.")
        _public_host(cover_host(target))
        return super().redirect_request(request, fp, code, message, headers, target)


def fetch_cover(url):
    """A bounded, TLS-verified GET; never send the private API request headers."""
    _public_host(cover_host(url))
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tiktok.com/"})
    # Match the successful daily downloader, independent of stale shell proxy settings.
    with build_opener(ProxyHandler({}), _CoverRedirect()).open(request, timeout=12) as response:
        if response.status != 200 or cover_host(response.geturl()) != cover_host(url):
            raise MonitorError("Thumbnail request did not return a successful same-host response.")
        content = response.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise MonitorError("Thumbnail exceeds the size limit.")
    return content


def _image_module():
    try:
        from PIL import Image
    except ImportError as exc:
        raise MonitorError("Thumbnail finalization needs optional Pillow: python -m pip install Pillow. Collection data is preserved.") from exc
    return Image


def _check_image(path):
    Image = _image_module()
    with Image.open(path) as image:
        if image.format not in {"PNG", "JPEG"} or image.width * image.height > 4_000_000:
            raise ValueError("Unsupported report thumbnail")
        image.verify()


def _save_png(content, destination):
    Image = _image_module()
    if len(content) > MAX_BYTES:
        raise ValueError("Oversized thumbnail")
    with Image.open(BytesIO(content)) as image:
        if image.format not in {"PNG", "JPEG", "WEBP", "AVIF"} or image.width * image.height > 4_000_000:
            raise ValueError("Unsupported thumbnail")
        image.load()
        image = image.convert("RGB")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        image.save(temporary, format="PNG")
        _check_image(temporary)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def complete_covers(store, report, download=True, transport=fetch_cover, sleep=time.sleep):
    """Only metadata is updated. SQLite remains on the calling thread."""
    _image_module()
    jobs = []
    for row in report["rows"]:
        metadata = store.metadata_at(row["account"], row["id"], row["recorded_at"])
        jobs.append((row, metadata.get("thumbnail_url")))

    def finish(job):
        row, url = job
        result = {"account": row["account"], "id": row["id"], "status": "unavailable"}
        try:
            if row.get("image") and Path(row["image"]).is_file():
                _check_image(row["image"])
                return {**result, "status": "existing"}, None
        except (OSError, ValueError):
            pass
        destination = store.state_dir / "assets/report_covers" / row["account"] / (row["id"] + ".png")
        # Reuse previously fetched local bytes before requesting a signed URL again.
        cached = store.state_dir / "assets/analysis_covers" / (row["account"] + "_" + row["id"] + ".jpg")
        for candidate in (destination, cached):
            if candidate.is_file():
                try:
                    _save_png(candidate.read_bytes(), destination)
                    return {**result, "status": "cached"}, destination
                except (OSError, ValueError):
                    pass
        if not download or not url:
            return result, None
        try:
            cover_host(url)
        except MonitorError:
            return {**result, "status": "no_source"}, None
        for attempt in range(3):
            try:
                _save_png(transport(url), destination)
                return {**result, "status": "downloaded"}, destination
            except (OSError, ValueError, MonitorError) as exc:
                result["error_type"] = type(exc).__name__
                if attempt < 2:
                    sleep(attempt + 1)
        return result, None

    with ThreadPoolExecutor(max_workers=4) as pool:
        completed = list(pool.map(finish, jobs))
    updates = [(row["account"], row["id"], row["recorded_at"],
                {"thumbnail_local": path.relative_to(store.state_dir).as_posix()})
               for (row, _), (_, path) in zip(jobs, completed) if path]
    store.commit_batch((), metadata=updates)
    return [result for result, _ in completed]


def historical_references(config, day, metric, output):
    """Read only reports at or before this day; never turn references into translations."""
    paths = [path for path in config.report_dir.glob("????-??-??/" + metric + "/merged.json")
             if path.parent.parent.name <= day]
    current = Path(output) / "merged.json"
    if current.is_file() and current not in paths:
        paths.append(current)
    references = {}
    for path in sorted(paths, key=lambda item: item.parent.parent.name, reverse=True):
        prior = read_json(path)
        if prior.get("report_day", "9999") > day or prior.get("metric") != metric:
            continue
        for row in prior.get("rows", []):
            key = (row["account"], row["id"], row["title"])
            text = row.get("historical_description_zh")
            if not text and row.get("metadata_status") == "historical_reference":
                text = row.get("description_zh", "").removeprefix(HISTORICAL)
            if not text and (row.get("metadata_status") == "available"
                             or row.get("synopsis_provenance", "").startswith("Current Chinese")):
                text = row.get("description_zh")
            if text and not text.startswith(PENDING) and text != "待翻译／待补充原始简介":
                references.setdefault(key, text)
    return references


def finalize_report(store, config, day, metric="frontend", output=None, download=True):
    valid_day(day)
    output = Path(output) if output else config.report_dir / day / metric
    references = historical_references(config, day, metric, output)
    before = build_report(store, config, day, metric)
    fingerprints = list(store.connection.execute("SELECT id,fingerprint FROM snapshots ORDER BY id"))
    fingerprints = [tuple(row) for row in fingerprints]
    downloads = complete_covers(store, before, download)
    valid_covers = {(item["account"], item["id"]) for item in downloads if item["status"] in {"existing", "cached", "downloaded"}}
    report = build_report(store, config, day, metric)
    coverage = {"records": len(report["rows"]), "covers": 0, "english_synopses": 0,
                "current_chinese": 0, "historical_chinese": 0, "english_only": 0}
    for row in report["rows"]:
        coverage["covers"] += (row["account"], row["id"]) in valid_covers
        coverage["english_synopses"] += bool(row.get("description_en"))
        current = store.metadata_at(row["account"], row["id"], row["recorded_at"]).get("description_zh")
        reference = references.get((row["account"], row["id"], row["title"]))
        if current:
            coverage["current_chinese"] += 1
            row["synopsis_provenance"] = "Current Chinese translation with matching saved English source"
        elif reference:
            coverage["historical_chinese"] += 1
            row.update(description_zh=HISTORICAL + reference, historical_description_zh=reference,
                       metadata_status="historical_reference",
                       synopsis_provenance="Historical Chinese reference; not verified against current English")
            row["metadata_note"] = "中文内容为历史参考，未作为已核对译文写回数据库；本次英文原文另列。"
        else:
            coverage["english_only"] += 1
            row.update(description_zh=PENDING + (row.get("description_en") or "原始简介待补充"),
                       metadata_status="pending", synopsis_provenance="Current original English; Chinese translation pending")
    numeric = [key for key in before["rows"][0] if key.endswith(("_views", "_delta", "_rate", "_at", "_id"))] if before["rows"] else []
    unchanged = ([{key: row.get(key) for key in numeric} for row in before["rows"]]
                 == [{key: row.get(key) for key in numeric} for row in report["rows"]]
                 and before["summary"] == report["summary"])
    intact = fingerprints == [tuple(row) for row in store.connection.execute("SELECT id,fingerprint FROM snapshots ORDER BY id")]
    if not unchanged or not intact:
        raise MonitorError("Finalization changed numeric observations or snapshot fingerprints.")
    report["media_coverage"] = coverage
    folder = export_report(store, config, report, output)
    complete = (report["complete"] and report["metric_coverage"]["status"] == "complete"
                and coverage["records"] == coverage["covers"] == coverage["english_synopses"])
    verification = {"day": day, "status": "verified" if complete else "incomplete", "coverage": coverage,
                    "numeric_observations_unchanged": unchanged, "snapshot_fingerprints_unchanged": intact,
                    "snapshot_ids": sorted({row["snapshot_id"] for row in report["rows"]}),
                    "downloads": downloads}
    _json_file(folder / "media_validation.json", verification)
    if not complete:
        raise MonitorError("Report finalization is incomplete; inspect media_validation.json. Valid collection data is preserved.")
    return folder, report
