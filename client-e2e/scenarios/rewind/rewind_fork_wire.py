"""Fork to a new session sends a non-in-place rewind, names both short ids, and switches sessions."""

from __future__ import annotations

import json

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_state = json.loads(FIXTURE_PATH.read_text())["handshake"]["session/start"]["state"]
_forked = json.loads(json.dumps(_state))
_FORKED = "22222222-0000-4000-8000-000000000002"
_forked["session"]["id"] = _FORKED
_forked["session"]["parentSessionId"] = _state["session"]["id"]

# The rewound prompt returns to the composer; resubmitting it shows which session is live.
timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi", entry_id="user-hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
    "/rewind\r",
    "1",
    "2",
    "\r",
    turn_started(session_id=_FORKED),
    user_msg("hi", entry_id="user-hi-again", session_id=_FORKED),
    assistant_msg("Hi again.", session_id=_FORKED),
    turn_completed(session_id=_FORKED),
]

handshake = {
    "session/rewind/read": {"hasFileChanges": False, "paths": []},
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
request_methods = {"session/rewind"}

capture_steps = {3, 4}
screen_contains = ("session: 00000000 (before rewind) → 22222222 (after rewind)",)
