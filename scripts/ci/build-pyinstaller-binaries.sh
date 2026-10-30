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

# vibe-acp.spec bundles vibe-rs for `vibe-acp --setup`. A wheel install
# already carries it; a source install builds it here. The wizard needs no
# voice, so the build drops it unless CARGO_BUILD_FLAGS says otherwise.
if [[ " $* " == *" vibe-acp.spec "* ]] && [ "${VIBE_NO_INSTALL_PROJECT:-0}" != "1" ] \
  && [ -z "${VIBE_SKIP_RUST_TUI:-}" ] && [ -z "${VIBE_ACP_RUST_TUI:-}" ]; then
  tui_target="$PWD/.native-build/vibe-rs-target"
  read -r -a tui_flags <<< "${CARGO_BUILD_FLAGS:---no-default-features}"
  CARGO_TARGET_DIR="$tui_target" cargo build --release --locked \
    --manifest-path vibe/cli-rust/Cargo.toml --bin vibe-rs "${tui_flags[@]}"
  tui_name=vibe-rs
  if [ "${OS:-}" = "Windows_NT" ]; then
    tui_name=vibe-rs.exe
  fi
  export VIBE_ACP_RUST_TUI="$tui_target/release/$tui_name"
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
#
# PYTHONDONTWRITEBYTECODE: every spec imports the Unified Runtime package to
# probe it, which writes __pycache__/*.pyc.<random> temp files into the venv,
# while another spec's collect_all lists that same package as data. A temp
# name does not end in .pyc, so it gets collected, then vanishes before
# PyInstaller copies it ("Unable to find ... .pyc.<n>"). PyInstaller compiles
# the bytecode it bundles itself, so the venv never needs these files.
pids=()
specs=("$@")
run_spec() {
  local spec="$1"
  mkdir -p ".native-build/pyinstaller-work/${spec%.spec}"
  PYTHONDONTWRITEBYTECODE=1 \
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
