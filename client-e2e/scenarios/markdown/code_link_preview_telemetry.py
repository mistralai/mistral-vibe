"""Scenario: a missing file records only `done` as an error; a seen preview records `opened`, a copy out of it, and `done` on Esc."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, advance

request_methods = {"telemetry/record"}
env = {"SSH_TTY": "/dev/pts/0"}

_PROMPT = "where is the entry point"
_ANSWER = (
    "See [greet.py:12](code_preview/greet.py:12) or [gone.py](code_preview/gone.py)."
)

# On row 11, "greet.py:12" spans columns 7-17 and "gone.py" columns 22-28.
_HOVER_MISSING = "\x1b[<35;24;11M"
_HOVER = "\x1b[<35;10;11M"
_INTO_POPUP = "\x1b[<35;20;15M"
_WHEEL_DOWN = "\x1b[<65;20;15M"
_DRAG_TWO_LINES = "\x1b[<0;7;16M\x1b[<32;50;17M\x1b[<0;50;17m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _HOVER_MISSING,
    advance(300),
    _HOVER,
    advance(300),
    _INTO_POPUP,
    _WHEEL_DOWN,
    _DRAG_TWO_LINES,
    advance(1000),
    "\x1b",
]
