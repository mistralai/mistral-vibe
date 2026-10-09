"""Scenario: hovering any row of a wrapped markdown link highlights every row of it."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Action, Timeline

_PROMPT = "link the tokio docs"
_URL = "https://docs.rs/tokio/latest/tokio/macro.select.html"
_LABEL = (
    "the official Tokio documentation for the select macro, which waits on"
    " multiple concurrent branches and returns when the first one completes"
)
_ANSWER = f"Read [{_LABEL}]({_URL}) before refactoring."

expected_actions = {"rust": [Action("open_url", _URL)]}

# The label wraps from one-based cell (8, 15) to (30, 16); SGR button 35 is a bare pointer move.
_HOVER_TAIL = "\x1b[<35;10;16M"
_CLICK_TAIL = "\x1b[<0;10;16M\x1b[<0;10;16m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _HOVER_TAIL,
    _CLICK_TAIL,
]
