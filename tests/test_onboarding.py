"""Behavioral onboarding tests; all accounts, signatures and responses are synthetic."""
import contextlib
from copy import deepcopy
from datetime import timedelta
import io
import json
from pathlib import Path
import shutil
import unittest
import uuid
from unittest.mock import patch

from tkmonitor.analysis import build_report
from tkmonitor.cli import main
from tkmonitor.config import load_config
from tkmonitor.model import MonitorError, Record, Snapshot, SourceError, iso_time, now_utc, today
from tkmonitor.onboarding import account_lock, atomic_json, file_digest
from tkmonitor.sources import HttpResponse
from tkmonitor.store import Store

ONE = "0000000000000000001"
PRECISE = 9007199254740993


def spec(uid="SYNTHETIC_UID"):
    return {"url": "https://www.tiktok.com/api/drama/user/drama_list/?cursor=20&secUid=" + uid + "&X-Bogus=SYNTHETIC_PRIVATE",
            "headers": {"Referer": "https://www.tiktok.com/@old", "User-Agent": "synthetic"},
            "initial_cursor": "0", "cursor_param": "cursor", "max_pages": 100, "timeout_seconds": 15, "retries": 2}


def page():
    return {"status_code": 0, "hasMore": False, "cursor": 1,
            "dramaList": [{"dramaID": ONE, "dramaName": "Synthetic One", "numWatched": str(PRECISE),
                           "description": "Saved original synopsis", "numVideos": 30}]}


def json_objects(text):
    decoder, result, offset = json.JSONDecoder(), [], 0
    while (start := text.find("{", offset)) >= 0:
        try:
            value, end = decoder.raw_decode(text[start:])
            result.append(value)
            offset = start + end
        except ValueError:
            offset = start + 1
    return result


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parent.parent / "_local/test-runs"
        self.root = self.test_root / ("onboard-" + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        def cleanup():
            if self.root.resolve().parent != self.test_root.resolve():
                raise RuntimeError("Test cleanup escaped the workspace test directory.")
            shutil.rmtree(self.root)
        self.addCleanup(cleanup)
        self.config_path = self.root / "config.local.json"
        self.base_data = {"state_dir": "state", "accounts": [{"handle": "old", "name": "Old", "prefix": "Old",
                          "source": {"type": "http", "request_spec": "request-specs/old.local.json"}}]}
        atomic_json(self.config_path, self.base_data)
        atomic_json(self.root / "request-specs/old.local.json", spec())
        self.requests = []

    def invoke(self, *args, transport=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            if transport is None:
                code = main(["--config", str(self.config_path), *args])
            else:
                with patch("tkmonitor.sources.urllib_transport", transport):
                    code = main(["--config", str(self.config_path), *args])
        self.stdout, self.stderr = stdout.getvalue(), stderr.getvalue()
        return code, json_objects(self.stdout)

    def transport(self, url, headers, timeout):
        self.requests.append(url)
        return HttpResponse(200, {"Content-Type": "application/json"}, json.dumps(page()).encode(), url)

    def task(self, handle="new", *options):
        code, results = self.invoke("onboard", "--url", "https://www.tiktok.com/@" + handle, "--name", "New", *options)
        self.assertEqual(code, 4)
        return Path(results[-1]["manifest"])

    def capture(self, path, **changes):
        task = json.loads(path.read_text(encoding="utf-8"))
        request = spec("CAPTURED_UID")
        request["url"] = request["url"].replace("cursor=20", "cursor=0")
        atomic_json(task["candidate_path"], request)
        atomic_json(task["response_path"], page())
        evidence = {"schema": "tkmonitor.browser-capture/v1", "task_id": task["task_id"], "account": task["account"],
                    "visible_account": task["account"], "identity": {"handle": task["account"], "secUid": "CAPTURED_UID", "source": "profile_document"},
                    "captured_at": iso_time(now_utc()), "request_id": "synthetic-request", "request_method": "GET",
                    "http_status": 200, "loading_finished": True, "capture_source": "first_page", "scroll_count": 0,
                    "user_page_refreshes": 0, "temporary_tabs": 1, "duration_ms": 10,
                    "request_spec_sha256": file_digest(task["candidate_path"]), "response_sha256": file_digest(task["response_path"])}
        evidence.update(changes)
        atomic_json(task["evidence_path"], evidence)
        return task

    def store_count(self):
        with Store(self.root / "state") as store:
            return len(store.snapshots())

    def test_dry_run_is_read_only_and_does_not_probe_or_create_store(self):
        before = self.config_path.read_bytes()
        with patch("tkmonitor.sources.urllib_transport", side_effect=AssertionError("No probe allowed")):
            code, result = self.invoke("onboard", "--url", "https://www.tiktok.com/@new", "--dry-run")
        self.assertEqual(code, 0)
        self.assertEqual(result[-1]["next_action"], "needs_browser")
        self.assertFalse((self.root / "_local").exists())
        self.assertFalse((self.root / "state").exists())
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_invalid_input_url_does_not_write_or_request(self):
        for url in ("http://www.tiktok.com/@new", "https://evil.test/@new", "https://www.tiktok.com/@new/video/1",
                    "https://user@www.tiktok.com/@new", "https://www.tiktok.com/@../", "https://www.tiktok.com:444/@new"):
            code, _ = self.invoke("onboard", "--url", url)
            self.assertEqual(code, 2)
        self.assertFalse((self.root / "_local").exists())

    def test_missing_config_prepares_one_resumable_task_without_store(self):
        task = self.task()
        again = self.task()
        self.assertEqual(task, again)
        self.assertIs(json.loads(task.read_text(encoding="utf-8"))["options"]["translate"], False)
        self.assertFalse((self.root / "config.new.local.json").exists())
        self.assertFalse((self.root / "state").exists())
        code, result = self.invoke("onboard", "--resume", str(task))
        self.assertEqual(code, 4)
        self.assertEqual(result[-1]["status"], "NEEDS_BROWSER")

    def test_existing_legacy_spec_is_reused_with_no_extra_probe_and_single_account_report(self):
        original = self.config_path.read_bytes()
        with (
            patch("tkmonitor.translations.require_chinese", side_effect=AssertionError("Unexpected translation check")),
            patch("tkmonitor.translations.translation_tasks", side_effect=AssertionError("Unexpected translation task")),
        ):
            code, results = self.invoke("onboard", "--url", "https://www.tiktok.com/@old", "--report",
                                        transport=self.transport)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.requests), 1)
        self.assertIn("cursor=0", self.requests[0])
        self.assertTrue(results[-1]["spec_reused"])
        self.assertFalse(results[-1]["browser_required"])
        self.assertEqual(self.config_path.read_bytes(), original)
        report = json.loads((self.root / "state/account-reports/old" / today() / "frontend/merged.json").read_text(encoding="utf-8"))
        self.assertEqual(report["missing_accounts"], [])
        self.assertIs(report["include_translations"], False)
        self.assertEqual(report["rows"][0]["title"], "Synthetic One")
        self.assertEqual(report["rows"][0]["description_en"], "Saved original synopsis")
        self.assertEqual(report["rows"][0]["today_views"], PRECISE)

    def test_legacy_manifest_resumes_original_text_regardless_of_old_language_flag(self):
        with (
            patch("tkmonitor.translations.require_chinese", side_effect=AssertionError("Unexpected translation check")),
            patch("tkmonitor.translations.translation_tasks", side_effect=AssertionError("Unexpected translation task")),
        ):
            for allow_untranslated in (False, True):
                with self.subTest(allow_untranslated=allow_untranslated):
                    handle = "legacy_" + str(allow_untranslated).lower()
                    task_path = self.task(handle, "--report")
                    task = self.capture(task_path)
                    del task["options"]["translate"]
                    task["options"]["allow_untranslated"] = allow_untranslated
                    atomic_json(task_path, task)
                    code, _ = self.invoke("onboard", "--resume", str(task_path), transport=self.transport)
                    self.assertEqual(code, 0, self.stderr)
                    report_path = self.root / "state/account-reports" / handle / today() / "frontend/merged.json"
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    self.assertIs(report["include_translations"], False)
                    self.assertEqual(report["rows"][0]["description_en"], "Saved original synopsis")
        self.assertEqual(len(self.requests), 2)
        self.assertFalse((self.root / "state/translation_tasks").exists())

    def test_original_three_account_collect_makes_only_its_three_normal_requests(self):
        self.base_data["accounts"] = []
        for handle in ("old_a", "old_b", "old_c"):
            self.base_data["accounts"].append({"handle": handle, "name": handle, "prefix": handle,
                 "source": {"type": "http", "request_spec": "request-specs/" + handle + ".local.json"}})
            atomic_json(self.root / ("request-specs/" + handle + ".local.json"), spec(handle))
        atomic_json(self.config_path, self.base_data)
        code, _ = self.invoke("collect", transport=self.transport)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.store_count(), 3)
        self.assertFalse((self.root / "_local/onboarding").exists())

    def test_custom_single_account_paths_and_extra_fields_are_preserved(self):
        own = self.root / "config.old.local.json"
        data = deepcopy(self.base_data)
        data.update(report_dir="custom-reports", extra="keep")
        data["accounts"][0]["custom"] = "retained"
        atomic_json(own, data)
        before = own.read_bytes()
        code, _ = self.invoke("onboard", "--url", "https://www.tiktok.com/@old", "--report", "--allow-untranslated", transport=self.transport)
        self.assertEqual(code, 0)
        self.assertEqual(own.read_bytes(), before)
        self.assertTrue((self.root / "custom-reports" / today() / "frontend/merged.json").exists())

    def test_capture_resume_uses_original_collector_precise_values_and_keeps_default_scope(self):
        original = self.config_path.read_bytes()
        task_path = self.task("new", "--report", "--allow-untranslated")
        self.capture(task_path)
        code, result = self.invoke("onboard", "--resume", str(task_path), transport=self.transport)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(result[-1]["capture"]["scroll_count"], 0)
        self.assertEqual(self.config_path.read_bytes(), original)
        with Store(self.root / "state") as store:
            self.assertEqual(store.snapshots()[0].records[0].frontend_integer, PRECISE)
            config = load_config(self.root / "config.new.local.json")
            report = build_report(store, config, today(), "frontend")
            self.assertEqual(report["rows"][0]["initial_views"], PRECISE)
            self.assertIsNone(report["rows"][0]["yesterday_views"])
            self.assertIsNone(report["rows"][0]["day_rate"])
        code, result = self.invoke("onboard", "--resume", str(task_path), transport=lambda *a: self.fail("Repeat receipt must not recollect"))
        self.assertEqual(code, 0)
        self.assertTrue(result[-1]["reused_receipt"])
        self.assertEqual(self.store_count(), 1)

    def test_bad_identity_response_or_action_evidence_never_activates(self):
        for changes in ({"identity": {"handle": "other", "secUid": "CAPTURED_UID", "source": "profile_document"}},
                        {"http_status": 403}, {"loading_finished": False}, {"scroll_count": 1}, {"user_page_refreshes": 1}):
            task_path = self.task()
            self.capture(task_path, **changes)
            code, _ = self.invoke("onboard", "--resume", str(task_path), transport=lambda *a: self.fail("Bad capture must not request"))
            self.assertEqual(code, 2)
            self.assertFalse((self.root / "request-specs/new.local.json").exists())
            self.assertFalse((self.root / "config.new.local.json").exists())
            # Mark this intentionally invalid task as failed to prepare an independent case.
            task = json.loads(task_path.read_text(encoding="utf-8")); task["state"] = "invalid_fixture"
            atomic_json(task_path, task)

    def test_response_and_spec_byte_tampering_are_rejected(self):
        task_path = self.task(); task = self.capture(task_path)
        atomic_json(task["response_path"], {**page(), "cursor": 99})
        code, _ = self.invoke("onboard", "--resume", str(task_path))
        self.assertEqual(code, 2)
        self.assertFalse((self.root / "request-specs/new.local.json").exists())

    def test_cookie_header_is_rejected_even_with_matching_hash(self):
        task_path = self.task(); task = self.capture(task_path)
        request = json.loads(Path(task["candidate_path"]).read_text(encoding="utf-8")); request["headers"]["Cookie"] = "SYNTHETIC_SECRET"
        atomic_json(task["candidate_path"], request)
        evidence = json.loads(Path(task["evidence_path"]).read_text(encoding="utf-8")); evidence["request_spec_sha256"] = file_digest(task["candidate_path"])
        atomic_json(task["evidence_path"], evidence)
        code, _ = self.invoke("onboard", "--resume", str(task_path))
        self.assertEqual(code, 2)
        self.assertNotIn("SYNTHETIC_SECRET", self.stderr + self.stdout)

    def test_auth_recovery_is_once_and_preserves_old_file_until_verified(self):
        old = self.root / "request-specs/old.local.json"; before = old.read_bytes()
        def denied(url, headers, timeout):
            return HttpResponse(403, {"Content-Type": "application/json"}, b"{}", url)
        code, result = self.invoke("onboard", "--url", "https://www.tiktok.com/@old", transport=denied)
        self.assertEqual(code, 4)
        self.assertEqual(old.read_bytes(), before)
        task_path = Path(result[-1]["manifest"]); self.capture(task_path)
        code, _ = self.invoke("onboard", "--resume", str(task_path), transport=denied)
        self.assertEqual(code, 2)
        manifests = [p for p in task_path.parent.glob('*.local.json') if len(p.stem.removesuffix('.local')) == 32]
        self.assertEqual(len(manifests), 1)
        self.assertEqual(json.loads(old.read_text(encoding="utf-8"))["retries"], 2)
        self.assertNotIn("SYNTHETIC_PRIVATE", self.stderr + self.stdout)
        self.assertEqual(self.store_count(), 0)

    def test_non_auth_source_errors_do_not_trigger_browser_recovery(self):
        for error in (SourceError("synthetic", "NETWORK_ERROR"), SourceError("synthetic", "RATE_LIMIT"),
                      SourceError("synthetic", "SERVER_ERROR"), SourceError("synthetic", "NON_JSON"),
                      SourceError("synthetic", "API_ERROR"), MonitorError("Response schema changed")):
            with patch("tkmonitor.cli.collect_external", side_effect=error):
                code, _ = self.invoke("onboard", "--url", "https://www.tiktok.com/@old")
            self.assertEqual(code, 2)
        self.assertEqual(list((self.root / "_local/onboarding").glob('*.local.json')), [])

    def test_export_failure_resume_does_not_recollect_committed_values(self):
        task_path = self.task("new", "--xlsx", "--allow-untranslated"); self.capture(task_path)
        with patch("tkmonitor.cli.export_xlsx", side_effect=MonitorError("Synthetic export failed")):
            code, _ = self.invoke("onboard", "--resume", str(task_path), transport=self.transport)
        self.assertEqual(code, 2)
        self.assertEqual(self.store_count(), 1)
        self.assertEqual(json.loads(task_path.read_text(encoding="utf-8"))["state"], "collected")
        with patch("tkmonitor.cli.export_xlsx", return_value={"xlsx_export": "synthetic"}):
            code, _ = self.invoke("onboard", "--resume", str(task_path), transport=lambda *a: self.fail("No refetch on export resume"))
        self.assertEqual(code, 0)
        self.assertEqual(len(self.requests), 1)

    def test_translation_pause_then_import_and_resume_does_not_recollect(self):
        task_path = self.task("new", "--report", "--translate")
        task = self.capture(task_path)
        self.assertIs(task["options"]["translate"], True)
        manifest_before = task_path.read_bytes()
        code, _ = self.invoke("onboard", "--resume", str(task_path), "--translate", transport=self.transport)
        self.assertEqual(code, 2, self.stderr)
        self.assertIn("saved task options", self.stderr)
        self.assertEqual(task_path.read_bytes(), manifest_before)
        self.assertEqual(self.requests, [])
        code, results = self.invoke("onboard", "--resume", str(task_path), transport=self.transport)
        self.assertEqual(code, 5, self.stderr)
        self.assertEqual(self.store_count(), 1)
        translation_path = Path(results[-1]["task"])
        translation = json.loads(translation_path.read_text(encoding="utf-8"))
        translation["records"][0].update(title_zh="小小英雄", description_zh="已保存原文的中文翻译。", genre="亲情")
        atomic_json(translation_path, translation)
        task = json.loads(task_path.read_text(encoding="utf-8"))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--config", task["account_config"], "import-translations", "--input", str(translation_path)]), 0)
        code, _ = self.invoke("onboard", "--resume", str(task_path), transport=lambda *a: self.fail("Translation resume must not recollect"))
        self.assertEqual(code, 0, self.stderr)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(json.loads(task_path.read_text(encoding="utf-8"))["state"], "complete")

    def test_crash_after_commit_is_detected_from_prepared_snapshot_fingerprints(self):
        task_path = self.task(); self.capture(task_path)
        original_write = atomic_json
        def interrupt(path, data):
            if data.get("state") == "collected":
                raise OSError("Synthetic journal interruption")
            return original_write(path, data)
        with patch("tkmonitor.onboarding.atomic_json", side_effect=interrupt):
            code, _ = self.invoke("onboard", "--resume", str(task_path), transport=self.transport)
        self.assertEqual(code, 2)
        self.assertEqual(self.store_count(), 1)
        code, _ = self.invoke("onboard", "--resume", str(task_path), transport=lambda *a: self.fail("Committed values must not be refetched"))
        self.assertEqual(code, 0)
        self.assertEqual(len(self.requests), 1)

    def test_activation_interruption_can_resume_without_destroying_old_config(self):
        task_path = self.task(); self.capture(task_path)
        with patch("tkmonitor.onboarding._activate_config", side_effect=OSError("Synthetic interrupted activation")):
            code, _ = self.invoke("onboard", "--resume", str(task_path))
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(task_path.read_text(encoding="utf-8"))["state"], "activating")
        code, _ = self.invoke("onboard", "--resume", str(task_path), transport=self.transport)
        self.assertEqual(code, 0, self.stderr)
        self.assertEqual(self.store_count(), 1)

    def test_cross_day_resume_uses_fresh_actual_day(self):
        task_path = self.task(); task = self.capture(task_path)
        task["options"]["day"] = (now_utc() - timedelta(days=3)).date().isoformat()
        atomic_json(task_path, task)
        code, result = self.invoke("onboard", "--resume", str(task_path), transport=self.transport)
        self.assertEqual(code, 0)
        self.assertTrue(any(item.get("status") == "STATISTICS_DAY_CHANGED" for item in result))
        self.assertEqual(result[-1]["day"], today())

    def test_changed_config_or_output_path_is_rejected(self):
        task_path = self.task(); task = self.capture(task_path)
        task["candidate_path"] = str(self.root / "outside.local.json")
        atomic_json(task_path, task)
        code, _ = self.invoke("onboard", "--resume", str(task_path))
        self.assertEqual(code, 2)

    def test_account_lock_rejects_overlapping_operation_and_recovers_after_release(self):
        with account_lock(self.root, "new"):
            with self.assertRaises(MonitorError):
                with account_lock(self.root, "new"):
                    self.fail("Overlapping lock acquired")
        with account_lock(self.root, "new"):
            pass

    def test_opt_in_batch_recovery_keeps_atomic_commit_and_default_scope(self):
        self.base_data["accounts"] = []
        for handle in ("old_a", "old_b", "old_c"):
            self.base_data["accounts"].append({"handle": handle, "name": handle, "prefix": handle,
                "source": {"type": "http", "request_spec": "request-specs/" + handle + ".local.json"}})
            atomic_json(self.root / ("request-specs/" + handle + ".local.json"), spec(handle))
        atomic_json(self.config_path, self.base_data); original = self.config_path.read_bytes()
        def snapshot(config, account, day, raw, *, network=None):
            at = iso_time(now_utc())
            return Snapshot(account["handle"] + ":" + at, account["handle"], at, "synthetic", (Record(ONE, "One", None, 123),), at)
        def first(config, account, day, raw, *, network=None):
            if account["handle"] == "old_b":
                raise SourceError("Synthetic rejected request", "AUTH_REQUIRED")
            return snapshot(config, account, day, raw)
        with patch("tkmonitor.cli.collect_external", side_effect=first):
            code, result = self.invoke("collect", "--recover-request")
        self.assertEqual(code, 4); self.assertEqual(self.store_count(), 0)
        task_path = Path(result[-1]["manifest"]); self.capture(task_path)
        with patch("tkmonitor.cli.collect_external", side_effect=snapshot):
            code, _ = self.invoke("onboard", "--resume", str(task_path))
        self.assertEqual(code, 0); self.assertEqual(self.store_count(), 3)
        self.assertEqual(self.config_path.read_bytes(), original)
        self.assertFalse((self.root / "config.old_b.local.json").exists())

    def test_corrupt_existing_request_is_not_silently_replaced(self):
        target = self.root / "request-specs/old.local.json"; target.write_text("broken JSON", encoding="utf-8")
        code, _ = self.invoke("onboard", "--url", "https://www.tiktok.com/@old")
        self.assertEqual(code, 2)
        self.assertEqual(target.read_text(encoding="utf-8"), "broken JSON")
        self.assertEqual(list((self.root / "_local/onboarding").glob('*.local.json')), [])

    def test_crash_before_attempt_finished_recovers_valid_status_without_refetch(self):
        task_path = self.task(); self.capture(task_path)
        original = Store.finish_attempt
        def interrupt(store, ident, status, message):
            if status == "valid":
                raise OSError("Synthetic interruption after commit")
            return original(store, ident, status, message)
        with patch.object(Store, "finish_attempt", interrupt):
            code, _ = self.invoke("onboard", "--resume", str(task_path), transport=self.transport)
        self.assertEqual(code, 2)
        self.assertEqual(self.store_count(), 1)
        code, _ = self.invoke("onboard", "--resume", str(task_path), transport=lambda *a: self.fail("No refetch after committed transaction"))
        self.assertEqual(code, 0, self.stderr)
        with Store(self.root / "state") as store:
            self.assertEqual(store.status()["recent_attempts"][0]["status"], "valid")

    def test_normal_collection_preserves_initial_values_chinese_and_cover_bytes(self):
        import base64
        at = iso_time(now_utc() - timedelta(days=1))
        cover = self.root / "state/assets/synthetic.png"
        cover.parent.mkdir(parents=True)
        cover.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='))
        before_image = cover.read_bytes()
        with Store(self.root / "state") as store:
            store.commit_batch([Snapshot("initial-old", "old", at, "synthetic", (Record(ONE, "Synthetic One", 10, 100),), at)],
                metadata=[("old", ONE, at, {"source_title": "Synthetic One", "description_en": "Saved original synopsis",
                    "title_zh": "保留标题", "description_zh": "保留简介", "genre": "保留题材", "thumbnail_local": "assets/synthetic.png"})],
                baselines=[{"account": "old", "id": ONE, "views": 10, "capturedAt": at, "snapshotId": "initial-old"}])
        code, _ = self.invoke("onboard", "--url", "https://www.tiktok.com/@old", "--report", transport=self.transport)
        self.assertEqual(code, 0, self.stderr)
        with Store(self.root / "state") as store:
            result = build_report(store, load_config(self.root / "config.old.local.json"), today(), "frontend")["rows"][0]
            self.assertEqual(result["initial_views"], 100)
            self.assertEqual((result["title_zh"], result["description_zh"]), ("保留标题", "保留简介"))
            self.assertEqual(store.baseline("old", ONE, "display")["value"], 10)
        self.assertEqual(cover.read_bytes(), before_image)

    def test_completed_receipt_requires_real_snapshot_evidence(self):
        task_path = self.task(); task = self.capture(task_path)
        task["state"] = "complete"
        atomic_json(task_path, task)
        code, _ = self.invoke("onboard", "--resume", str(task_path))
        self.assertEqual(code, 2)
        self.assertNotIn('"status": "COMPLETED"', self.stdout)

    def test_new_scroll_permission_prepares_distinct_task_without_mutating_previous(self):
        first = self.task()
        second = self.task("new", "--allow-scroll-fallback")
        self.assertNotEqual(first, second)
        self.assertFalse(json.loads(first.read_text(encoding="utf-8"))["allow_scroll_fallback"])
        self.assertTrue(json.loads(second.read_text(encoding="utf-8"))["allow_scroll_fallback"])

    def test_interrupted_browser_capture_requires_explicit_new_task_and_keeps_old_evidence(self):
        first = self.task(); task = json.loads(first.read_text(encoding="utf-8"))
        attempted = first.with_name(task["task_id"] + ".capture-attempt.local.json")
        atomic_json(attempted, {"started_at": iso_time(now_utc())})
        code, result = self.invoke("onboard", "--resume", str(first))
        self.assertEqual(code, 4)
        self.assertEqual(result[-1]["status"], "NEEDS_BROWSER_ACTION")
        second = self.task("new", "--new-task")
        self.assertNotEqual(first, second)
        self.assertTrue(attempted.is_file())
