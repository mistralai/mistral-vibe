"""Vibe client-e2e paths, client binary, terminal geometry, and home config."""

from __future__ import annotations

from pathlib import Path

ROWS, COLUMNS = 40, 120
# The client acks fence N with `ACK_PREFIX N BEL` once its frame has settled.
ACK_PREFIX = b"\x1b]5379;vibe-idle;"
# Kitty-protocol F24: the fence the harness sends after each key batch.
FENCE_KEY = "\x1b[57387u"
# 2026-09-10T09:00:45Z, exactly two weeks after the fixture sessions.
REPLAY_NOW_MS = 1_789_030_845_000

E2E_DIR = Path(__file__).resolve().parents[2]
VIBE_DIR = E2E_DIR.parent
SCENARIO_DIR = E2E_DIR / "scenarios"
# Committed golden reference snapshots.
GOLDEN_DIR = E2E_DIR / "goldens"
# One hash per golden, so PRs changing the same goldens conflict in git.
GOLDEN_LOCK = E2E_DIR / "goldens.lock"
FIXTURE_PATH = E2E_DIR / "fixtures" / "fixture.json"
# A frozen release-notes history, so what's-new goldens never churn per release.
WHATS_NEW_FIXTURE = E2E_DIR / "fixtures" / "whats_new"
# Eight frozen releases: enough to cap the startup banner and scroll `/release-notes`.
WHATS_NEW_HISTORY_FIXTURE = E2E_DIR / "fixtures" / "whats_new_history"
# The client's working directory: a small tree, so startup indexing stays cheap.
WORKSPACE_DIR = E2E_DIR / "fixtures" / "workspace"
CLIENT_BIN = VIBE_DIR / "vibe" / "cli-rust" / "target" / "release" / "vibe-rs"
REPLAY_BIN = CLIENT_BIN.with_name("vibe-replay")

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
