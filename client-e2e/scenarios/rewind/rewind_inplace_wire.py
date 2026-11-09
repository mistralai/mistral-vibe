"""Keep in current session sends an in-place rewind without file restore and stays on the same session."""

from __future__ import annotations

import json

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_state = json.loads(FIXTURE_PATH.read_text())["handshake"]["session/start"]["state"]

# The rewound prompt returns to the composer; resubmitting it shows which session is live.
timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi", entry_id="user-hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
    "/rewind\r",
    "\r",
    "\r",
    "\r",
    turn_started(),
    user_msg("hi", entry_id="user-hi-again"),
    assistant_msg("Hi again."),
    turn_completed(),
]

handshake = {
    "session/rewind/read": {"hasFileChanges": False, "paths": []},
    "session/rewind": {
        "message": "hi",
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
request_methods = {"session/rewind"}

capture_steps = {3, 4}
screen_excludes = ("Forked to a new session",)
