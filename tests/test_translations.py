"""Original-text delivery, explicit translation, and source-bound reuse."""

import contextlib
import copy
import io
import json
from pathlib import Path
from unittest.mock import patch

from test_monitor import LocalCase, ONE, TWO, write_json
from tkmonitor.analysis import build_report
from tkmonitor.cli import main
from tkmonitor.model import MonitorError, Record, Snapshot
from tkmonitor.translations import import_translations, translation_tasks


class TranslationTests(LocalCase):
    def seed(self, day="2020-01-01", description="A girl saves her father.", translated=False):
        metadata = {"source_title": "A Little Hero", "description_en": description}
        if translated:
            metadata.update(title_zh="小小英雄", description_zh="一个女孩救下了自己的父亲。", genre="亲情")
        snapshot = Snapshot(day, "demo", day + "T00:00:00Z", "synthetic",
                            (Record(ONE, "A Little Hero", None, 1234567, metadata=metadata),))
        self.store.commit_batch([snapshot])
        return snapshot

    def invoke(self, *args):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = main(["--config", str(self.config_path), *args])
        self.stdout, self.stderr = output.getvalue(), errors.getvalue()
        return code

    def completed(self, day="2020-01-01"):
        result = translation_tasks(self.store, self.config, day)
        path = Path(result["task"])
        task = json.loads(path.read_text(encoding="utf-8"))
        for item in task["records"]:
            item.update(title_zh="小小英雄", description_zh="一个女孩救下了自己的父亲。", genre="亲情")
        return path, task

    def immutable(self):
        return ([tuple(r) for r in self.store.connection.execute("SELECT id,fingerprint FROM snapshots ORDER BY id")],
                [tuple(r) for r in self.store.connection.execute("SELECT * FROM baselines ORDER BY account,drama_id,metric")])

    def saved_report(self):
        path = self.config.report_dir / "2020-01-01/frontend/merged.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def assert_original_report(self, report):
        self.assertIs(report["include_translations"], False)
        row = report["rows"][0]
        self.assertEqual((row["title"], row["description_en"]), ("A Little Hero", "A girl saves her father."))
        self.assertEqual(row["today_views"], 1234567)
        self.assertFalse(row.get("title_zh"))
        self.assertFalse(row.get("description_zh"))
        self.assertNotIn("待翻译", json.dumps(report, ensure_ascii=False))
        self.assertFalse((self.config.state_dir / "translation_tasks").exists())

    def test_report_default_and_compatibility_flag_deliver_original_text_without_translation(self):
        self.seed(translated=True)
        args = ("report", "--day", "2020-01-01", "--metric", "frontend", "--no-covers")
        with (
            patch("tkmonitor.translations.require_chinese", side_effect=AssertionError("Unexpected translation check")),
            patch("tkmonitor.translations.translation_tasks", side_effect=AssertionError("Unexpected translation task")),
        ):
            for flags in ((), ("--allow-untranslated",)):
                with self.subTest(flags=flags):
                    self.assertEqual(self.invoke(*args, *flags), 0, self.stderr)
                    self.assert_original_report(self.saved_report())

    def test_report_requires_chinese_only_when_explicitly_requested(self):
        self.seed()
        self.assertEqual(self.invoke("report", "--day", "2020-01-01", "--metric", "frontend",
                                     "--no-covers", "--translate"), 5, self.stderr)
        self.assertEqual(json.loads(self.stdout)["pending"], 1)
        self.assertFalse((self.config.report_dir / "2020-01-01/frontend/merged.json").exists())

    def test_default_collection_reports_originals_and_finalization_still_completes_covers(self):
        snapshot = self.seed()
        covers = [{"account": "demo", "id": ONE, "status": "existing"}]
        with (
            patch("tkmonitor.cli.collect_external", return_value=snapshot) as collect,
            patch("tkmonitor.translations.require_chinese", side_effect=AssertionError("Unexpected translation check")),
            patch("tkmonitor.translations.translation_tasks", side_effect=AssertionError("Unexpected translation task")),
            patch("tkmonitor.media.complete_covers", return_value=covers) as complete_covers,
        ):
            self.assertEqual(self.invoke("collect", "--day", "2020-01-01", "--report"), 0, self.stderr)
            self.assert_original_report(self.saved_report())
            self.assertEqual(self.invoke("finalize", "--day", "2020-01-01", "--no-download"), 0, self.stderr)
            report = self.saved_report()
            self.assert_original_report(report)
            self.assertEqual(report["media_coverage"]["covers"], 1)
            collect.assert_called_once()
            complete_covers.assert_called_once()

    def test_collection_is_saved_before_translation_pause(self):
        snapshot = self.seed()
        # A fresh collection can commit successfully while its delivery needs translation.
        with patch("tkmonitor.cli.collect_external", return_value=snapshot):
            self.assertEqual(self.invoke("collect", "--day", "2020-01-01", "--report", "--translate"), 5, self.stderr)
        self.assertEqual(len(self.store.snapshots()), 1)
        self.assertEqual(self.store.connection.execute("SELECT status FROM attempts").fetchone()[0], "valid")

    def test_import_then_export_without_recollection_keeps_all_numbers_and_times(self):
        self.seed()
        before, row = self.immutable(), build_report(self.store, self.config, "2020-01-01", "frontend")["rows"][0]
        path, task = self.completed()
        write_json(path, task)
        self.assertEqual(self.invoke("import-translations", "--input", str(path)), 0, self.stderr)
        self.assertEqual(before, self.immutable())
        with patch("tkmonitor.cli.collect_external", side_effect=AssertionError("No recollection")):
            self.assertEqual(self.invoke("report", "--day", "2020-01-01", "--metric", "frontend",
                                         "--no-covers", "--translate"), 0, self.stderr)
        saved = self.saved_report()
        self.assertIs(saved.get("include_translations", True), True)
        self.assertEqual(saved["rows"][0]["description_zh"], "一个女孩救下了自己的父亲。")
        current = build_report(self.store, self.config, "2020-01-01", "frontend")["rows"][0]
        self.assertEqual(current["title_zh"], "小小英雄")
        self.assertEqual(current["description_zh"], "一个女孩救下了自己的父亲。")
        for key in ("today_views", "initial_views", "recorded_at", "snapshot_id"):
            self.assertEqual(row[key], current[key])

    def test_unchanged_original_reuses_translation_but_changed_original_queues_again(self):
        self.seed()
        path, task = self.completed()
        write_json(path, task)
        import_translations(self.store, self.config, path)
        self.seed("2020-01-02")
        self.assertEqual(translation_tasks(self.store, self.config, "2020-01-02")["pending"], 0)
        self.seed("2020-01-03", "The girl reunites with her mother.")
        result = translation_tasks(self.store, self.config, "2020-01-03")
        self.assertEqual(result["pending"], 1)
        pending = json.loads(Path(result["task"]).read_text(encoding="utf-8"))["records"][0]
        self.assertEqual(pending["description_en"], "The girl reunites with her mother.")

    def test_legacy_valid_chinese_is_reused_and_task_is_stable(self):
        self.seed(translated=True)
        self.assertEqual(translation_tasks(self.store, self.config, "2020-01-01")["pending"], 0)
        self.seed("2020-01-02", "A changed original.")
        first = translation_tasks(self.store, self.config, "2020-01-02")
        content = Path(first["task"]).read_bytes()
        self.assertEqual(first, translation_tasks(self.store, self.config, "2020-01-02"))
        self.assertEqual(content, Path(first["task"]).read_bytes())

    def test_invalid_batch_is_rejected_before_any_metadata_changes(self):
        self.seed()
        path, task = self.completed()
        bad = copy.deepcopy(task["records"][0])
        bad["id"] = TWO
        task["records"].append(bad)
        write_json(path, task)
        before = self.immutable()
        with self.assertRaisesRegex(MonitorError, "does not match"):
            import_translations(self.store, self.config, path)
        self.assertNotIn("title_zh", self.store.metadata_at("demo", ONE, "2020-01-01T00:00:00Z"))
        self.assertEqual(before, self.immutable())

    def test_wrong_original_hash_or_account_cannot_be_imported(self):
        self.seed()
        path, task = self.completed()
        for field, wrong in (("source_hash", "wrong"), ("description_en", "Invented plot"), ("account", "other")):
            with self.subTest(field=field):
                changed = copy.deepcopy(task)
                changed["records"][0][field] = wrong
                write_json(path, changed)
                with self.assertRaises(MonitorError):
                    import_translations(self.store, self.config, path)

    def test_pending_or_english_only_text_does_not_count_as_chinese(self):
        self.seed()
        path, task = self.completed()
        for field, wrong in (("title_zh", "待翻译"), ("description_zh", "A girl saves her father."),
                             ("description_zh", "【中文待翻译：以下为本次采集的英文简介】English")):
            with self.subTest(field=field):
                changed = copy.deepcopy(task)
                changed["records"][0][field] = wrong
                write_json(path, changed)
                with self.assertRaisesRegex(MonitorError, "real Chinese"):
                    import_translations(self.store, self.config, path)

    def test_missing_original_cannot_be_replaced_with_invented_plot(self):
        self.seed(description="")
        path, task = self.completed()
        write_json(path, task)
        with self.assertRaisesRegex(MonitorError, "source synopsis is missing"):
            import_translations(self.store, self.config, path)

    def test_incomplete_metric_keeps_original_diagnostic_exit_code(self):
        self.store.commit_batch([Snapshot("display-only", "demo", "2020-01-01T00:00:00Z", "synthetic",
                                          (Record(ONE, "Title", 100, None),))])
        self.assertEqual(self.invoke("report", "--day", "2020-01-01", "--metric", "frontend", "--no-covers"), 3, self.stderr)
