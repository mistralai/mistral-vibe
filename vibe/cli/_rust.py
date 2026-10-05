from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import sys
import time
from typing import Final

from vibe.core.experiments._constants import (
    EVAL_CACHE_FILE_NAME,
    EVAL_CACHE_TTL_SECONDS,
)
from vibe.utils.vibe_home import get_vibe_home

ROLLOUT_EXPERIMENT_KEY: Final = "vibe_cli_rust_tui_rollout"
# Tells vibe-rs it was picked by the rollout, so failures print the Python fallback hint.
ROLLOUT_ENV: Final = "VIBE_RUST_ROLLOUT"
# Python CLI flags the Rust CLI rejects: the rollout must not break these invocations.
_PYTHON_ONLY_FLAGS: Final = frozenset({
    "--setup",
    "--legacy-harness",
    "--experimental-harness",
})

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


def rust_rollout_selected(option_args: list[str]) -> bool:
    # Never triggers the cargo fallback build: the rollout only targets wheel installs.
    if not _BUNDLED_BIN.exists():
        return False
    if any(arg.split("=", 1)[0] in _PYTHON_ONLY_FLAGS for arg in option_args):
        return False
    return _cached_rollout_variant() == "rust"


def _cached_rollout_variant() -> object:
    try:
        with (get_vibe_home() / EVAL_CACHE_FILE_NAME).open(encoding="utf-8") as f:
            entries = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(entries, dict):
        return None
    min_stored_at = int(time.time()) - EVAL_CACHE_TTL_SECONDS
    fresh: list[tuple[int, object]] = [
        (entry["stored_at_timestamp"], entry.get("payload"))
        for entry in entries.values()
        if isinstance(entry, dict)
        and isinstance(entry.get("stored_at_timestamp"), int)
        and entry["stored_at_timestamp"] > min_stored_at
    ]
    if not fresh:
        return None
    # One entry per API key: the most recent eval belongs to the active key.
    _, payload = max(fresh, key=lambda item: item[0])
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, dict):
        return None
    return _resolved_value(features.get(ROLLOUT_EXPERIMENT_KEY))


def _resolved_value(feature: object) -> object:
    # Mirrors FeatureDefinition.resolved_value without importing pydantic.
    if not isinstance(feature, dict):
        return None
    rules = feature.get("rules")
    for rule in rules if isinstance(rules, list) else []:
        if isinstance(rule, dict) and rule.get("force") is not None:
            return rule["force"]
    return feature.get("defaultValue")


def exec_rust_cli(passthrough: list[str], *, rollout: bool = False) -> None:
    """Run the Rust TUI, keeping the caller's cwd."""
    env = {**os.environ}
    if rollout:
        env[ROLLOUT_ENV] = "1"
    else:
        env.pop(ROLLOUT_ENV, None)
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
