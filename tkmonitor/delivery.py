"""Independently audit saved XLSX values and package only verified report files."""

import math
from pathlib import Path
import re
import shutil
import zipfile
from xml.etree import ElementTree as ET

from .config import read_json
from .model import MonitorError
from .reports import _json_file

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
XLSX_ESCAPE = re.compile(r"_x([0-9a-f]{4})_", re.IGNORECASE)
FIELDS = {"E": "initial_views", "F": "yesterday_views", "G": "today_views", "H": "day_delta", "I": "day_rate",
          "J": "yesterday_from_initial_delta", "K": "yesterday_from_initial_rate", "L": "today_from_initial_delta",
          "M": "today_from_initial_rate", "AB": "last_views", "AC": "last_delta", "AD": "last_rate"}
TOTALS = {"E": "initial_total", "F": "yesterday_total", "G": "today_total", "H": "day_delta", "I": "day_rate",
          "J": "yesterday_from_initial_delta", "K": "yesterday_from_initial_rate", "L": "today_from_initial_delta",
          "M": "today_from_initial_rate", "AB": "last_total", "AC": "last_delta", "AD": "last_rate"}


def workbook_name(config):
    prefix = config["prefix"]
    if not isinstance(prefix, str) or not prefix or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in prefix):
        raise MonitorError("Workbook configuration needs a safe filename prefix.")
    return prefix + "_" + str(len(config["rows"])) + "部剧_初始昨天今天_完整数字.xlsx"


def _equal(actual, expected):
    if expected is None:
        return actual is None or actual == ""
    if isinstance(expected, str):
        return actual == expected
    if isinstance(expected, int):
        return actual == expected
    return isinstance(actual, (float, int)) and math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-9)


def _xlsx_text(value):
    # A single pass also preserves literal markers escaped with _x005F_.
    return XLSX_ESCAPE.sub(lambda match: chr(int(match[1], 16)), value)


def verify_workbook(path, config):
    """Read the exported ZIP, including cached formula values, instead of trusting the exporter."""
    include_translations = config.get("include_translations", True)
    try:
        with zipfile.ZipFile(path) as book:
            shared = []
            if "xl/sharedStrings.xml" in book.namelist():
                shared = [_xlsx_text("".join(node.itertext())) for node in ET.fromstring(book.read("xl/sharedStrings.xml"))]
            sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
            cells = {node.attrib["r"]: node for node in sheet.findall(".//s:sheetData/s:row/s:c", NS)}
            if any(node.attrib.get("t") == "e" for node in cells.values()):
                raise MonitorError("Saved workbook contains spreadsheet error cells.")

            def value(address):
                cell = cells.get(address)
                if cell is None:
                    return None
                kind = cell.attrib.get("t")
                if kind == "inlineStr":
                    return _xlsx_text("".join(cell.find("s:is", NS).itertext()))
                raw = cell.find("s:v", NS)
                if raw is None or raw.text is None:
                    return None
                if kind == "s":
                    return shared[int(raw.text)]
                if kind == "str":
                    return _xlsx_text(raw.text)
                return float(raw.text)

            def check(address, expected):
                if not _equal(value(address), expected):
                    raise MonitorError("Saved workbook value mismatch at " + address + ".")

            for number, row in enumerate(config["rows"], 8):
                check("X" + str(number), row["id"])
                check("D" + str(number), row["title"])
                if include_translations:
                    check("C" + str(number), row.get("title_zh") or "中文名待补")
                    check("V" + str(number), row.get("description_zh") or row.get("description_en") or "简介待补")
                    check("W" + str(number), row.get("genre") or "题材待补")
                else:
                    check("C" + str(number), row.get("status") or None)
                    check("V" + str(number), row.get("description_en") or None)
                    check("W" + str(number), row.get("genre") or None)
                for column, field in FIELDS.items():
                    check(column + str(number), row.get(field))
            total = len(config["rows"]) + 8
            for column, field in TOTALS.items():
                check(column + str(total), config["summary"][field])
            image_count = sum(name.startswith("xl/media/") and not name.endswith("/") for name in book.namelist())
            if image_count != sum(bool(row.get("image")) for row in config["rows"]):
                raise MonitorError("Saved workbook image count differs from the report.")
        text_check = "chinese_text_verified" if include_translations else "original_text_verified"
        return {"account": config["name"], "rows": len(config["rows"]), "images": image_count,
                "file": Path(path).name, "formula_values_verified": True, text_check: True}
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError, IndexError, ValueError, TypeError) as exc:
        raise MonitorError("Cannot verify the saved XLSX structure; valid collection data is preserved.") from exc


def package_report(folder, output=None):
    folder = Path(folder).resolve()
    destination = Path(output).resolve() if output else folder
    configs = read_json(folder / "workbook_configs.json")
    if not isinstance(configs, list) or not configs:
        raise MonitorError("Packaging needs nonempty workbook configurations.")
    report = read_json(folder / "merged.json")
    media = read_json(folder / "media_validation.json")
    coverage = media.get("coverage", {})
    if (not report.get("complete") or report.get("metric_coverage", {}).get("status") != "complete"
            or media.get("status") != "verified" or media.get("day") != report.get("report_day")
            or coverage.get("records") != len(report.get("rows", []))
            or coverage.get("covers") != coverage.get("records")
            or coverage.get("english_synopses") != coverage.get("records")
            or media.get("snapshot_ids") != sorted({row["snapshot_id"] for row in report.get("rows", [])})):
        raise MonitorError("Packaging requires a complete report and verified covers/synopses; run finalize first.")
    if configs != report["accounts"]:
        raise MonitorError("Workbook configurations differ from the report; regenerate before packaging.")
    filenames = ["monitor.html", "merged.json", "media_validation.json", "workbook_configs.json"]
    checked = []
    for config in configs:
        filename = workbook_name(config)
        checked.append(verify_workbook(folder / filename, config))
        filenames.extend((filename, config["prefix"] + ".csv"))
    if (folder / "validation.json").is_file():
        filenames.append("validation.json")
    # Validate the entire delivery before creating or copying any destination files.
    if any(not (folder / name).is_file() or not (folder / name).stat().st_size for name in filenames):
        raise MonitorError("A required report file is missing or empty; nothing was packaged.")
    destination.mkdir(parents=True, exist_ok=True)
    if destination != folder:
        for name in filenames:
            shutil.copy2(folder / name, destination / name)
    _json_file(destination / "workbooks_verified.json", checked)
    return {"output": str(destination), "workbooks": checked}
