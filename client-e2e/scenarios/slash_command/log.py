from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/log\r"]

handshake = {
    "session/log/read": {
        "log": {
            "enabled": True,
            "sessionId": "00000000-0000-4000-8000-000000000001",
            "persisted": False,
            "path": None,
            "title": None,
            "needsInitialAutoTitle": True,
        }
    }
}
