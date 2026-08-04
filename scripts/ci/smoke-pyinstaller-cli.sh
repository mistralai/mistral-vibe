#!/usr/bin/env bash
set -euo pipefail

binary_dir="${1:-dist/vibe-dir}"

if [ -n "${PYTHON_BIN:-}" ]; then
  read -r -a python_cmd <<< "${PYTHON_BIN}"
elif command -v uv >/dev/null 2>&1; then
  python_cmd=(uv run python)
elif [ -x ".venv/bin/python" ]; then
  python_cmd=(.venv/bin/python)
elif [ -x ".venv/Scripts/python.exe" ]; then
  python_cmd=(.venv/Scripts/python.exe)
else
  python_cmd=(python)
fi

"${python_cmd[@]}" tests/cli/smoke_binary.py "${binary_dir}"
