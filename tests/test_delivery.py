"""Offline regressions for the portable daily completion and delivery workflow."""

from io import BytesIO
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
import zipfile

from test_monitor import LocalCase, ONE
from tkmonitor.analysis import build_report
from tkmonitor.cli import export_xlsx
from tkmonitor.delivery import FIELDS, TOTALS, package_report, verify_workbook, workbook_name
from tkmonitor.media import HISTORICAL, PENDING, complete_covers, cover_host, finalize_report
from tkmonitor.model import MonitorError, Record, Snapshot
from tkmonitor.reports import export_report


def saved_book(path, config, changes=None, images=None):
    """A minimal saved spreadsheet fixture with independently specified cached values."""
    values = {}
    for number, row in enumerate(config["rows"], 8):
        values.update({"X" + str(number): row["id"], "D" + str(number): row["title"]})
        values.update({col + str(number): row.get(field) for col, field in FIELDS.items()})
    values.update({col + str(len(config["rows"]) + 8): config["summary"][field] for col, field in TOTALS.items()})
    values.update(changes or {})
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    sheet = ET.Element(ns + "worksheet")
    row = ET.SubElement(ET.SubElement(sheet, ns + "sheetData"), ns + "row", r="8")
    for address, value in values.items():
        if value is None:
            continue
        cell = ET.SubElement(row, ns + "c", r=address)
        if isinstance(value, str):
            cell.set("t", "inlineStr")
            ET.SubElement(ET.SubElement(cell, ns + "is"), ns + "t").text = value
        else:
            ET.SubElement(cell, ns + "f").text = "1+1"
            ET.SubElement(cell, ns + "v").text = str(value)
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("xl/worksheets/sheet1.xml", ET.tostring(sheet))
        count = sum(bool(item.get("image")) for item in config["rows"]) if images is None else images
        for number in range(count):
            book.writestr("xl/media/image" + str(number) + ".png", b"synthetic image")


class SavedWorkbookTests(LocalCase):
    def configuration(self):
        self.store.commit_batch([
            Snapshot("old", "demo", "2020-01-01T00:00:00Z", "synthetic", (Record(ONE, "Title", None, 100),)),
            Snapshot("new", "demo", "2020-01-02T00:00:00Z", "synthetic", (Record(ONE, "Title", None, 110),))])
        return build_report(self.store, self.config, "2020-01-02", "frontend")["accounts"][0]

    def test_checks_saved_formula_values_precise_ids_and_images(self):
        config = self.configuration()
        path = self.root / "saved.xlsx"
        saved_book(path, config)
        self.assertTrue(verify_workbook(path, config)["formula_values_verified"])
        for changes in ({"X8": int(ONE)}, {"H8": 11}, {"I9": .11}, {"F8": None}):
            with self.subTest(changes=changes):
                saved_book(path, config, changes)
                with self.assertRaisesRegex(MonitorError, "mismatch"):
                    verify_workbook(path, config)
        saved_book(path, config, images=1)
        with self.assertRaisesRegex(MonitorError, "image count"):
            verify_workbook(path, config)

    def test_rejects_unsafe_filename_and_malformed_workbook(self):
        with self.assertRaises(MonitorError):
            workbook_name({"prefix": "../outside", "rows": []})
        config = self.configuration()
        path = self.root / "saved.xlsx"
        path.write_bytes(b"not a zip")
        with self.assertRaisesRegex(MonitorError, "structure"):
            verify_workbook(path, config)

    def test_account_exports_use_separate_bounded_processes_and_verify_saved_files(self):
        config = self.configuration()
        second = {**config, "prefix": "Second", "name": "Second"}
        folder = self.root / "exports"
        folder.mkdir()
        (folder / "workbook_configs.json").write_text(json.dumps([config, second]), encoding="utf-8")
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            staging = Path(argv[-1])
            accounts = json.loads((staging / "workbook_configs.json").read_text("utf-8"))
            self.assertEqual(len(accounts), 1)
            saved_book(staging / workbook_name(accounts[0]), accounts[0])
            (staging / "export_manifest.json").write_text(json.dumps([{"account": accounts[0]["name"]}]))
            return SimpleNamespace(returncode=0)

        with patch("tkmonitor.cli.subprocess.run", side_effect=run):
            result = export_xlsx(self.config, folder)
        self.assertEqual(len(result["workbooks"]), 2)
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertIn("--no-opt", call)
            self.assertIn("--max-old-space-size=384", call)
        self.assertFalse(list(folder.glob(".xlsx-*")))
        self.assertEqual(len(json.loads((folder / "export_manifest.json").read_text("utf-8"))), 2)


@unittest.skipUnless(importlib.util.find_spec("PIL"), "Optional Pillow is not installed")
class FinalizationTests(LocalCase):
    def setup_snapshot(self, day="2020-01-02", english="Current original", chinese=None, cover=True):
        metadata = {"source_title": "Synthetic", "description_en": english, "title_zh": "测试",
                    "thumbnail_url": "https://example.tiktokcdn.com/cover?signature=PRIVATE"}
        if chinese:
            metadata["description_zh"] = chinese
        if cover:
            from PIL import Image
            path = self.config.state_dir / "assets" / "cover.png"
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (2, 2)).save(path)
            metadata["thumbnail_local"] = "assets/cover.png"
        self.store.commit_batch([Snapshot(day, "demo", day + "T00:00:00Z", "synthetic",
                                         (Record(ONE, "Synthetic", None, 123456789, metadata=metadata),))])

    def test_complete_media_keeps_values_and_fingerprints_and_embeds_source_synopsis(self):
        self.setup_snapshot(chinese="已核对简介")
        snapshots = [(item.id, item.digest) for item in self.store.snapshots()]
        folder, report = finalize_report(self.store, self.config, "2020-01-02", download=False)
        self.assertEqual(report["media_coverage"]["current_chinese"], 1)
        self.assertEqual(report["rows"][0]["today_views"], 123456789)
        self.assertEqual(snapshots, [(item.id, item.digest) for item in self.store.snapshots()])
        embedded = json.loads((folder / "monitor.html").read_text("utf-8").split('<script id="data" type="application/json">')[1].split('</script>')[0])
        self.assertEqual(len(embedded["covers"]), 1)
        row = embedded["days"]["2020-01-02"]["frontend"]["rows"][0]
        self.assertEqual(row["description_zh"], "已核对简介")
        self.assertEqual(row["description_en"], "Current original")

    def test_changed_english_uses_labeled_historical_reference_without_promoting_it(self):
        self.setup_snapshot("2020-01-01", "Old source", "旧中文简介")
        finalize_report(self.store, self.config, "2020-01-01", download=False)
        self.setup_snapshot("2020-01-02", "Changed source")
        folder, report = finalize_report(self.store, self.config, "2020-01-02", download=False)
        self.assertEqual(report["rows"][0]["description_zh"], HISTORICAL + "旧中文简介")
        self.assertEqual(report["media_coverage"]["historical_chinese"], 1)
        self.assertNotIn("description_zh", self.store.metadata_at("demo", ONE, "2020-01-02T00:00:00Z"))
        _, repeated = finalize_report(self.store, self.config, "2020-01-02", download=False)
        self.assertEqual(repeated["rows"][0]["description_zh"], report["rows"][0]["description_zh"])

    def test_pending_english_is_never_reclassified_as_historical_chinese(self):
        self.setup_snapshot("2020-01-01")
        finalize_report(self.store, self.config, "2020-01-01", download=False)
        self.setup_snapshot("2020-01-02")
        _, report = finalize_report(self.store, self.config, "2020-01-02", download=False)
        self.assertEqual(report["media_coverage"]["english_only"], 1)
        self.assertEqual(report["media_coverage"]["historical_chinese"], 0)
        self.assertTrue(report["rows"][0]["description_zh"].startswith(PENDING))

    def test_future_report_does_not_fill_earlier_chinese(self):
        self.setup_snapshot("2020-01-02", chinese="Future Chinese")
        finalize_report(self.store, self.config, "2020-01-02", download=False)
        self.setup_snapshot("2020-01-01")
        _, report = finalize_report(self.store, self.config, "2020-01-01", download=False)
        self.assertEqual(report["media_coverage"]["english_only"], 1)

    def test_missing_cover_fails_with_reviewable_coverage_and_keeps_valid_snapshot(self):
        self.setup_snapshot(cover=False)
        with self.assertRaisesRegex(MonitorError, "incomplete"):
            finalize_report(self.store, self.config, "2020-01-02", download=False)
        check = json.loads((self.config.report_dir / "2020-01-02/frontend/media_validation.json").read_text("utf-8"))
        self.assertEqual(check["status"], "incomplete")
        self.assertEqual(check["coverage"]["covers"], 0)
        self.assertEqual(len(self.store.snapshots()), 1)

    def test_download_retries_are_bounded_private_errors_redacted_and_images_decoded(self):
        self.setup_snapshot(cover=False)
        report = build_report(self.store, self.config, "2020-01-02", "frontend")
        attempts = []

        def failing(url):
            attempts.append(url)
            raise OSError("signature=PRIVATE")

        result = complete_covers(self.store, report, transport=failing, sleep=lambda _: None)
        self.assertEqual(len(attempts), 3)
        self.assertNotIn("PRIVATE", json.dumps(result))
        self.assertEqual(result[0]["status"], "unavailable")
        from PIL import Image
        content = BytesIO()
        Image.new("RGB", (2, 2)).save(content, format="PNG")
        result = complete_covers(self.store, report, transport=lambda _: content.getvalue(), sleep=lambda _: None)
        self.assertEqual(result[0]["status"], "downloaded")
        _, completed = finalize_report(self.store, self.config, "2020-01-02", download=False)
        self.assertEqual(completed["media_coverage"]["covers"], 1)

    def test_invalid_image_bytes_are_not_marked_as_complete(self):
        self.setup_snapshot(cover=False)
        report = build_report(self.store, self.config, "2020-01-02", "frontend")
        result = complete_covers(self.store, report, transport=lambda _: b"not an image", sleep=lambda _: None)
        self.assertEqual(result[0]["status"], "unavailable")
        self.assertNotIn("thumbnail_local", self.store.metadata_at("demo", ONE, "2020-01-02T00:00:00Z"))

    def test_package_verifies_all_files_before_copying_and_rejects_stale_values(self):
        self.setup_snapshot()
        folder, report = finalize_report(self.store, self.config, "2020-01-02", download=False)
        config = report["accounts"][0]
        path = folder / workbook_name(config)
        saved_book(path, config, {"G8": 1})
        destination = self.root / "delivery"
        with self.assertRaises(MonitorError):
            package_report(folder, destination)
        self.assertFalse(destination.exists())
        saved_book(path, config)
        result = package_report(folder, destination)
        self.assertEqual(result["workbooks"][0]["images"], 1)
        self.assertTrue((destination / "monitor.html").is_file())
        self.assertTrue((destination / "workbooks_verified.json").is_file())


class PublicationSafetyTests(LocalCase):
    def test_thumbnail_host_policy(self):
        self.assertEqual(cover_host("https://p16.tiktokcdn.com/image?signature=private"), "p16.tiktokcdn.com")
        for url in ("http://p16.tiktokcdn.com/", "https://tiktokcdn.com.evil.invalid/", "https://127.0.0.1/",
                    "https://user:password@p16.tiktokcdn.com/", "https://p16.tiktokcdn.com:444/", "https://p16.tiktokcdn.com/#fragment"):
            with self.subTest(url=url), self.assertRaises(MonitorError):
                cover_host(url)

    def test_interrupted_stream_preserves_existing_html_and_removes_temporary_file(self):
        self.store.commit_batch([Snapshot("one", "demo", "2020-01-01T00:00:00Z", "synthetic", (Record(ONE, "Title", 1),))])
        report = build_report(self.store, self.config, "2020-01-01", "display")
        folder = export_report(self.store, self.config, report)
        original = (folder / "monitor.html").read_bytes()

        def interrupted(*args, **kwargs):
            yield "partial json"
            raise OSError("Synthetic interrupted write")

        with patch("tkmonitor.reports.json.JSONEncoder.iterencode", side_effect=interrupted):
            with self.assertRaises(OSError):
                export_report(self.store, self.config, report)
        self.assertEqual((folder / "monitor.html").read_bytes(), original)
        self.assertFalse(list(folder.glob(".monitor-*.tmp")))
