"""The first submitted prompt drops the what's-new body instead of pinning it."""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import tempfile

from e2e.app_server.config import E2E_DIR, MINIMAL_HOME_CONFIG
from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

# A fresh harness home never trips the what's-new gate; seed a stale
# seen-version so the banner mounts before the prompt dismisses it.
_home = tempfile.mkdtemp(prefix="e2e_whatsnew_dismissed_")
atexit.register(shutil.rmtree, _home, True)
Path(_home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
Path(_home, "cache.toml").write_text(
    '[update_cache]\nlatest_version = "0.0.0"\nstored_at_timestamp = 0\n'
    'seen_whats_new_version = "0.0.0"\n'
    '[whats_new]\nseen_version = "0.0.0"\n'
)

env = {
    "VIBE_HOME": _home,
    "VIBE_WHATS_NEW_FILE": os.fspath(E2E_DIR / "fixtures" / "whats_new.fixture"),
}

screen_excludes = {"rust": ("What's new in v9.9.9-fixture",)}

capture_startup = False
timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
]
