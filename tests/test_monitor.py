import contextlib
import csv
from datetime import timedelta
import io
import json
from pathlib import Path
import shutil
import sys
import unittest
import uuid
from unittest.mock import patch

from tkmonitor.analysis import build_report
from tkmonitor.cli import main
from tkmonitor.config import load_config
from tkmonitor.migration import import_history
from tkmonitor.model import MonitorError, Record, Snapshot, iso_time, local_day, now_utc, parse_display
from tkmonitor.reports import export_report
from tkmonitor.sources import HttpResponse, collect_checkpoint, collect_external, collect_http, validate_url
from tkmonitor.store import Store

ONE, TWO = "0000000000000000001", "0000000000000000002"
FIXTURES = Path(__file__).parent / "fixtures"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def snap(at, value, precise=None, drama_id=ONE, account="demo", ident=None):
    return Snapshot(ident or account + ":" + at, account, at, "synthetic fixture", (Record(drama_id, "Synthetic One", value, precise),))


class LocalCase(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parent.parent / "_local" / "test-runs"
        test_root.mkdir(parents=True, exist_ok=True)
        self.root = test_root / uuid.uuid4().hex
        self.root.mkdir()
        def cleanup():
            if self.root.resolve().parent != test_root.resolve():
                raise RuntimeError("Test cleanup target escaped its workspace.")
            shutil.rmtree(self.root)
        self.addCleanup(cleanup)
        self.config_path = self.root / "config.local.json"
        write_json(self.config_path, {"state_dir": "state", "accounts": [{"handle": "demo", "name": "Demo", "prefix": "Demo"}]})
        self.config = load_config(self.config_path)
        self.store = Store(self.config.state_dir)
        self.addCleanup(self.store.close)

    def fixture_history(self):
        target = self.root / "history"
        shutil.copytree(FIXTURES, target)
        return target


class MigrationTests(LocalCase):
    def test_idempotent_import_preserves_raw_and_every_initial_value(self):
        source = self.fixture_history()
        original = (source / "history_snapshots.json").read_bytes()
        first = import_history(self.store, self.config, source)
        second = import_history(self.store, self.config, source)
        self.assertEqual((first["imported_snapshots"], second["imported_snapshots"]), (3, 0))
        self.assertEqual(second["snapshots"], 3)
        self.assertEqual(second["dramas"], 2)
        self.assertEqual(self.store.commit_batch(self.store.snapshots()), 0)
        self.assertEqual((source / "history_snapshots.json").read_bytes(), original)
        self.assertEqual(len(list((self.config.state_dir / "raw/imports").rglob("history_snapshots.json"))), 1)
        for baseline in json.loads((source / "initial_baselines.json").read_text())["records"]:
            stored = self.store.baseline(baseline["account"], baseline["id"], "display")
            self.assertEqual(stored["value"], baseline["views"])
            self.assertEqual(stored["captured_at"], baseline["capturedAt"])

    def test_changed_same_id_rejected_instead_of_overwriting(self):
        source = self.fixture_history()
        import_history(self.store, self.config, source)
        path = source / "history_snapshots.json"
        value = json.loads(path.read_text())
        value["snapshots"][2]["records"][0].update(views=1, viewsRaw="1")
        write_json(path, value)
        with self.assertRaisesRegex(MonitorError, "Immutable snapshot conflict"):
            import_history(self.store, self.config, source)
        self.assertEqual(self.store.snapshots()[-1].records[0].display_views, 0)

    def test_invalid_baseline_rejects_entire_migration(self):
        source = self.fixture_history()
        path = source / "initial_baselines.json"
        value = json.loads(path.read_text())
        value["records"][0]["views"] = 999
        write_json(path, value)
        with self.assertRaisesRegex(MonitorError, "earliest"):
            import_history(self.store, self.config, source)
        self.assertEqual(self.store.status()["snapshots"], 0)

    def test_transaction_rolls_back_new_snapshot_before_conflict(self):
        first = snap("2020-01-01T00:00:00Z", 100, ident="first")
        self.store.commit_batch([first])
        with self.assertRaises(MonitorError):
            self.store.commit_batch([snap("2020-01-02T00:00:00Z", 20), snap("2020-01-01T00:00:00Z", 999, ident="first")])
        self.assertEqual(self.store.status()["snapshots"], 1)

    def test_metadata_changes_invalidate_old_translations_and_covers_stay_in_assets(self):
        old = snap("2020-01-01T00:00:00Z", 100)
        self.store.commit_batch([old], metadata=[("demo", ONE, old.captured_at, {
            "source_title": "Synthetic One", "title_zh": "旧译名", "description_en": "Old description",
            "description_zh": "旧简介", "genre": "旧题材", "thumbnail_local": "../../config.local.json",
        })])
        self.store.commit_batch([Snapshot("changed", "demo", "2020-01-02T00:00:00Z", "synthetic", (
            Record(ONE, "Changed title", 200, metadata={"source_title": "Changed title", "description_en": "New description"}),))])
        old_row = build_report(self.store, self.config, "2020-01-01")["rows"][0]
        new_row = build_report(self.store, self.config, "2020-01-02")["rows"][0]
        self.assertEqual(old_row["title_zh"], "旧译名")
        self.assertEqual(new_row["title_zh"], "待翻译")
        self.assertEqual(new_row["description_zh"], "待翻译／待补充原始简介")
        self.assertEqual(new_row["genre"], "待归纳")
        self.assertIsNone(new_row["image"])

    def test_new_source_description_without_saved_old_source_invalidates_inherited_translation(self):
        old = snap("2020-01-01T00:00:00Z", 100)
        self.store.commit_batch([old], metadata=[("demo", ONE, old.captured_at, {
            "source_title": "Synthetic One", "title_zh": "旧译标题", "description_zh": "甲", "genre": "旧题材",
        })])
        changed = snap("2020-01-02T00:00:00Z", 200)
        self.store.commit_batch([changed], metadata=[("demo", ONE, changed.captured_at, {
            "source_title": "Synthetic One", "description_en": "About B",
        })])
        row = build_report(self.store, self.config, "2020-01-02")["rows"][0]
        self.assertEqual(row["title_zh"], "旧译标题")
        self.assertEqual(row["description_zh"], "待翻译／待补充原始简介")
        self.assertEqual(row["genre"], "待归纳")
        self.assertEqual(row["metadata_status"], "pending")
        old_row = build_report(self.store, self.config, "2020-01-01")["rows"][0]
        self.assertEqual((old_row["description_zh"], old_row["genre"]), ("甲", "旧题材"))

    def test_equal_known_source_preserves_translation_and_new_explicit_translation_is_used(self):
        old = snap("2020-01-01T00:00:00Z", 100)
        self.store.commit_batch([old], metadata=[("demo", ONE, old.captured_at, {
            "source_title": "Synthetic One", "title_zh": "旧译标题", "description_en": "About A",
            "description_zh": "甲", "genre": "旧题材",
        })])
        unchanged = snap("2020-01-02T00:00:00Z", 200)
        self.store.commit_batch([unchanged], metadata=[("demo", ONE, unchanged.captured_at, {
            "source_title": "Synthetic One", "description_en": "About A",
        })])
        row = build_report(self.store, self.config, "2020-01-02")["rows"][0]
        self.assertEqual((row["title_zh"], row["description_zh"], row["genre"]), ("旧译标题", "甲", "旧题材"))
        self.assertEqual(row["metadata_status"], "available")
        translated = snap("2020-01-03T00:00:00Z", 300)
        self.store.commit_batch([translated], metadata=[("demo", ONE, translated.captured_at, {
            "description_en": "About B", "description_zh": "乙", "genre": "新题材",
        })])
        row = build_report(self.store, self.config, "2020-01-03")["rows"][0]
        self.assertEqual((row["title_zh"], row["description_zh"], row["genre"]), ("旧译标题", "乙", "新题材"))
        self.assertEqual(row["metadata_status"], "available")

    def test_explicit_new_translation_wins_when_old_source_was_unknown(self):
        old = snap("2020-01-01T00:00:00Z", 100)
        self.store.commit_batch([old], metadata=[("demo", ONE, old.captured_at, {
            "source_title": "Synthetic One", "title_zh": "旧译标题", "description_zh": "甲", "genre": "旧题材",
        })])
        updated = snap("2020-01-02T00:00:00Z", 200)
        self.store.commit_batch([updated], metadata=[("demo", ONE, updated.captured_at, {
            "description_en": "About B", "description_zh": "乙", "genre": "新题材",
        })])
        row = build_report(self.store, self.config, "2020-01-02")["rows"][0]
        self.assertEqual((row["title_zh"], row["description_zh"], row["genre"]), ("旧译标题", "乙", "新题材"))
        self.assertEqual(row["metadata_status"], "available")

    def test_rich_metadata_and_covers_copied_locally(self):
        source = self.fixture_history()
        cover = source / "thumbnails/demo/cover.png"
        cover.parent.mkdir(parents=True)
        cover.write_bytes(b"synthetic png bytes")
        write_json(source / "workbook_configs.json", [{"afterTime": "2020-01-03T03:00:00Z", "rows": [{
            "account": "demo", "id": ONE, "title_zh": "模拟剧目", "description_zh": "测试简介", "genre": "模拟", "episodes": 3,
            "thumbnail": "thumbnails/demo/cover.png", "video_id": TWO, "create_time": None,
        }]}])
        import_history(self.store, self.config, source)
        row = build_report(self.store, self.config, "2020-01-03")["rows"][0]
        self.assertEqual(row["title_zh"], "模拟剧目")
        self.assertEqual(Path(row["image"]).read_bytes(), cover.read_bytes())
        self.assertIsNone(row["create_time"])
        self.assertEqual(build_report(self.store, self.config, "2020-01-01")["rows"][0]["title_zh"], "待翻译")


class AnalysisTests(LocalCase):
    def setUp(self):
        super().setUp()
        import_history(self.store, self.config, self.fixture_history())

    def test_missing_day_is_not_filled_and_zero_remains_zero(self):
        with self.assertRaisesRegex(MonitorError, "No valid snapshot"):
            build_report(self.store, self.config, "2020-01-02")
        report = build_report(self.store, self.config, "2020-01-03")
        one, two = report["rows"]
        self.assertEqual(one["today_views"], 0)
        self.assertIsNone(one["yesterday_views"])
        self.assertIsNone(one["day_delta"])
        self.assertEqual(one["last_views"], 110)
        self.assertEqual(one["last_delta"], -110)
        self.assertIsNone(two["yesterday_views"])
        self.assertIsNone(two["day_rate"])
        self.assertEqual(two["status"], "新增")
        self.assertIsNone(report["summary"]["yesterday_total"])

    def test_last_daily_selection_and_same_day_last_actual(self):
        report = build_report(self.store, self.config, "2020-01-01")
        row = report["rows"][0]
        self.assertEqual((row["initial_views"], row["today_views"], row["last_views"]), (100, 110, 100))
        self.assertEqual(row["last_elapsed_hours"], 1)
        self.assertEqual(row["last_delta_per_24h"], 240)
        self.assertIsNone(row["yesterday_views"])

    def test_no_future_baseline_or_precise_values_leak_back(self):
        first = build_report(self.store, self.config, "2020-01-01")
        self.assertEqual(len(first["rows"]), 1)
        self.assertIsNone(first["rows"][0]["first_precise_views"])
        precise = build_report(self.store, self.config, "2020-01-01", "frontend")
        self.assertIsNone(precise["rows"][0]["initial_views"])
        self.assertIsNone(precise["summary"]["today_total"])
        self.assertIsNone(precise["summary"]["day_delta"])

    def test_metrics_are_isolated_and_first_precise_baseline_separate(self):
        display = build_report(self.store, self.config, "2020-01-03", "display")
        precise = build_report(self.store, self.config, "2020-01-03", "frontend")
        self.assertEqual((display["summary"]["today_total"], precise["summary"]["today_total"]), (20, 16))
        self.assertEqual(display["rows"][0]["initial_views"], 100)
        self.assertEqual(precise["rows"][0]["initial_views"], 7)
        self.assertIsNone(precise["rows"][0]["last_views"])
        self.assertIsNone(precise["rows"][0]["last_delta"])

    def test_all_missing_selected_metric_is_flagged_without_substitution(self):
        report = build_report(self.store, self.config, "2020-01-01", "frontend")
        self.assertFalse(report["complete"])
        self.assertTrue(report["account_coverage_complete"])
        self.assertEqual(report["metric_coverage"], {"known": 0, "total": 1, "status": "missing"})
        self.assertTrue(report["warnings"])
        self.assertIsNone(report["summary"]["today_total"])
        self.assertIsNone(report["rows"][0]["today_views"])

    def test_partial_precise_coverage_is_successful_and_real_zero_is_known(self):
        self.store.commit_batch([Snapshot("partial", "demo", "2020-01-04T04:00:00Z", "synthetic", (
            Record(ONE, "One", 5, 0), Record(TWO, "Two", 20, None)))])
        report = build_report(self.store, self.config, "2020-01-04", "frontend")
        self.assertTrue(report["complete"])
        self.assertEqual(report["metric_coverage"], {"known": 1, "total": 2, "status": "partial"})
        self.assertEqual(report["summary"]["today_total"], 0)
        self.assertIsNone(report["rows"][1]["today_views"])

    def test_zero_denominator_has_delta_but_no_rate_and_negative_is_retained(self):
        self.store.commit_batch([snap("2020-01-04T04:00:00Z", 5)])
        report = build_report(self.store, self.config, "2020-01-04")
        self.assertEqual(report["rows"][0]["yesterday_views"], 0)
        self.assertEqual(report["rows"][0]["day_delta"], 5)
        self.assertIsNone(report["rows"][0]["day_rate"])
        self.assertIsNone(report["summary"]["day_rate"])
        self.assertEqual(build_report(self.store, self.config, "2020-01-03")["rows"][0]["today_from_initial_delta"], -100)

    def test_summaries_use_paired_denominators(self):
        self.store.commit_batch([Snapshot("day4", "demo", "2020-01-04T04:00:00Z", "synthetic", (
            Record(ONE, "One", 5), Record(TWO, "Two", None, 15), Record("0000000000000000003", "Three", 100)))])
        summary = build_report(self.store, self.config, "2020-01-04")["summary"]
        self.assertEqual(summary["today_total"], 105)
        self.assertEqual(summary["day_delta"], 5)
        self.assertEqual(summary["day_comparable_count"], 1)
        self.assertEqual(summary["day_denominator"], 0)
        self.assertIsNone(summary["day_rate"])

    def test_beijing_calendar_day_uses_recorded_utc(self):
        self.store.commit_batch([snap("2020-01-04T16:01:00Z", 99)])
        report = build_report(self.store, self.config, "2020-01-05")
        self.assertEqual(report["rows"][0]["recorded_at_beijing"], "2020-01-05 00:01:00")

    def test_export_full_digits_safe_html_and_offline_controls(self):
        self.store.commit_batch([Snapshot("export", "demo", "2020-01-04T04:00:00Z", "synthetic", (
            Record(ONE, "</script><img src=x onerror=alert(1)>", 123456789, metadata={"title_zh": "模拟"}),))])
        report = build_report(self.store, self.config, "2020-01-04")
        folder = export_report(self.store, self.config, report, embed_covers=False)
        csv = (folder / "Demo.csv").read_text("utf-8-sig")
        self.assertIn("123456789", csv)
        self.assertIn(ONE, csv)
        html = (folder / "monitor.html").read_text("utf-8")
        self.assertNotIn("</script><img", html)
        self.assertIn("2020-01-01", html)
        self.assertIn('id="metric"', html)
        self.assertIn('id="day"', html)
        self.assertNotIn('src="https://', html)
        workbook = json.loads((folder / "workbook_configs.json").read_text("utf-8"))
        self.assertEqual(workbook[0]["rows"][0]["today_views"], 123456789)

    def test_emitted_csv_neutralizes_formula_strings_without_changing_numbers_or_source_data(self):
        dangerous = [whitespace + prefix + "1+1"
                     for whitespace in ("", " ", "\t", "\r", "\n", "\r\n", "\v", "\f", " \t\r\n")
                     for prefix in ("=", "+", "-", "@")]
        records = [Record(f"{100 + index:019d}", value, 5, metadata={"description_zh": value, "description_en": value})
                   for index, value in enumerate(dangerous)]
        records.append(Record(ONE, "Ordinary title", 0, metadata={"description_en": "  Ordinary text"}))
        self.store.commit_batch([Snapshot("csv-safety", "demo", "2020-01-03T04:00:00Z", "synthetic", tuple(records))])
        report = build_report(self.store, self.config, "2020-01-03", "display")
        folder = export_report(self.store, self.config, report, embed_covers=False)
        with (folder / "Demo.csv").open("r", encoding="utf-8-sig", newline="") as stream:
            emitted = list(csv.reader(stream))
        headers = emitted[0]
        csv_rows = {row[headers.index("id")]: dict(zip(headers, row)) for row in emitted[1:]}
        self.assertEqual(len(emitted) - 1, len(records))
        merged = json.loads((folder / "merged.json").read_text("utf-8"))
        json_rows = {row["id"]: row for row in merged["rows"]}
        html = (folder / "monitor.html").read_text("utf-8")
        html_data = json.loads(html.split('<script id="data" type="application/json">', 1)[1].split("</script>", 1)[0])
        html_rows = {row["id"]: row for row in html_data["days"]["2020-01-03"]["display"]["rows"]}
        for index, original in enumerate(dangerous):
            drama_id = f"{100 + index:019d}"
            for field in ("title", "description_zh", "description_en"):
                self.assertEqual(csv_rows[drama_id][field], "'" + original)
                self.assertEqual(json_rows[drama_id][field], original)
                self.assertEqual(html_rows[drama_id][field], original)
        numeric = csv_rows[ONE]
        self.assertEqual(numeric["today_views"], "0")
        self.assertEqual(numeric["today_from_initial_delta"], "-100")
        self.assertEqual(numeric["today_from_initial_rate"], "-1.0")
        self.assertEqual(numeric["title"], "Ordinary title")
        self.assertEqual(numeric["description_en"], "  Ordinary text")
        self.assertIsInstance(json_rows[ONE]["today_from_initial_delta"], int)
        self.assertIsInstance(json_rows[ONE]["today_from_initial_rate"], float)


class HttpTests(LocalCase):
    def setUp(self):
        super().setUp()
        self.spec_path = self.root / "request.local.json"
        self.spec = {"url": "https://www.tiktok.com/api/synthetic?cursor=0&private_token=SECRET", "headers": {"Cookie": "SECRET"}, "retries": 2, "max_pages": 3}
        write_json(self.spec_path, self.spec)

    def response(self, items=None, more=False, cursor="1", status=200, content_type="application/json", body=None, url=None):
        value = {"status_code": 0, "dramaList": items if items is not None else [{"dramaID": ONE, "dramaName": "Synthetic", "numWatched": "12345"}], "hasMore": more, "cursor": cursor}
        return HttpResponse(status, {"Content-Type": content_type}, body if body is not None else json.dumps(value).encode(), url or self.spec["url"])

    def transport(self, sequence):
        self.calls = []
        def invoke(url, headers, timeout):
            self.calls.append((url, headers, timeout))
            result = sequence.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return invoke

    def test_collect_all_pages_and_never_infer_display_from_frontend(self):
        sequence = [self.response(more=True), self.response(items=[{"dramaID": TWO, "dramaName": "Two", "numWatched": "9", "viewsRaw": "10"}], cursor="2")]
        snapshot = collect_http("demo", self.spec_path, self.transport(sequence), self.root / "raw")
        self.assertEqual(len(snapshot.records), 2)
        self.assertIsNone(snapshot.records[0].display_views)
        self.assertEqual(snapshot.records[0].frontend_integer, 12345)
        self.assertEqual(snapshot.records[1].display_views, 10)
        self.assertIn("cursor=1", self.calls[-1][0])
        self.assertEqual(len(list((self.root / "raw").glob("*.body"))), 2)

    def test_preserves_opaque_signed_query_encoding(self):
        self.spec["url"] = "https://www.tiktok.com/api/synthetic?opaque=a%20b+%2f%2F&cursor=0&signature=A%3d%3D"
        write_json(self.spec_path, self.spec)
        sequence = [self.response(more=True), self.response(items=[{"dramaID": TWO, "dramaName": "Two", "numWatched": "9"}], cursor="2")]
        collect_http("demo", self.spec_path, self.transport(sequence))
        self.assertEqual(self.calls[0][0], self.spec["url"])
        self.assertEqual(self.calls[1][0], self.spec["url"].replace("cursor=0", "cursor=1"))

    def test_duplicate_ids_in_later_page_fail_without_commit(self):
        sequence = [self.response(more=True), self.response(cursor="2")]
        with self.assertRaisesRegex(MonitorError, "Duplicate drama ID"):
            snapshot = collect_http("demo", self.spec_path, self.transport(sequence))
            self.store.commit_batch([snapshot])
        self.assertEqual(self.store.status()["snapshots"], 0)

    def test_stalled_and_backward_cursors_fail(self):
        for cursor in ("0", "-1"):
            if cursor == "-1":
                sequence = [self.response(more=True, cursor="2"), self.response(items=[{"dramaID": TWO, "dramaName": "Two", "numWatched": "5"}], more=True, cursor="1")]
            else:
                sequence = [self.response(more=True, cursor=cursor)]
            with self.assertRaisesRegex(MonitorError, "cursor"):
                collect_http("demo", self.spec_path, self.transport(sequence))

    def test_negative_and_signed_nonforward_numeric_cursors_fail_without_commit(self):
        for initial, following in (("0", -1), ("0", "-1"), ("0", "+0"), ("0", " -0 "), ("2", "+1"), ("+2", "2"), ("opaque-A", "-1")):
            with self.subTest(initial=initial, following=following):
                self.spec["initial_cursor"] = initial
                write_json(self.spec_path, self.spec)
                sequence = [self.response(more=True, cursor=following), self.response(items=[{"dramaID": TWO, "dramaName": "Two", "numWatched": "9"}], cursor="2")]
                with self.assertRaisesRegex(MonitorError, "cursor"):
                    snapshot = collect_http("demo", self.spec_path, self.transport(sequence))
                    self.store.commit_batch([snapshot])
                self.assertEqual(self.store.status()["snapshots"], 0)

    def test_forward_signed_numeric_and_opaque_cursors_remain_supported(self):
        for following in ("+1", "opaque-A", "token-1"):
            with self.subTest(following=following):
                sequence = [self.response(more=True, cursor=following), self.response(items=[{"dramaID": TWO, "dramaName": "Two", "numWatched": "9"}], cursor="2")]
                result = collect_http("demo", self.spec_path, self.transport(sequence))
                self.assertEqual(len(result.records), 2)

    def test_opaque_cursor_repetition_and_negative_terminal_cursor_fail(self):
        second = {"dramaID": TWO, "dramaName": "Two", "numWatched": "9"}
        sequence = [self.response(more=True, cursor="opaque-A"), self.response(items=[second], more=True, cursor="opaque-A")]
        with self.assertRaisesRegex(MonitorError, "cursor"):
            collect_http("demo", self.spec_path, self.transport(sequence))
        with self.assertRaisesRegex(MonitorError, "negative"):
            collect_http("demo", self.spec_path, self.transport([self.response(cursor="-1")]))
        self.spec["initial_cursor"] = "-1"
        write_json(self.spec_path, self.spec)
        transport = self.transport([])
        with self.assertRaisesRegex(MonitorError, "negative"):
            collect_http("demo", self.spec_path, transport)
        self.assertEqual(self.calls, [])

    def test_auth_fails_without_retry_and_private_values_are_not_in_errors(self):
        for status in (401, 403):
            with self.assertRaisesRegex(MonitorError, "authentication failed") as raised:
                collect_http("demo", self.spec_path, self.transport([self.response(status=status)]))
            self.assertEqual(len(self.calls), 1)
            self.assertNotIn("SECRET", str(raised.exception))

    def test_non_json_html_and_invalid_json_fail(self):
        for response in (self.response(content_type="text/html", body=b"<html>Login</html>"), self.response(body=b"{broken")):
            with self.assertRaisesRegex(MonitorError, "JSON"):
                collect_http("demo", self.spec_path, self.transport([response]))

    def test_timeout_5xx_and_429_retries_are_bounded(self):
        sleeps = []
        throttled = self.response(status=429)
        throttled.headers["Retry-After"] = "99999"
        result = collect_http("demo", self.spec_path, self.transport([TimeoutError("SECRET"), throttled, self.response()]), sleep=sleeps.append)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(sleeps, [1, 30])
        self.assertEqual(len(result.records), 1)
        with self.assertRaisesRegex(MonitorError, "bounded retries"):
            collect_http("demo", self.spec_path, self.transport([self.response(status=503) for _ in range(3)]), sleep=lambda _: None)
        self.assertEqual(len(self.calls), 3)

    def test_network_error_text_never_leaks(self):
        with self.assertRaises(MonitorError) as raised:
            collect_http("demo", self.spec_path, self.transport([TimeoutError("Cookie=SECRET") for _ in range(3)]), sleep=lambda _: None)
        self.assertNotIn("SECRET", str(raised.exception))

    def test_empty_page_and_api_error_fail(self):
        invalid = self.response(body=json.dumps({"status_code": 7, "dramaList": [], "cursor": "0", "hasMore": False}).encode())
        for response in (self.response(items=[]), self.response(items=[], more=True), invalid):
            with self.assertRaises(MonitorError):
                collect_http("demo", self.spec_path, self.transport([response]))

    def test_maximum_pages_requires_terminal_response(self):
        self.spec["max_pages"] = 1
        write_json(self.spec_path, self.spec)
        with self.assertRaisesRegex(MonitorError, "Maximum pagination"):
            collect_http("demo", self.spec_path, self.transport([self.response(more=True)]))

    def test_host_policy_and_redirect_response_reject_non_tiktok(self):
        for url in ("http://www.tiktok.com/api", "https://tiktok.com.evil.invalid/", "https://127.0.0.1/", "https://user:pass@tiktok.com/", "https://tiktok.com:444/", "https://tiktok.com/#frag"):
            with self.assertRaises(MonitorError):
                validate_url(url)
        self.assertEqual(validate_url("https://www.tiktok.com/api"), "www.tiktok.com")
        with self.assertRaisesRegex(MonitorError, "hostname"):
            collect_http("demo", self.spec_path, self.transport([self.response(url="https://m.tiktok.com/api")]))

    def test_nested_response_field_mapping(self):
        self.spec["fields"] = {"items": "data.items", "has_more": "data.more", "cursor": "data.next"}
        self.spec["record_fields"] = {"id": "drama.id", "title": "drama.title", "frontend_integer": "count"}
        write_json(self.spec_path, self.spec)
        body = json.dumps({"data": {"items": [{"drama": {"id": ONE, "title": "One"}, "count": 5}], "more": False, "next": "1"}}).encode()
        result = collect_http("demo", self.spec_path, self.transport([self.response(body=body)]))
        self.assertEqual(result.records[0].frontend_integer, 5)

    def drama_items(self, first, count):
        return [{"dramaID": f"{index:019d}", "dramaName": "Synthetic " + str(index), "numWatched": "5"}
                for index in range(first, first + count)]

    def test_tiktok_drama_list_requires_zero_start_before_request(self):
        for captured, override in (("20", None), ("0", "20"), ("0", "opaque-A")):
            with self.subTest(captured=captured, override=override):
                self.spec["url"] = "https://www.tiktok.com/api/drama/user/drama_list/?cursor=" + captured
                self.spec.pop("initial_cursor", None)
                if override is not None:
                    self.spec["initial_cursor"] = override
                write_json(self.spec_path, self.spec)
                transport = self.transport([self.response()])
                with self.assertRaisesRegex(MonitorError, "start.*zero"):
                    collect_http("demo", self.spec_path, transport)
                self.assertEqual(self.calls, [])

    def test_tiktok_drama_list_offsets_match_actual_page_counts(self):
        self.spec["url"] = "https://www.tiktok.com/api/drama/user/drama_list/?cursor=0"
        write_json(self.spec_path, self.spec)
        sequence = [self.response(items=self.drama_items(1, 20), more=True, cursor="20"),
                    self.response(items=self.drama_items(21, 3), cursor="23")]
        snapshot = collect_http("demo", self.spec_path, self.transport(sequence))
        self.assertEqual(len(snapshot.records), 23)
        self.assertIn("cursor=20", self.calls[1][0])

    def test_tiktok_id_only_references_resolve_to_one_complete_record(self):
        self.spec["url"] = "https://www.tiktok.com/api/drama/user/drama_list/?cursor=0"
        write_json(self.spec_path, self.spec)
        full = {"dramaID": ONE, "dramaName": "Synthetic complete drama", "numWatched": "12345"}
        sequence = [self.response(items=[{"dramaID": ONE}, full], cursor="2")]
        result = collect_http("demo", self.spec_path, self.transport(sequence))
        self.assertEqual(len(result.records), 1)
        self.assertEqual((result.records[0].title, result.records[0].frontend_integer), (full["dramaName"], 12345))

    def test_tiktok_reference_resolution_keeps_raw_pagination_offsets(self):
        self.spec["url"] = "https://www.tiktok.com/api/drama/user/drama_list/?cursor=0"
        write_json(self.spec_path, self.spec)
        sequence = [self.response(items=[{"dramaID": TWO}, *self.drama_items(1, 1)], more=True, cursor="2"),
                    self.response(items=self.drama_items(2, 1), cursor="3")]
        result = collect_http("demo", self.spec_path, self.transport(sequence))
        self.assertEqual([record.id for record in result.records], [ONE, TWO])
        self.assertIn("cursor=2", self.calls[1][0])

    def test_tiktok_unresolved_reference_cannot_be_silently_dropped(self):
        self.spec["url"] = "https://www.tiktok.com/api/drama/user/drama_list/?cursor=0"
        write_json(self.spec_path, self.spec)
        sequence = [self.response(items=[{"dramaID": TWO}, *self.drama_items(1, 1)], cursor="2")]
        with self.assertRaisesRegex(MonitorError, "Unresolved.*reference"):
            result = collect_http("demo", self.spec_path, self.transport(sequence))
            self.store.commit_batch([result])
        self.assertEqual(self.store.status()["snapshots"], 0)

    def test_tiktok_complete_duplicates_still_fail_even_with_references(self):
        self.spec["url"] = "https://www.tiktok.com/api/drama/user/drama_list/?cursor=0"
        write_json(self.spec_path, self.spec)
        first = {"dramaID": ONE, "dramaName": "First title", "numWatched": "9"}
        second = {"dramaID": ONE, "dramaName": "Conflicting title", "numWatched": "10"}
        sequence = [self.response(items=[{"dramaID": ONE}, first, second], cursor="3")]
        with self.assertRaisesRegex(MonitorError, "Duplicate drama ID"):
            collect_http("demo", self.spec_path, self.transport(sequence))

    def test_only_exact_tiktok_id_references_are_resolved(self):
        for item in ({"dramaID": 1}, {"dramaID": "invalid"}, {"dramaID": ONE, "dramaName": "No count"}):
            with self.subTest(item=item):
                self.spec["url"] = "https://www.tiktok.com/api/drama/user/drama_list/?cursor=0"
                write_json(self.spec_path, self.spec)
                with self.assertRaises(MonitorError):
                    collect_http("demo", self.spec_path, self.transport([self.response(items=[item], cursor="1")]))

    def test_tiktok_drama_list_cannot_override_its_actual_cursor_parameter(self):
        self.spec.update(url="https://www.tiktok.com/api/drama/user/drama_list/?cursor=20", initial_cursor="0", cursor_param="page")
        write_json(self.spec_path, self.spec)
        transport = self.transport([])
        with self.assertRaisesRegex(MonitorError, "actual cursor"):
            collect_http("demo", self.spec_path, transport)
        self.assertEqual(self.calls, [])

    def test_tiktok_drama_list_skipped_overlapping_or_opaque_offsets_cannot_commit(self):
        self.spec["url"] = "https://www.tiktok.com/api/drama/user/drama_list/?cursor=0"
        write_json(self.spec_path, self.spec)
        for first, final in (("999", "23"), ("19", "23"), ("20", "24"), ("20", "22"), ("opaque-A", "23")):
            with self.subTest(first=first, final=final):
                sequence = [self.response(items=self.drama_items(1, 20), more=True, cursor=first),
                            self.response(items=self.drama_items(21, 3), cursor=final)]
                with self.assertRaisesRegex(MonitorError, "offset"):
                    snapshot = collect_http("demo", self.spec_path, self.transport(sequence))
                    self.store.commit_batch([snapshot])
                self.assertEqual(self.store.status()["snapshots"], 0)

    def test_cover_urls_and_theme_values_are_preserved_without_downloads(self):
        zoom_url = "https://cdn.invalid/zoom-240.png?synthetic=1"
        cover_url = "https://cdn.invalid/cover-240.png?synthetic=2"
        fallback_url = "https://cdn.invalid/cover-list.png?synthetic=3"
        for zoom, cover, expected in (({"240": zoom_url, "480": "unused"}, {"240": cover_url}, zoom_url),
                                      ({"480": "unused"}, {"240": cover_url}, cover_url),
                                      ({}, {"urlList": [fallback_url]}, fallback_url)):
            with self.subTest(expected=expected):
                item = {"dramaID": ONE, "dramaName": "Synthetic", "numWatched": "5", "zoomCover": zoom, "cover": cover,
                        "themes": [{"tagID": "synthetic-id", "tagKey": "synthetic-key", "tagVal": "Family"}]}
                snapshot = collect_http("demo", self.spec_path, self.transport([self.response(items=[item])]))
                self.assertEqual(snapshot.records[0].metadata["thumbnail_url"], expected)
                self.assertEqual(snapshot.records[0].metadata["platform_themes"], ["Family"])


class CheckpointAndCliTests(LocalCase):
    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = main(["--config", str(self.config_path), *args])
        return result, out.getvalue(), err.getvalue()

    def source_config(self, accounts):
        write_json(self.config_path, {"state_dir": "state", "accounts": accounts})
        self.config = load_config(self.config_path)

    def test_cli_history_report_and_missing_day_exit_codes(self):
        source = self.fixture_history()
        self.assertEqual(self.cli("import-history", "--source", str(source))[0], 0)
        code, out, err = self.cli("report", "--day", "2020-01-03", "--metric", "display", "--no-covers")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["summary"]["today_total"], 20)
        self.assertEqual(self.cli("report", "--day", "2020-01-02", "--metric", "display")[0], 2)
        self.assertEqual(self.cli("status")[0], 0)

    def test_stale_checkpoint_fails_and_records_failed_attempt(self):
        write_json(self.root / "checkpoint.json", snap("2020-01-01T00:00:00Z", 2).as_dict())
        self.source_config([{"handle": "demo", "source": {"type": "checkpoint", "path": "checkpoint.json"}}])
        code, _, err = self.cli("collect")
        self.assertEqual(code, 2)
        self.assertIn("stale", err)
        self.assertEqual(self.store.status()["snapshots"], 0)
        self.assertEqual(self.store.status()["recent_attempts"][0]["status"], "failed")

    def test_partial_account_failure_cannot_commit_first_account(self):
        captured = iso_time(now_utc() - timedelta(seconds=1))
        write_json(self.root / "fresh.json", snap(captured, 2).as_dict())
        write_json(self.root / "stale.json", snap("2020-01-01T00:00:00Z", 3, account="other").as_dict())
        self.source_config([{"handle": "demo", "source": {"type": "checkpoint", "path": "fresh.json"}}, {"handle": "other", "source": {"type": "checkpoint", "path": "stale.json"}}])
        self.assertEqual(self.cli("collect")[0], 2)
        self.assertEqual(self.store.status()["snapshots"], 0)

    def test_interrupted_collection_records_failure(self):
        with patch("tkmonitor.cli.collect_external", side_effect=KeyboardInterrupt):
            self.assertEqual(self.cli("collect")[0], 130)
        self.assertEqual(self.store.status()["snapshots"], 0)
        self.assertEqual(self.store.status()["recent_attempts"][0]["status"], "failed")

    def test_export_failure_does_not_roll_back_valid_collection(self):
        captured = iso_time(now_utc() - timedelta(seconds=1))
        write_json(self.root / "fresh.json", snap(captured, 2).as_dict())
        self.source_config([{"handle": "demo", "source": {"type": "checkpoint", "path": "fresh.json"}}])
        with patch("tkmonitor.cli.export_report", side_effect=MonitorError("Synthetic export failure")):
            self.assertEqual(self.cli("collect", "--report")[0], 2)
        self.assertEqual(self.store.status()["snapshots"], 1)
        self.assertEqual(self.store.status()["recent_attempts"][0]["status"], "valid")

    def test_report_with_zero_selected_metric_coverage_exits_nonzero_and_keeps_reviewable_outputs(self):
        import_history(self.store, self.config, self.fixture_history())
        code, out, _ = self.cli("report", "--day", "2020-01-01", "--metric", "frontend", "--no-covers")
        self.assertEqual(code, 3)
        result = json.loads(out)
        self.assertFalse(result["complete"])
        self.assertEqual(result["metric_coverage"]["known"], 0)
        self.assertTrue(result["warnings"])
        saved = json.loads((self.config.report_dir / "2020-01-01/frontend/merged.json").read_text("utf-8"))
        self.assertIsNone(saved["rows"][0]["today_views"])
        html = (self.config.report_dir / "2020-01-01/frontend/monitor.html").read_text("utf-8")
        self.assertIn("所选指标没有已保存的播放量", html)

    def test_daily_collect_defaults_to_frontend_and_wrong_metric_does_not_rollback_collection(self):
        captured = iso_time(now_utc() - timedelta(seconds=1))
        write_json(self.root / "fresh.json", snap(captured, None, precise=4).as_dict())
        self.source_config([{"handle": "demo", "source": {"type": "checkpoint", "path": "fresh.json"}}])
        code, _, _ = self.cli("collect", "--report")
        self.assertEqual(code, 0)
        report_dir = self.config.report_dir / local_day(captured)
        precise = json.loads((report_dir / "frontend/merged.json").read_text("utf-8"))
        self.assertTrue(precise["complete"])
        self.assertEqual(precise["summary"]["today_total"], 4)
        code, out, _ = self.cli("collect", "--report", "--metric", "display")
        self.assertEqual(code, 3)
        self.assertIn('"status": "missing"', out)
        display = json.loads((report_dir / "display/merged.json").read_text("utf-8"))
        self.assertIsNone(display["summary"]["today_total"])
        self.assertEqual(self.store.status()["snapshots"], 1)
        self.assertEqual(self.store.status()["recent_attempts"][0]["status"], "valid")

    def test_external_command_must_rewrite_checkpoint_freshly(self):
        captured = iso_time(now_utc() - timedelta(seconds=10))
        write_json(self.root / "fresh.json", snap(captured, 2).as_dict())
        account = {"handle": "demo", "source": {"type": "browser", "path": "fresh.json", "command": [sys.executable, "-c", "pass"]}}
        with self.assertRaisesRegex(MonitorError, "fresh checkpoint"):
            collect_external(self.config, account, local_day(captured), self.root / "raw")

    def test_legacy_checkpoint_validates_terminal_page_visible_cards_and_precise_metric(self):
        at = "2020-01-01T00:00:00Z"
        checkpoint = {"run": {"account": "demo", "start_at": at, "last_read_at": at, "pages": [{"observed_at": at, "response": {
            "status_code": 0, "hasMore": False, "cursor": "1", "dramaList": [{"dramaID": ONE, "dramaName": "Synthetic", "numWatched": "1234"}]
        }}]}, "cards": [{"id": ONE, "text": "Synthetic\n1.2K\n4 Episodes"}]}
        path = self.root / "legacy.json"
        write_json(path, checkpoint)
        result = collect_checkpoint(path, "demo", "2020-01-01")
        self.assertEqual((result.records[0].display_views, result.records[0].frontend_integer), (1200, 1234))
        checkpoint["run"]["pages"][0]["response"]["cursor"] = "999"
        write_json(path, checkpoint)
        with self.assertRaisesRegex(MonitorError, "offset"):
            collect_checkpoint(path, "demo", "2020-01-01")
        checkpoint["run"]["pages"][0]["response"]["cursor"] = "1"
        checkpoint["run"]["pages"][0]["response"]["hasMore"] = True
        write_json(path, checkpoint)
        with self.assertRaisesRegex(MonitorError, "incomplete"):
            collect_checkpoint(path, "demo", "2020-01-01")

    def test_ids_must_stay_strings_and_missing_does_not_validate_as_zero(self):
        with self.assertRaisesRegex(MonitorError, "19-digit strings"):
            self.store.commit_batch([snap("2020-01-01T00:00:00Z", 5, drama_id=1)])
        with self.assertRaisesRegex(MonitorError, "neither"):
            self.store.commit_batch([snap("2020-01-01T00:00:00Z", None)])
        self.assertEqual(parse_display("0"), 0)


if __name__ == "__main__":
    unittest.main()
