"""A rewind whose file restore partly fails rebuilds the session, then warns once per unrestored file and returns the prompt."""

from __future__ import annotations

import json

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_state = json.loads(FIXTURE_PATH.read_text())["handshake"]["session/start"]["state"]

# Enter picks the user message, Enter rewinds in place and restores its files.
timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi", entry_id="user-hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
    "/rewind\r",
    "\r",
    "\r",
]

handshake = {
    "session/rewind/read": {"hasFileChanges": True, "paths": ["src/a.py", "src/b.py"]},
    "session/rewind": {
        "message": "hi",
        "restoreErrors": [
            "Failed to restore src/a.py: permission denied",
            "Failed to restore src/b.py: file is locked",
        ],
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

# The rebuilt transcript is empty, then one warning row per unrestored file, in order.
screen_rows = {
    6: "⚠ Failed to restore src/a.py: permission denied",
    8: "⚠ Failed to restore src/b.py: file is locked",
    35: "> hi",
}
screen_excludes = ("Hi. What do you need?", "Rewind to:")
