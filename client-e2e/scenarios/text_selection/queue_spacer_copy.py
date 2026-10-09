"""A copy across the blank rows that keep the queue at the bottom skips them."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1", "SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}
expected_clipboard = "Working on it."

# Drag from the reply into the blank rows that hold the queued prompt at the bottom.
_SELECT = "\x1b[<0;3;15M\x1b[<32;120;22M\x1b[<0;120;22m"

# A release step must not consume a marker of its own, so settle key by key.
settle_per_key = True
capture_steps = {2, 3}

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    _SELECT,
]
