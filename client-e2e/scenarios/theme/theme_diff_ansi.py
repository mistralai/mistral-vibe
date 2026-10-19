"""Switching to an ANSI theme restyles a diff already in the transcript."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    edit_file,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "edit the file"
_OLD = "alpha\nbeta\ngamma\ndelta\n"
_NEW = "alpha\nBETA\ngamma\ndelta\n"
# The lone edit stays ungrouped, so its own header row unfolds the diff.
_EXPAND_EDIT = "\x1b[<0;1;11M\x1b[<0;1;11m"

handshake = {
    "config/read": {"config": {"theme": "textual-dark"}},
    "runtime/read": {"runtime": {"config": {"theme": "textual-dark"}}},
    "config/write": {"runtime": {"config": {"theme": "ansi-dark"}}},
}

capture_startup = False
# From textual-dark, thirteen rows up is ansi-dark.
timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    edit_file("notes.txt", [(3, _OLD, _NEW)]),
    assistant_msg("Done."),
    turn_completed(),
    _EXPAND_EDIT,
    "/theme",
    "\r",
    "\x1b[A" * 13,
    "\r",
]
