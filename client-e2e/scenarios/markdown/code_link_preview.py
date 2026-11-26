"""Scenario: resting on a link to a workspace file previews its code; wheel and scrollbar scroll it, Esc closes it."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, advance, take_snapshot

_PROMPT = "where is the entry point"
_ANSWER = "See [greet.py:12](code_preview/greet.py:12) for the entry point."

# The label spans one-based columns 7-17 on the answer row; the full-width popup opens under it.
_ROW, _COL = 11, 10
_HOVER = f"\x1b[<35;{_COL};{_ROW}M"
_INTO_POPUP = f"\x1b[<35;{_COL + 10};{_ROW + 4}M"
_WHEEL_DOWN = f"\x1b[<65;{_COL + 10};{_ROW + 4}M"
# The scrollbar sits in column 119, rows 13-28; dragging its thumb to the bottom shows the last lines.
_DRAG_TO_END = "\x1b[<0;119;20M\x1b[<32;119;28M\x1b[<0;119;28m"

screen_excludes = ("code_preview/greet.py",)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _HOVER,
    advance(300),
    take_snapshot(),
    _INTO_POPUP,
    _WHEEL_DOWN,
    take_snapshot(),
    _DRAG_TO_END,
    take_snapshot(),
    "\x1b",
]
