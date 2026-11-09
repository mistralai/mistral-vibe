"""Rewinding to a bare /skill message filters the reopened completion menu and highlights that skill."""

from __future__ import annotations

import json

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.events import (
    assistant_msg,
    loaded_skill,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_state = json.loads(FIXTURE_PATH.read_text())["handshake"]["session/start"]["state"]

_NAME = "code-review"
_PROMPT = f"/{_NAME}"
_BODY = f'<skill_content name="{_NAME}">\nReview twice.\n</skill_content>'
_MESSAGE_ID = "user-code-review"
_SKILLS = [
    {
        "name": _NAME,
        "description": "Review the current change.",
        "prompt": "",
        "userInvocable": True,
        "source": "local",
    },
    {
        "name": f"{_NAME}-strict",
        "description": "Review the current change, strictly.",
        "prompt": "",
        "userInvocable": True,
        "source": "local",
    },
]

handshake = {
    "runtime/read": {"runtime": {"skills": _SKILLS}},
    "workspace/prompt/prepare": {
        "prompt": {
            "displayText": _PROMPT,
            "promptText": _PROMPT,
            "images": [],
            "autoTitle": None,
            "mentions": {"count": 0, "contextTypes": {}, "fileExtensions": {}},
        }
    },
    "session/rewind/read": {"hasFileChanges": False, "paths": []},
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
    },
}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT, entry_id=_MESSAGE_ID),
    loaded_skill(_NAME, _BODY, related_entry_id=_MESSAGE_ID),
    assistant_msg("Review complete."),
    turn_completed(),
    "/rewind\r",
    "\r",
    "\r",
]
