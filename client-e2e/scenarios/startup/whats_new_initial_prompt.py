"""A prompt passed on the command line keeps the conversation last: what's new waits for a fresh session."""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import tempfile

from e2e.app_server.config import MINIMAL_HOME_CONFIG, WHATS_NEW_FIXTURE
from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot

# A stale seen-version would trip the what's-new gate on a fresh session; see startup/whats_new.py.
_home = tempfile.mkdtemp(prefix="e2e_whatsnew_initial_prompt_")
atexit.register(shutil.rmtree, _home, True)
Path(_home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
Path(_home, "cache.toml").write_text(
    '[update_cache]\nlatest_version = "0.0.0"\nstored_at_timestamp = 0\n'
    'seen_whats_new_version = "0.0.0"\n'
)

env = {"VIBE_HOME": _home, "VIBE_WHATS_NEW_DIR": os.fspath(WHATS_NEW_FIXTURE)}
client_args = ("hi",)

screen_contains = ("Hi. What do you need?",)
screen_excludes = ("Release notes",)

timeline: Timeline = [
    "",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
    take_snapshot(),
]
