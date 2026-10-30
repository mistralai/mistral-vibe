from __future__ import annotations

import os
from pathlib import Path
import shlex
import sys

# Kept intentionally tiny: the `vibe` console script lands here and only execs
# the Rust TUI, so it must not import the rest of the Python package.

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
#     launcher.py        <-- This file
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
_APP_SERVER_ENV_KEYS = (
    "VIBE_APP_SERVER_CMD",
    "VIBE_APP_SERVER_BIN",
    "VIBE_APP_SERVER_CWD",
)


def main() -> None:
    exec_rust_cli(sys.argv[1:])


def exec_rust_cli(
    passthrough: list[str],
    *,
    extra_env: dict[str, str] | None = None,
    app_server_cmd: list[str] | None = None,
) -> None:
    """Run the Rust TUI in the caller's cwd; `app_server_cmd` sets its app-server."""
    env = {**os.environ, **(extra_env or {})}
    binary = _rust_cli_binary()
    selected = _app_server_env(env, app_server_cmd, bundled=binary == _BUNDLED_BIN)
    # Stale values from the parent env must not shadow the selected app-server.
    for name in _APP_SERVER_ENV_KEYS:
        env.pop(name, None)
    env.update(selected)
    argv = [str(binary), *passthrough]
    if sys.platform == "win32":
        # execvpe does not overlay the process on Windows; run and forward the code.
        import subprocess

        sys.exit(subprocess.run(argv, env=env).returncode)
    os.execvpe(str(binary), argv, env)


def _rust_cli_binary() -> Path:
    if _BUNDLED_BIN.exists():
        return _BUNDLED_BIN
    if not _MANIFEST.exists():
        sys.exit(
            f"Rust CLI not bundled at {_BUNDLED_BIN} and no source checkout at "
            f"{_MANIFEST}."
        )
    if not _RELEASE_BIN.exists():
        _build_release()
    return _RELEASE_BIN


def _app_server_env(
    env: dict[str, str], app_server_cmd: list[str] | None, *, bundled: bool
) -> dict[str, str]:
    if app_server_cmd is not None:
        return {"VIBE_APP_SERVER_CMD": shlex.join(app_server_cmd)}
    if bundled:
        # Wheel install: point the Rust client at the installed vibe-app-server.
        script = Path(sys.argv[0]).resolve().parent / _APP_SERVER_SCRIPT
        return {"VIBE_APP_SERVER_BIN": str(script)}
    # Source checkout: the Makefile's command, if any, runs from the project root.
    selected = {"VIBE_APP_SERVER_CWD": str(_PROJECT_ROOT)}
    if cmd := env.get("VIBE_APP_SERVER_CMD"):
        selected["VIBE_APP_SERVER_CMD"] = cmd
    return selected


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
            "cargo not found; install Rust (https://rustup.rs) to run vibe from a "
            "source checkout."
        )
    if result.returncode != 0 or not _RELEASE_BIN.exists():
        sys.exit("Failed to build vibe-rs.")
