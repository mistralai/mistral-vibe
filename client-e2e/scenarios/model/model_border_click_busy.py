"""While a turn runs the input box's model is not a link: hovering and clicking it leave /model closed."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

# The input box's top border is row 35; "mistral-medium-3.5[medium]" fills columns 78-103 of 120.
_ROW = 35
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
