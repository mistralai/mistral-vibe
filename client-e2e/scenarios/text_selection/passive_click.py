"""While a selection shows, only a result's own header row folds it (Python `_click_is_passive`)."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    edit_file,
    manual_shell,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "edit the file"
_OLD = "alpha\nbeta\ngamma\ndelta\n"
_NEW = "alpha\nBETA\ngamma\ndelta\n"


def _select(row: int) -> str:
    return f"\x1b[<0;3;{row}M\x1b[<32;6;{row}M\x1b[<0;6;{row}m"


def _click(column: int, row: int) -> str:
    return f"\x1b[<0;{column};{row}M\x1b[<0;{column};{row}m"


# After Ctrl+O: group 15, notes 16, its diff 17-21, todo 22-24, "Done." 26, shell gap 27, shell 28.
timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    edit_file("notes.txt", [(3, _OLD, _NEW)]),
    edit_file("todo.txt", [(1, "one\n", "ONE\n")]),
    assistant_msg("Done."),
    turn_completed(),
    manual_shell("printf 'one\\ntwo'", "one\ntwo\n"),
    "\x0f",
    _select(26),
    _click(12, 19),
    _select(26),
    _click(2, 15),
    _select(26),
    _click(2, 27),
    _select(26),
    _click(2, 28),
    # Folding the shell body leaves the rows above it in place.
    _select(26),
    _click(2, 16),
]

capture_steps = {3, 5, 7, 9, 11}
