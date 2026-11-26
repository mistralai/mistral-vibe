"""Scenario: the preview stays closed until the pointer has rested on the link for 300 ms."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, advance

_PROMPT = "where is the entry point"
_ANSWER = "See [greet.py:12](code_preview/greet.py:12) for the entry point."
_HOVER = "\x1b[<35;10;11M"

screen_excludes = ("code_preview/greet.py",)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _HOVER,
    advance(200),
]
