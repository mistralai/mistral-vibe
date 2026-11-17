"""Undo brings back the draft and mode a double Escape cleared."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"
_UNDO = "\x1b[122;9u"

handshake = {"session/shellCommand": {"accepted": True, "lastEventId": 0}}
request_methods = {"session/shellCommand"}

timeline: Timeline = ["!echo hello", _ESCAPE * 2, _UNDO, "\r"]
