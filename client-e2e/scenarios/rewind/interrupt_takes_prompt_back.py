"""Esc before any answer interrupts and rewinds the turn: the prompt returns to the input, no marker, no panel."""

from __future__ import annotations

import json

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.events import turn_interrupted, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"
_state = json.loads(FIXTURE_PATH.read_text())["handshake"]["session/start"]["state"]

# The server closes the interrupted turn; only then is the rewind sent.
on_request = {"turn/interrupt": [turn_interrupted()]}

timeline: Timeline = [
    "fix the parser\r",
    turn_started(),
    user_msg("fix the parser", entry_id="user-fix"),
    {"release": 3},
    _ESCAPE,
]

handshake = {
    "session/rewind": {
        "message": "fix the parser",
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
request_methods = {"turn/interrupt", "session/rewind/read", "session/rewind"}

screen_contains = ("fix the parser",)
screen_excludes = ("Edit message from here", "Keep in current session", "Interrupted")
