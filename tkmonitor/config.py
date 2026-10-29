"""Portable configuration; all local paths resolve against the configuration file."""

from dataclasses import dataclass
import json
from pathlib import Path
import re

from .model import HANDLE_PATTERN, MonitorError


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MonitorError(f"Cannot read valid JSON from local file {Path(path).name}.") from exc


@dataclass(frozen=True)
class Config:
    path: Path
    state_dir: Path
    report_dir: Path
    accounts: tuple[dict, ...]

    def resolve(self, path):
        value = Path(path)
        return value.resolve() if value.is_absolute() else (self.path.parent / value).resolve()

    @property
    def handles(self):
        return {account["handle"] for account in self.accounts}


def load_config(path):
    path = Path(path).resolve()
    data = read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), list) or not data["accounts"]:
        raise MonitorError("Configuration needs a nonempty accounts list.")
    accounts, handles, prefixes = [], set(), set()
    for item in data["accounts"]:
        if not isinstance(item, dict):
            raise MonitorError("Every account configuration must be an object.")
        handle = item.get("handle")
        if not isinstance(handle, str) or not HANDLE_PATTERN.fullmatch(handle) or handle in handles:
            raise MonitorError("Account handles must be unique and contain only letters, digits, dots, hyphens or underscores.")
        name = item.get("name", handle)
        prefix = item.get("prefix", re.sub(r"[^A-Za-z0-9_-]", "_", name))
        if not isinstance(name, str) or not isinstance(prefix, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", prefix) or prefix in prefixes:
            raise MonitorError("Account names need unique safe filename prefixes.")
        handles.add(handle)
        prefixes.add(prefix)
        accounts.append({**item, "handle": handle, "name": name, "prefix": prefix})
    state = Path(data.get("state_dir", "_local/state"))
    state = state.resolve() if state.is_absolute() else (path.parent / state).resolve()
    report = Path(data["report_dir"]) if "report_dir" in data else state / "reports"
    report = report.resolve() if report.is_absolute() else (path.parent / report).resolve()
    return Config(path, state, report, tuple(accounts))
