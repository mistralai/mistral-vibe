"""Ctrl+O toggles group folding and individual result bodies together."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    read_file,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "inspect and run"

# The group holds two collapsible results (bash output and a file read), so
# Ctrl+O expands the group content and the result bodies in one keypress and
# the second press folds both levels back.
timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash("echo one", "one"),
    read_file("notes.txt", "   1→a note", num_lines=1),
    assistant_msg("Done."),
    turn_completed(),
    "\x0f",
    "\x0f",
]
