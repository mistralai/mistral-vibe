from __future__ import annotations

from e2e.app_server.events import edit_file, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "rename the marker everywhere"

# Two changes far enough apart to split into two hunks, separated by a gap row.
_MULTI_OLD = "one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n"
_MULTI_NEW = "one\nTWO\nthree\nfour\nfive\nsix\nseven\neight\nNINE\nten\n"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    edit_file(
        "multi.txt",
        [(10, _MULTI_OLD, _MULTI_NEW), (40, "alpha\n", "alpha\nomega\n")],
        replace_all=True,
    ),
    # No start line: the gutter drops its line numbers.
    edit_file("unanchored.txt", [(None, "left\n", "right\n")]),
    turn_completed(),
]
