"""Rewinding to a prompt with a collapsed paste keeps its placeholder.

The rewind panel title previews it, and the input gets it back as a placeholder.
"""

from __future__ import annotations

import json

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.events import (
    assistant_msg,
    paste,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_state = json.loads(FIXTURE_PATH.read_text())["handshake"]["session/start"]["state"]

_TEXT = "\n".join(f"row {number}" for number in range(12))
_PROMPT = "summarize "
_SENT = f"{_PROMPT}{_TEXT}"


def _fingerprint(text: str) -> str:
    """FNV-1a 64, as the client marks a collapsed paste."""
    value = 0xCBF29CE484222325
    for byte in text.encode():
        value = ((value ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{value:016x}"


_MESSAGE = user_msg(_SENT)
_MESSAGE["params"]["entry"]["userDisplayContent"] = {
    "version": "1",
    "host": "vibe",
    "content": [
        {
            "type": "vibe.collapsed_paste",
            "start": len(_PROMPT),
            "chars": len(_TEXT),
            "hash": _fingerprint(_TEXT),
            "pasted": len(_TEXT) + 1,
        }
    ],
}

capture_startup = False
capture_steps = {2, 4, 5}
screen_contains = {"rust": ("> summarize [Pasted 74 characters]",)}
screen_excludes = {"rust": ("row 5",)}
# The restored placeholder is a mention: copying it copies the text it stands for.
expected_clipboard = _SENT

# The prompt is typed as sent: its paste ends with a newline the submit trims.
timeline: Timeline = [
    _PROMPT + paste(f"{_TEXT}\n"),
    "\r",
    turn_started(),
    _MESSAGE,
    assistant_msg("Twelve rows."),
    turn_completed(),
    "/rewind\r",
    "\r",
    "\r",
    "\x1b[18~\x19",
]

handshake = {
    "workspace/prompt/prepare": {
        "prompt": {
            "displayText": _SENT,
            "promptText": _SENT,
            "images": [],
            "autoTitle": None,
            "mentions": {"count": 0, "contextTypes": {}, "fileExtensions": {}},
        }
    },
    "session/rewind/read": {"hasFileChanges": False, "paths": []},
    "session/rewind": {
        "message": _SENT,
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
