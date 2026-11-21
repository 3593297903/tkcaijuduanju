import base64
from argparse import Namespace
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
import shutil
import unittest
from uuid import uuid4

from tkmonitor.browser_fallback import (CAPTURE_SCHEMA, create_fallback, is_fallback,
                                        linked_fallback, resume_fallback)
from tkmonitor.config import load_config, read_json
from tkmonitor.media import _save_png
from tkmonitor.model import MonitorError, SourceError, iso_time, now_utc, today
from tkmonitor.onboarding import account_plan, atomic_json, create_browser_task
from tkmonitor.store import Store


FIRST_ID = "0000000000000000001"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jE1sAAAAASUVORK5CYII=")


class BrowserFallbackTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parents[1] / "_local" / "test-runs"
        self.root = test_root / ("browser-fallback-" + uuid4().hex)
        self.root.mkdir(parents=True)

        def cleanup():
            if self.root.resolve().parent != test_root.resolve():
                raise RuntimeError("Test cleanup escaped the workspace test directory.")
            shutil.rmtree(self.root)

        self.addCleanup(cleanup)
        self.config_file = self.root / "config.local.json"
        accounts = [{"handle": handle, "name": handle} for handle in ("demo", "other")]
        atomic_json(self.config_file, {"accounts": accounts})
        self.config = load_config(self.config_file)
        self.args = Namespace(report=True, finalize=True, xlsx=True, delivery=None,
                              metric="frontend", translate=False, day=today())
        self.reports = []
        self.results = []

    def new_task(self, selections=None, **kwargs):
        value = create_fallback(self.config, self.config, self.args,
                                selected=selections or {"demo", "other"}, reason="DNS_PUBLIC_UNAVAILABLE", **kwargs)
        self.assertEqual(value["status"], "NEEDS_BROWSER")
        return Path(value["manifest"])

    def write_capture(self, path, account, *, terminal=True, cursor_offset=0, covers=False):
        task = read_json(path)
        folder = Path(task["output_dir"]) / account
        folder.mkdir(parents=True, exist_ok=True)
        start = now_utc()
        capture = {"schema": CAPTURE_SCHEMA, "task_id": task["task_id"], "account": account,
                   "visible_account": account, "identity": {"handle": account, "secUid": "fixture", "source": "profile_document"},
                   "start_at": iso_time(start), "last_read_at": iso_time(start), "pages": [], "covers": []}
        if task["phase"] == "collect":
            rows = [
                {"status_code": 0, "hasMore": True, "cursor": 2,
                 "dramaList": [{"dramaID": FIRST_ID, "dramaName": "Example", "numWatched": 9007199254740993},
                               {"dramaID": FIRST_ID}]},
                {"status_code": 0, "hasMore": not terminal, "cursor": 2},
            ]
            for cursor, value in zip((0, 2 + cursor_offset), rows):
                body = json.dumps(value, ensure_ascii=False).encode("utf-8")
                (folder / f"page-{cursor}.local.json").write_bytes(body)
                capture["pages"].append({"cursor": cursor, "observed_at": iso_time(start), "secUid": "fixture",
                                         "sha256": sha256(body).hexdigest()})
        if covers:
            asset = folder / f"cover-{FIRST_ID}.bin"
            asset.write_bytes(PNG)
            capture["covers"] = [{"id": FIRST_ID, "title": "Example", "sha256": sha256(PNG).hexdigest(),
                                  "url": "https://p16-common-sign.tiktokcdn.com/observed-image.jpg",
                                  "identity_source": "card_id", "card_id": FIRST_ID}]
        atomic_json(folder / "capture.local.json", capture)
        if all((folder.parent / handle / "capture.local.json").is_file() for handle in task["selected_accounts"]):
            atomic_json(folder.parent / "ready.local.json", {"task_id": task["task_id"], "accounts": task["selected_accounts"]})
        return folder

    def export(self, config, store, options):
        self.reports.append((config, store.snapshots(), options))
        return 0

    def resume(self, path, export=None):
        return resume_fallback(self.config, path, export or self.export, self.results.append)

    def test_waits_for_every_account_before_batch_commit_and_reuses_receipt(self):
        path = self.new_task()
        self.assertTrue(is_fallback(path, self.config))
        self.write_capture(path, "demo")
        self.assertEqual(self.resume(path), 4)
        self.assertEqual(self.results[-1]["status"], "NEEDS_BROWSER")
        self.write_capture(path, "other")
        self.assertEqual(self.resume(path), 0)
        self.assertEqual(len(self.reports), 1)
        snapshots = self.reports[0][1]
        self.assertEqual({item.account for item in snapshots}, {"demo", "other"})
        for item in snapshots:
            self.assertEqual(item.source, "authorized browser response checkpoint")
            self.assertEqual(item.records[0].frontend_integer, 9007199254740993)
        self.assertEqual(self.resume(path), 0)
        self.assertEqual(self.results[-1]["reused_receipt"], True)
        self.assertEqual(len(self.reports), 1)

    def test_bad_second_account_never_commits_first(self):
        path = self.new_task()
        self.write_capture(path, "demo")
        self.write_capture(path, "other", cursor_offset=1)
        with self.assertRaisesRegex(MonitorError, "not continuous"):
            self.resume(path)
        with Store(self.config.state_dir) as store:
            self.assertEqual(store.snapshots(), [])
        self.write_capture(path, "other", terminal=False)
        with self.assertRaises(MonitorError):
            self.resume(path)

    def test_rejects_tampered_and_stale_body_before_committing(self):
        path = self.new_task({"demo"})
        folder = self.write_capture(path, "demo")
        body = folder / "page-0.local.json"
        body.write_bytes(body.read_bytes() + b"\n")
        with self.assertRaisesRegex(MonitorError, "page bytes"):
            self.resume(path)
        self.write_capture(path, "demo")
        proof = read_json(folder / "capture.local.json")
        proof["start_at"] = iso_time(now_utc() - timedelta(minutes=10))
        atomic_json(folder / "capture.local.json", proof)
        with self.assertRaisesRegex(MonitorError, "timestamps"):
            self.resume(path)

    def test_export_can_fail_and_resume_without_asking_for_pages_again(self):
        path = self.new_task({"demo"})
        folder = self.write_capture(path, "demo")

        def fail(*args):
            raise MonitorError("Excel failed")

        with self.assertRaisesRegex(MonitorError, "Excel failed"):
            self.resume(path, fail)
        (folder / "capture.local.json").unlink()
        self.assertEqual(self.resume(path), 0)
        self.assertEqual(len(self.reports[0][1]), 1)

    def test_collect_task_transfers_to_cover_recovery_of_exact_snapshot(self):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            self.skipTest("Pillow is optional")
        path = self.new_task({"demo"})
        self.write_capture(path, "demo")

        def export(config, store, options):
            if (config.state_dir / "assets/report_covers" / "demo" / f"{FIRST_ID}.png").is_file():
                return self.export(config, store, options)
            snapshot = store.snapshots()[0]
            folder = config.report_dir / options.day / options.metric
            atomic_json(folder / "media_validation.json", {"snapshot_ids": [snapshot.id], "downloads": [
                {"account": "demo", "id": FIRST_ID, "status": "unavailable", "error_code": "DNS_PUBLIC_UNAVAILABLE"},
            ]})
            atomic_json(folder / "merged.json", {"report_day": options.day, "metric": options.metric, "rows": [
                {"account": "demo", "id": FIRST_ID, "title": "Example", "recorded_at": snapshot.captured_at,
                 "snapshot_id": snapshot.id},
            ]})
            raise SourceError("cover failed", "COVER_NETWORK_UNAVAILABLE")

        self.assertEqual(self.resume(path, export), 4)
        cover_path = Path(self.results[-1]["manifest"])
        self.assertEqual(read_json(cover_path)["phase"], "covers")
        self.write_capture(cover_path, "demo", covers=True)
        self.assertEqual(self.resume(path, export), 0)
        self.assertEqual(read_json(path)["state"], "complete")
        self.assertEqual(read_json(cover_path)["state"], "complete")
        self.assertEqual(len(self.reports[0][1]), 1)

    def test_cover_only_recovery_can_finish_from_verified_cache(self):
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            self.skipTest("Pillow is optional")
        path = self.new_task({"demo"})
        self.write_capture(path, "demo")
        self.assertEqual(self.resume(path), 0)
        snapshot = self.reports[0][1][0]
        folder = self.config.report_dir / today() / "frontend"
        atomic_json(folder / "media_validation.json", {"snapshot_ids": [snapshot.id], "downloads": [
            {"account": "demo", "id": FIRST_ID, "status": "unavailable", "error_code": "COVER_SOURCE_UNAVAILABLE"},
        ]})
        atomic_json(folder / "merged.json", {"report_day": today(), "metric": "frontend", "rows": [
            {"account": "demo", "id": FIRST_ID, "title": "Example", "recorded_at": snapshot.captured_at,
             "snapshot_id": snapshot.id},
        ]})
        manifest = self.new_task({"demo"}, phase="covers")
        self.assertEqual(self.resume(manifest), 4)
        cache = self.config.state_dir / "assets/report_covers" / "demo" / f"{FIRST_ID}.png"
        _save_png(PNG, cache)
        self.assertEqual(self.resume(manifest), 0)
        self.assertEqual(len(self.reports[-1][1]), 1)

    def test_original_task_tracks_browser_receipt_and_completion(self):
        account_file = self.root / "config.demo.local.json"
        atomic_json(account_file, {"accounts": [{"handle": "demo"}]})
        account_config = load_config(account_file)
        _, original_path = create_browser_task(self.config, account_plan(self.config, "demo"), self.args)
        task = create_fallback(self.config, account_config, self.args, selected={"demo"},
                               reason="DNS_PUBLIC_UNAVAILABLE", origin_manifest=original_path)
        path = Path(task["manifest"])
        self.assertEqual(linked_fallback(self.config, original_path), path)
        self.write_capture(path, "demo")
        self.assertEqual(self.resume(path), 0)
        origin = read_json(original_path)
        self.assertEqual(origin["state"], "complete")
        self.assertEqual(origin["prepared_snapshots"][0]["id"], self.reports[0][1][0].id)
