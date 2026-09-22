"""Toast text selects by word, row, and drag without dismissing the toast."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
clipboard_clients = {"rust"}
# The drag comes last, so it owns the final clipboard payload.
expected_clipboard = "Slash"
screen_contains = {"rust": ("Slash commands cannot be queued",)}

# The first toast text row is terminal row 32, running from column 62. SGR
# coordinates are 1-based; column 68 sits inside "commands". The drag starts on
# another cell, so it opens its own click chain instead of extending this one.
_CLICK = "\x1b[<0;68;32M\x1b[<0;68;32m"
_DRAG = "\x1b[<0;62;32M\x1b[<32;66;32M\x1b[<0;66;32m"

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "/resume\r",
    _CLICK * 2,
    _CLICK,
    _DRAG,
]
