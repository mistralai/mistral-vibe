"""Scenario: code in the preview popup selects and copies like other text, without its line numbers."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, advance, take_snapshot

env = {"SSH_TTY": "/dev/pts/0"}
expected_clipboard = "def main() -> None:\n    names = sys.argv[1:] or [DEFAULT_NAME]"

_PROMPT = "where is the entry point"
_ANSWER = "See [greet.py:12](code_preview/greet.py:12) for the entry point."

_HOVER = "\x1b[<35;10;11M"
_INTO_POPUP = "\x1b[<35;20;15M"
# Code starts at column 7 after the line numbers; lines 12 and 13 sit on rows 16 and 17.
_DRAG_TWO_LINES = "\x1b[<0;7;16M\x1b[<32;50;17M\x1b[<0;50;17m"
# A drag starting on the line numbers selects nothing.
_DRAG_GUTTER = "\x1b[<0;3;17M\x1b[<32;3;18M\x1b[<0;3;18m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg(_ANSWER),
    turn_completed(),
    _HOVER,
    advance(300),
    _INTO_POPUP,
    _DRAG_TWO_LINES,
    take_snapshot(),
    _DRAG_GUTTER,
    take_snapshot(),
]
