# Reusable Short Drama Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Preserve the original workflow first, then provide a reusable, validated local monitoring program with replaceable sources and immutable historical metrics.

**Architecture:** Source adapters emit snapshots, a validator gates transaction commits, and independent reporting reads only valid snapshots. The public repository contains source and sanitized fixtures; all real data and request credentials stay local. Python's standard library implements the core; the existing Artifact Tool exporter remains a separate optional integration.

**Tech Stack:** Python 3.11+, sqlite3, urllib, argparse, unittest; Node for the optional existing workbook exporter.

## Task 1: Archive before replacing

- [x] Copy nine original scripts/templates byte-for-byte into legacy/v1 and produce manifest.json hashes.
- [x] Preserve the original full data ZIP locally and verify its SHA-256.
- [x] Commit and push the legacy archive to main before new implementation.
- [x] Publish a legacy-v1-2026-10-28 tag after remote verification.

Verification: compare archived file bytes to source; inspect `git ls-files` to ensure no private data is tracked; fetch GitHub main HEAD and compare the uploaded commit.

## Task 2: Core, source adapters and command line

Files: `tkmonitor/{config,model,sources,store,analysis,reports,cli}.py`, `tkmonitor/__main__.py`, `config.example.json`, `run_daily.cmd`, `tests/test_monitor.py`, `pyproject.toml`, `README.md`.

- [x] First write meaningful tests for atomic ingestion, idempotent imports, missing-day/zero distinctions, metric isolation, calendar-day comparisons, pagination failure and authorization errors.
- [x] Implement the APIs and CLI in the specification, keeping each module responsible for one concern.
- [x] Use saved real histories for local integration; do not commit them or bake expected production counts into implementation logic.
- [x] Implement standard-library HTTP pagination with injected transport in tests, bounded retries, origin checks, no credential logs and fail-closed completeness checks.
- [x] Provide checkpoint source import and an explicit browser-helper integration contract; absent browser/HTTP configuration must cause actionable failure, not a stale-data success.
- [x] Generate complete CSV/JSON/HTML and a portable workbook input contract.
- [x] Run `python -m unittest discover -s tests -v` and CLI integration commands; fix failures before committing.

Local integration commands (run with a valid Python executable, from repository root):

```
python -m tkmonitor --help
python -m tkmonitor --config config.local.json import-history --source E:/jianc/outputs/monitoring_2026-10-28
python -m tkmonitor --config config.local.json report --day 2026-10-28 --metric display
python -m tkmonitor --config config.local.json status
```

## Task 3: Browser and workbook integrations

Files: `integrations/chrome_capture.mjs`, `integrations/export_workbooks.mjs`, `docs/browser-session.md`, `docs/runbook.md`.

- [x] Save and document the validated Chrome request-refresh helper. The independent HTTP collector always starts at cursor0, retains every page, and verifies end markers, exact offsets and IDs before committing.
- [x] Separate live validation from recorded-response tests. Never claim direct HTTP independent operation without an actual full live result.
- [x] Adapt the original workbook exporter to dynamic inputs/dates/counts and configurable runtime path. Export sequentially with bounded compilation resources and perform formula/image verification.
- [x] Run the imported October 28 data through the new report path and compare with the immutable original output.

## Task 4: Review and publish

- [x] Independent spec review against design; fix omissions.
- [x] Independent quality review after spec passes; fix confirmed defects and rerun affected tests.
- [x] Verify clean Git state, legacy hashes, tracked-file privacy, fresh tests and integration results.
- [x] Commit new flow to feature/reusable-monitor, push it, verify the remote SHA, and create a draft PR if supported.
- [x] Report exact tested capabilities and any live-source limitations, linking legacy archive and new branch separately.
