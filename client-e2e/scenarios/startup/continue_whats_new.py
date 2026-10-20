"""A resumed startup that shows what's new keeps the history intact above the banner."""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import tempfile

from e2e.app_server.config import MINIMAL_HOME_CONFIG, WHATS_NEW_FIXTURE
from e2e.app_server.scenario import Timeline

# A stale seen-version trips the what's-new gate; see startup/whats_new.py.
_home = tempfile.mkdtemp(prefix="e2e_whatsnew_continue_")
atexit.register(shutil.rmtree, _home, True)
Path(_home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
Path(_home, "cache.toml").write_text(
    '[update_cache]\nlatest_version = "0.0.0"\nstored_at_timestamp = 0\n'
    'seen_whats_new_version = "0.0.0"\n'
)

env = {"VIBE_HOME": _home, "VIBE_WHATS_NEW_FILE": os.fspath(WHATS_NEW_FIXTURE)}
client_args = ("--continue",)

# The saved prompt and reply stay adjacent, with what's new after the whole history.
screen_contains = (
    "> Continue the implementation",
    "Saved session restored.",
    "What's new in v9.9.9-fixture",
)

capture_startup = False
timeline: Timeline = ["x"]
