#!/bin/sh
set -eu

project_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
cd "$project_dir"
if [ -f ./runtime.local.sh ]; then
    . ./runtime.local.sh
fi
if [ -z "${TKMONITOR_PYTHON:-}" ]; then
    if [ -x "$project_dir/.venv/bin/python" ]; then
        TKMONITOR_PYTHON="$project_dir/.venv/bin/python"
    else
        TKMONITOR_PYTHON=python3
    fi
fi
exec "$TKMONITOR_PYTHON" -X utf8 -m tkmonitor \
    --config "${TKMONITOR_CONFIG:-$project_dir/config.local.json}" \
    onboard "$@"
