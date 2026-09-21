from __future__ import annotations

import os
from pathlib import Path
import shlex
import sys

if sys.platform == "win32":
    _BIN_NAME = "vibe-rs.exe"
    _APP_SERVER_SCRIPT = "vibe-app-server.exe"
else:
    _BIN_NAME = "vibe-rs"
    _APP_SERVER_SCRIPT = "vibe-app-server"

# vibe/
#   _bin/                <-- Exists if the Rust CLI is bundled in the wheel.
#     vibe-rs(.exe)
#   cli/
#     _rust.py           <-- This file
#   cli-rust/            <-- Source checkout only (excluded from the wheel).
#     Cargo.toml
#     target/
#       release/
#         vibe-rs(.exe)  <-- Exists after a release build in the source checkout.
_PKG_ROOT = Path(__file__).resolve().parents[1]
_BUNDLED_BIN = _PKG_ROOT / "_bin" / _BIN_NAME
_MANIFEST = _PKG_ROOT / "cli-rust" / "Cargo.toml"
_RELEASE_BIN = _PKG_ROOT / "cli-rust" / "target" / "release" / _BIN_NAME
# The Python project root, where `uv run vibe-app-server` resolves.
_PROJECT_ROOT = _PKG_ROOT.parent


def exec_rust_cli(passthrough: list[str]) -> None:
    """Run the Rust TUI, keeping the caller's cwd."""
    env = {**os.environ}
    if _BUNDLED_BIN.exists():
        binary = _BUNDLED_BIN
        # Wheel install: point the Rust client at the installed vibe-app-server
        env["VIBE_APP_SERVER_BIN"] = str(
            Path(sys.argv[0]).resolve().parent / _APP_SERVER_SCRIPT
        )
        env.pop("VIBE_APP_SERVER_CWD", None)
        # A stale full-command override must not shadow the wheel's binary.
        env.pop("VIBE_APP_SERVER_CMD", None)
    else:
        if not _MANIFEST.exists():
            sys.exit(f"Rust CLI not bundled and no source checkout at {_MANIFEST}.")
        if not _RELEASE_BIN.exists():
            _build_release()
        binary = _RELEASE_BIN
        env.pop("VIBE_APP_SERVER_BIN", None)
        env["VIBE_APP_SERVER_CWD"] = str(_PROJECT_ROOT)
    argv = [str(binary), *passthrough]
    if sys.platform == "win32":
        # execvpe does not overlay the process on Windows; run and forward the code.
        import subprocess

        sys.exit(subprocess.run(argv, env=env).returncode)
    os.execvpe(str(binary), argv, env)


def _build_release() -> None:
    import subprocess

    print("Building vibe-rs (first run, this may take a while)...", file=sys.stderr)
    cmd = [
        "cargo",
        "build",
        "--release",
        "--manifest-path",
        str(_MANIFEST),
        "--bin",
        "vibe-rs",
        *shlex.split(os.environ.get("CARGO_BUILD_FLAGS", "")),
    ]
    try:
        result = subprocess.run(cmd, cwd=_PROJECT_ROOT)
    except FileNotFoundError:
        sys.exit(
            "cargo not found; install Rust (https://rustup.rs) to use VIBE_CLI=rust."
        )
    if result.returncode != 0 or not _RELEASE_BIN.exists():
        sys.exit("Failed to build vibe-rs.")
