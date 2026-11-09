"""Undo/redo refreshes completion, restores input modes, and includes priority Ctrl+D."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_UNDO = "\x1b[122;9u"
_REDO = "\x1b[121;9u"

expected_clipboard = "help "
request_methods = {"telemetry/record"}

timeline: Timeline = [
    "/he",
    _UNDO,
    _UNDO,
    _REDO,
    _REDO,
    "\t",
    _UNDO,
    _REDO,
    "\x01\x04",
    _UNDO,
    "\x1b[18~\x1b[99;9u",
]
