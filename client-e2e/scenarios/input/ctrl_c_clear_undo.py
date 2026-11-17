"""Undo brings back the draft and mode Ctrl+C cleared, and Redo clears it again."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_UNDO = "\x1b[122;9u"
_REDO = "\x1b[121;9u"

handshake = {"session/shellCommand": {"accepted": True, "lastEventId": 0}}
request_methods = {"session/shellCommand"}

timeline: Timeline = ["!echo hello", "\x03", _UNDO, _REDO, _UNDO, "\r"]
