"""Group headers count distinct files for reads, edits and writes, not one per call."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    edit_file,
    read_file,
    turn_completed,
    turn_started,
    user_msg,
    write_file,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "fix the bug"

screen_contains = ("Read 2 files, edited 1 file, wrote 1 file, ran 1 command",)

# Four reads of two files, three edits and two writes of one file each.
timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    read_file("app.py", "   1→x = 1", num_lines=1),
    edit_file("app.py", [(1, "x = 1", "x = 2")]),
    read_file("utils.py", "   1→y = 1", num_lines=1),
    edit_file("app.py", [(1, "x = 2", "x = 3")]),
    read_file("app.py", "   1→x = 3", num_lines=1),
    edit_file("app.py", [(1, "x = 3", "x = 4")]),
    read_file("utils.py", "   1→y = 1", num_lines=1),
    write_file("NOTES.md", "draft"),
    write_file("NOTES.md", "final"),
    bash("pytest -q", "1 passed"),
    assistant_msg("Fixed."),
    turn_completed(),
]
