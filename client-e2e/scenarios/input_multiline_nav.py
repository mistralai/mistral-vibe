from __future__ import annotations

from e2e.app_server.scenario import Timeline

_UP = "\x1b[A"

timeline: Timeline = [
    "ab",
    "\n",  # newline -> caret on line 2
    "cd",  # draft is "ab\ncd", caret at end of line 2
    _UP,  # first line? no -> caret moves up to line 1
    "X",  # inserts on line 1: "abX\ncd"
]
