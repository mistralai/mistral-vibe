#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 <pyinstaller-spec> [<pyinstaller-spec> ...]" >&2
  exit 2
fi

uv sync --no-dev --group build

for spec in "$@"; do
  uv run --no-dev --group build pyinstaller "${spec}"
done
