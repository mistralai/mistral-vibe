"""`/clear` on a persisted session names it and how to resume it."""

from __future__ import annotations

from e2e.app_server.events import SESSION_ID, cleared_state
from e2e.app_server.scenario import Timeline

request_methods = {"session/log/read", "session/history/clear"}

handshake = {
    "session/log/read": {
        "log": {
            "enabled": True,
            "sessionId": SESSION_ID,
            "persisted": True,
            "path": None,
            "title": None,
            "needsInitialAutoTitle": False,
        }
    },
    "session/history/clear": {"state": cleared_state()},
}

timeline: Timeline = ["/clear\r"]
