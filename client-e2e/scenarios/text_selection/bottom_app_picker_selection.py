"""Picker box text and the transcript above it stay selectable while the picker is open."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {
    "VIBE_REPLAY_SETTLE_BUSY": "1",
    "VIBE_TYPING_GRACE_PERIOD_MS": "0",
    "SSH_TTY": "/dev/pts/0",
}
capture_startup = False
capture_steps = {1, 2, 3, 4}
clipboard_clients = {"rust"}
expected_clipboard = "Log Level"

_SENTENCE = "Bright violet machines hum quietly in the northern hall."

# Drag across the assistant row above the box, then across the picker title (1-based SGR coordinates).
_DRAG_TITLE = "\x1b[<0;3;29M\x1b[<32;60;29M\x1b[<0;60;29m"
_DRAG_TRANSCRIPT = "\x1b[<0;3;15M\x1b[<32;119;15M\x1b[<0;119;15m"

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg(_SENTENCE),
    turn_completed(),
    {"release": 5},
    "/log-level\r",
    _DRAG_TRANSCRIPT,
    _DRAG_TITLE,
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
