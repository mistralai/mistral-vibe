"""Undo batches typing and deletion; new edits invalidate redo without stealing copy."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_UNDO = "\x1b[122;9u"
_REDO = "\x1b[121;9u"
_SELECT_ALL = "\x1b[18~"
_COPY = "\x1b[99;9u"

request_methods = {"telemetry/record"}
expected_clipboard = "helloX"

timeline: Timeline = [
    "hello",
    "\x7f\x7f\x7f",
    _UNDO,
    _REDO,
    _UNDO,
    _UNDO,
    _UNDO,
    _REDO,
    _REDO,
    _UNDO,
    "X",
    _REDO,
    _SELECT_ALL + _COPY,
]
