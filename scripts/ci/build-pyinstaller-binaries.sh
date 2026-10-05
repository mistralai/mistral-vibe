#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 <pyinstaller-spec> [<pyinstaller-spec> ...]" >&2
  exit 2
fi

# VIBE_NO_INSTALL_PROJECT=1: a wheel is already pip-installed; any uv sync
# (plain or implicit via uv run) would prune it from the venv.
uv_run_args=(--no-dev --group build)
if [ "${VIBE_NO_INSTALL_PROJECT:-0}" = "1" ]; then
  uv_run_args+=(--no-sync)
else
  uv sync --no-dev --group build
fi

if [ -n "${VIBE_PYINSTALLER_WITH:-}" ]; then
  uv_run_args+=(--with "$VIBE_PYINSTALLER_WITH")
fi

# A fresh checkout stamps every file with now, which would always
# invalidate the cached TOCs; pin to a fixed epoch so they look current.
if [ -n "${VIBE_PIN_MTIMES:-}" ]; then
  find vibe -type f -exec touch -t 202001010000 {} +
fi

# PyInstaller stages/codesigns collected binaries in a single platform-wide
# cache dir; concurrent builds race there, so each spec gets its own
# PYINSTALLER_CONFIG_DIR. VIBE_SERIAL_PYINSTALLER=1 keeps the old
# one-at-a-time behavior.
pids=()
specs=("$@")
run_spec() {
  local spec="$1"
  mkdir -p ".native-build/pyinstaller-work/${spec%.spec}"
  PYINSTALLER_CONFIG_DIR="$PWD/.native-build/pyinstaller-config/${spec%.spec}" \
    uv run "${uv_run_args[@]}" pyinstaller \
    --workpath ".native-build/pyinstaller-work/${spec%.spec}" \
    "${spec}"
}
if [ "${VIBE_SERIAL_PYINSTALLER:-0}" = "1" ]; then
  for spec in "${specs[@]}"; do
    run_spec "$spec"
  done
else
  for spec in "${specs[@]}"; do
    run_spec "$spec" &
    pids+=("$!")
  done
  failed=0
  for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
      failed=1
    fi
  done
  if [ "$failed" -ne 0 ]; then
    echo "At least one pyinstaller build failed" >&2
    exit 1
  fi
fi
