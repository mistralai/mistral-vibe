"""Rewinding to a message that starts with a slash loads it back as a prompt, not a command."""

from __future__ import annotations

import json

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_state = json.loads(FIXTURE_PATH.read_text())["handshake"]["session/start"]["state"]

_MESSAGE = "/config"

capture_startup = False
request_methods = {"workspace/prompt/prepare", "telemetry/record"}
screen_contains = {"rust": ("> /config",)}

timeline: Timeline = [
    "config\x01/\r",
    turn_started(),
    user_msg(_MESSAGE),
    assistant_msg("That was a message."),
    turn_completed(),
    "/rewind\r",
    "\r",
    "\r",
]

handshake = {
    "session/rewind/read": {"hasFileChanges": False, "paths": []},
    "session/rewind": {
        "message": _MESSAGE,
        "restoreErrors": [],
        "restoredPaths": [],
        "state": _state,
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
