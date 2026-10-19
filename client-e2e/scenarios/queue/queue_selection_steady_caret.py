"""Queue selection hides a steady caret, and leaving it restores the steady caret."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
# textual-dark draws the block caret in a visible color.
_CONFIG = {"cursorBlink": False, "theme": "textual-dark"}
handshake = {
    "config/read": {"config": _CONFIG},
    "runtime/read": {"runtime": {"config": _CONFIG}},
}

settle_per_key = True
capture_steps = {2, 3, 4}
timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "second\r",
    "\x1b[A",
    "\x1b[B",
]
