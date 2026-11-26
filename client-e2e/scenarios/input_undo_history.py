"""Submit and history recall clear undo/redo; edits of recalled input remain undoable."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_UNDO = "\x1b[122;9u"
_REDO = "\x1b[121;9u"
_UP = "\x1b[A"
_DOWN = "\x1b[B"

expected_clipboard = "draft"
request_methods = {"telemetry/record"}

timeline: Timeline = [
    "sent\r",
    turn_started(),
    user_msg("sent"),
    assistant_msg("received"),
    turn_completed(),
    _UNDO + _REDO,
    "draft",
    _UP + _UP,
    _UNDO + _REDO,
    _DOWN,
    _UNDO + _REDO,
    "X",
    _UNDO,
    "\x1b[18~\x1b[99;9u",
]
