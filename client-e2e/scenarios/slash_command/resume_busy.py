"""A resume rejected because the session is open elsewhere says how to continue."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"
_SAVED = "00000000-0000-4000-8000-000000000003"

handshake = {
    f"session/resume#{_SAVED}": {
        "error": {
            "code": "conflict",
            "message": f"Session is already open: {_SAVED}",
            "data": {"harnessCode": "session_busy", "details": {"session_id": _SAVED}},
        }
    }
}
request_methods = {"session/resume"}

# Enter submits /resume, Up selects the saved session, the next Enter resumes it.
timeline: Timeline = ["/resume", "\r", "\x1b[A", "\r", _ESCAPE]

capture_startup = False
capture_steps = {1, 2, 3}
screen_contains = ("open in another Vibe process",)
