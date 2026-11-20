"""Ctrl+C before the server starts the first turn interrupts it once it starts and takes its prompt back."""

from __future__ import annotations

import json

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.events import turn_interrupted, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "write the migration"
_state = json.loads(FIXTURE_PATH.read_text())["handshake"]["session/start"]["state"]

# The server starts the turn after the key press, adds its prompt, then closes it as the client's interrupt asks.
timeline: Timeline = [
    f"{_PROMPT}\r",
    "\x03",
    turn_started(),
    user_msg(_PROMPT),
    turn_interrupted(),
    {"release": 4},
]

handshake = {
    "session/rewind": {
        "message": _PROMPT,
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
    }
}
request_methods = {"turn/interrupt", "session/rewind"}

screen_contains = (f"> {_PROMPT}",)
screen_excludes = ("Esc/Ctrl+C to interrupt", "Interrupted")
