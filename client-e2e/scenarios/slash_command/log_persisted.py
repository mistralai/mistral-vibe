"""/log prints a persisted session's log directory with a hint to share it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/log\r"]

handshake = {
    "session/log/read": {
        "log": {
            "enabled": True,
            "sessionId": "00000000-0000-4000-8000-000000000001",
            "persisted": True,
            "path": "/var/log/vibe/session_20260910_090045_00000000",
            "title": None,
            "needsInitialAutoTitle": False,
        }
    }
}

screen_contains = (
    "Current Log Directory",
    "/var/log/vibe/session_20260910_090045_00000000",
    "You can send this directory to share your interaction.",
)
