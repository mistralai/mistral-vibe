#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 <pyinstaller-spec> [<pyinstaller-spec> ...]" >&2
  exit 2
fi

uv sync --no-dev --group build

uv_run_args=(--no-dev --group build)
if [ -n "${VIBE_PYINSTALLER_WITH:-}" ]; then
  uv_run_args+=(--with "$VIBE_PYINSTALLER_WITH")
fi

for spec in "$@"; do
  uv run "${uv_run_args[@]}" pyinstaller "${spec}"
done
