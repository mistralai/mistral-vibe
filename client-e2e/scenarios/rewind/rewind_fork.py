from __future__ import annotations

import json

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_state = json.loads(FIXTURE_PATH.read_text())["handshake"]["session/start"]["state"]
_forked = json.loads(json.dumps(_state))
_forked["session"]["id"] = "00000000-0000-4000-8000-000000000002"
_forked["session"]["parentSessionId"] = _state["session"]["id"]

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
    "/rewind\r",
    "2",
    "2",
]

handshake = {
    "session/rewind/read": {"hasFileChanges": True, "paths": ["src/a.py"]},
    "session/rewind": {
        "message": "hi",
        "restoreErrors": [],
        "restoredPaths": [],
        "state": _forked,
        "sessionLog": {
            "enabled": False,
            "sessionId": None,
            "persisted": False,
            "path": None,
            "title": None,
            "needsInitialAutoTitle": False,
        },
    },
}
