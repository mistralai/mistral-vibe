"""A resumed startup keeps the conversation last on screen: what's new waits for a fresh session."""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import tempfile

from e2e.app_server.config import MINIMAL_HOME_CONFIG, WHATS_NEW_FIXTURE
from e2e.app_server.scenario import Timeline

# A stale seen-version would trip the what's-new gate on a fresh session; see startup/whats_new.py.
_home = tempfile.mkdtemp(prefix="e2e_whatsnew_continue_")
atexit.register(shutil.rmtree, _home, True)
Path(_home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
Path(_home, "cache.toml").write_text(
    '[update_cache]\nlatest_version = "0.0.0"\nstored_at_timestamp = 0\n'
    'seen_whats_new_version = "0.0.0"\n'
)

env = {"VIBE_HOME": _home, "VIBE_WHATS_NEW_DIR": os.fspath(WHATS_NEW_FIXTURE)}
client_args = ("--continue",)

screen_contains = ("> Continue the implementation", "Saved session restored.")
screen_excludes = ("Release notes",)

capture_startup = False
timeline: Timeline = ["x"]
