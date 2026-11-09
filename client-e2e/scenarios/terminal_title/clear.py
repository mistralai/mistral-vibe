"""Clearing a renamed conversation resets the terminal title."""

from __future__ import annotations

import json

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.scenario import Timeline

_state = json.loads(FIXTURE_PATH.read_text())["handshake"]["session/start"]["state"]

handshake = {
    "session/rename": {"title": "Renamed session", "updatedAt": None},
    "session/history/clear": {
        "state": _state,
        "sessionLog": {
            "enabled": True,
            "sessionId": _state["session"]["id"],
            "persisted": False,
            "path": None,
            "title": None,
            "needsInitialAutoTitle": True,
        },
    },
}

timeline: Timeline = ["/rename Renamed session\r", "/clear\r"]
expected_titles = ("Vibe", "Renamed session", "Vibe")
