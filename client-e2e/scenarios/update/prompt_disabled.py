"""With update checks disabled, a pending cached update never prompts at startup."""

from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

from e2e.app_server.config import (
    MINIMAL_HOME_CONFIG,
    REPLAY_NOW_MS,
    VIBE_DIR,
    WHATS_NEW_FIXTURE,
)
from e2e.app_server.scenario import Timeline

_CLI = VIBE_DIR / "vibe" / "cli-rust"
_VERSION = re.search(
    r'^version = "([^"]+)"', (_CLI / "Cargo.toml").read_text(), re.MULTILINE
)
_SCHEMA = re.search(
    r"const SCHEMA_VERSION: u32 = (\d+);",
    (_CLI / "src" / "utils" / "startup_cache.rs").read_text(),
)
assert _VERSION is not None and _SCHEMA is not None

# The same pending update `update/prompt.py` prompts for, but the previous run
# cached `enable_update_checks = false`: the gate reads that cache before the
# app-server is up, so the dialog never opens.
_home = tempfile.mkdtemp(prefix="e2e_update_prompt_disabled_")
atexit.register(shutil.rmtree, _home, True)
Path(_home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
Path(_home, "cache.toml").write_text(
    "[update_cache]\n"
    'latest_version = "99.0.0"\n'
    f"stored_at_timestamp = {REPLAY_NOW_MS // 1000}\n"
    'seen_whats_new_version = "0.0.0"\n'
)
Path(_home, "ui_startup_config.json").write_text(
    json.dumps({
        "schemaVersion": int(_SCHEMA.group(1)),
        "serverVersion": _VERSION.group(1),
        "theme": "ansi-dark",
        "disableWelcomeBannerAnimation": True,
        "autocopyToClipboard": True,
        "activeModel": "mistral-medium-3.5",
        "activeModelDisplayName": "mistral-medium-3.5",
        "imagesSupported": False,
        "defaultAgent": "accept-edits",
        "agents": [],
        "skills": [],
        "contextWindow": 0,
        "narratorEnabled": False,
        "enableUpdateChecks": False,
        "cachedAt": int(time.time()),
    })
)

env = {"VIBE_HOME": _home, "VIBE_WHATS_NEW_DIR": os.fspath(WHATS_NEW_FIXTURE)}

capture_startup = False
timeline: Timeline = ["x"]

screen_contains = ("> x",)
screen_excludes = ("A new Vibe release is available", "99.0.0")
