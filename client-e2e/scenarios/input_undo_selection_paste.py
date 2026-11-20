"""Undo restores Unicode selections, deleted lines, and atomic multiline pastes."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_UNDO = "\x1b[122;9u"
_REDO = "\x1b[121;9u"
_SELECT_ALL = "\x1b[18~"
_COPY = "\x1b[99;9u"
_DELETE_LINE = "\x1b[107;6u"

request_methods = {"telemetry/record"}
expected_clipboard = "été 世界"

timeline: Timeline = [
    "\x1b[200~été 世界\x1b[201~",
    _SELECT_ALL + "X",
    _UNDO,
    _COPY,
    _DELETE_LINE,
    _UNDO,
    "\x1b[200~one\r\ntwo\x1b[201~",
    _UNDO,
    _REDO,
    _UNDO,
    _COPY,
]
