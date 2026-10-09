"""Edit and write results start folded and toggle open and closed on click."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    edit_file,
    turn_completed,
    turn_started,
    user_msg,
    write_file,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "edit then write"

_OLD = "alpha\nbeta\ngamma\n"
_NEW = "alpha\nBETA\ngamma\n"
_CONTENT = "def hi(name: str) -> str:\n    return name\n"


def _click(row: int) -> str:
    return f"\x1b[<0;1;{row}M\x1b[<0;1;{row}m"


timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    edit_file("notes.txt", [(1, _OLD, _NEW)]),
    write_file("greet.py", _CONTENT),
    assistant_msg("Done."),
    turn_completed(),
    _click(15),
    _click(16),
    _click(21),
    _click(16),
    _click(17),
]
