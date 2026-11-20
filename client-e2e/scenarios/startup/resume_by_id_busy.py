"""A `--resume <id>` rejected because another Vibe holds the session says where to close it, keeping the fresh session."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SAVED = "00000000-0000-4000-8000-000000000003"

client_args = ("--resume", _SAVED)
handshake = {
    "session/resume": {
        "error": {
            "code": "conflict",
            "message": f"Session is already open: {_SAVED}",
            "data": {"harnessCode": "session_busy", "details": {"session_id": _SAVED}},
        }
    }
}
request_methods = {"session/resume"}
timeline: Timeline = []

screen_contains = ("open in another Vibe process",)
screen_excludes = (
    "Session is already open",
    "Failed to resume session",
    "Resuming session",
    "Saved session restored.",
)
