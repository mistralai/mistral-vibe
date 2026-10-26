"""While a turn runs the footer's model is not a link: hovering and clicking it leave /model closed."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
settle_per_key = True

# The footer is row 40; "mistral-medium-3.5[medium]" fills columns 74-99 of 120.
_ROW = 40
_HOVER = f"\x1b[<35;85;{_ROW}M"
_CLICK = f"\x1b[<0;85;{_ROW}M\x1b[<0;85;{_ROW}m"

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    _HOVER,
    _CLICK,
]

screen_contains = ("Working on it.",)
screen_excludes = ("Select Model",)
