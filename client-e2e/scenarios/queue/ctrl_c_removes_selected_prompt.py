"""Ctrl+C in queue selection removes the highlighted prompt and keeps the draft; Delete does not."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

_UP = "\x1b[A"
_DELETE = "\x1b[3~"
_CTRL_C = "\x03"

# The older `second` goes, not the newest `third`; the draft stays for Esc to restore.
screen_contains = {"rust": ("third", "draft", "Backspace/Ctrl+C to remove")}
screen_excludes = {"rust": ("second", "cancel last queued message")}
settle_per_key = True
capture_steps = {3, 4, 6, 7, 8, 9}

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    "third\r",
    "draft",
    # The first Up parks the caret to protect the draft; the second opens the queue.
    _UP,
    _UP,
    _UP,
    _DELETE,
    _CTRL_C,
]
