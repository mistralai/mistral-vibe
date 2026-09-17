"""Vibe client-e2e paths, clients, terminal geometry, and home config."""

from __future__ import annotations

import os
from pathlib import Path

ROWS, COLUMNS = 40, 120
IDLE_MARKER = b"\x1b]5379;vibe-idle\x07"
# Kitty-protocol F23/F24. Both clients drop them and use them to hold the idle
# marker across a batched step, so a step's keys cost one marker, not one each.
BATCH_KEYS = ("\x1b[57386u", "\x1b[57387u")

E2E_DIR = Path(__file__).resolve().parents[2]
VIBE_DIR = E2E_DIR.parent
SCENARIO_DIR = E2E_DIR / "scenarios"
# Local debug output for parity diff reports (gitignored, not committed).
# Override with VIBE_E2E_REPORT_DIR; disabled when VIBE_E2E_REPORTS=0 (e.g. CI).
REPORT_DIR = Path(os.environ.get("VIBE_E2E_REPORT_DIR", E2E_DIR / "reports"))
# Committed golden reference snapshots for Rust-only testing.
GOLDEN_DIR = E2E_DIR / "goldens"
FIXTURE_PATH = E2E_DIR / "fixtures" / "fixture.json"
REPLAY_BIN = VIBE_DIR / "vibe" / "cli-rust" / "target" / "release" / "vibe-replay"

CLIENTS = {
    "rust": (
        os.fspath(VIBE_DIR / "vibe" / "cli-rust" / "target" / "release" / "vibe-rs"),
    ),
    "python": (os.fspath(VIBE_DIR / ".venv" / "bin" / "vibe"),),
}

# The Python client pays ~0.4s of imports per capture; a zygote forks it instead.
PYTHON_BIN = VIBE_DIR / ".venv" / "bin" / "python"
ZYGOTE_CLIENT = "python"
ZYGOTE_ENV_VAR = "VIBE_E2E_ZYGOTE"

MINIMAL_HOME_CONFIG = """\
active_model = "mistral-medium-3.5"
enable_telemetry = false
enable_update_checks = false
disable_welcome_banner_animation = true

[[providers]]
name = "mistral"
api_base = "https://api.mistral.ai/v1"
api_key_env_var = "MISTRAL_API_KEY"
backend = "mistral"

[[models]]
name = "mistral-vibe-cli"
provider = "mistral"
alias = "mistral-medium-3.5"
"""
