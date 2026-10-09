"""An occurrence-only edit output with a null legacy pair still renders its diff."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    edit_file,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

screen_contains = {"rust": ("- beta", "+ BETA")}

_PROMPT = "edit the file"

_OLD = "alpha\nbeta\ngamma\n"
_NEW = "alpha\nBETA\ngamma\n"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    edit_file("notes.txt", [(1, _OLD, _NEW)], occurrences_only=True),
    assistant_msg("Done."),
    turn_completed(),
    # Ctrl+O unfolds the lone edit result.
    "\x0f",
]
