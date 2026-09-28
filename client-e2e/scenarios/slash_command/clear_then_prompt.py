"""`/clear` replaces the session, so the next prompt must reach the new one."""

from __future__ import annotations

import json
from typing import Any

from e2e.app_server.config import FIXTURE_PATH
from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import AppServerEvent, Timeline

_CLEARED_SESSION_ID = "00000000-0000-4000-8000-000000000003"

_fixture = json.loads(FIXTURE_PATH.read_text())["handshake"]
_state = _fixture["session/start"]["state"]


def _rebind(value: Any) -> Any:
    """Re-address a built event: it belongs to the session the clear handed out."""
    if isinstance(value, dict):
        return {
            key: _CLEARED_SESSION_ID if key == "sessionId" else _rebind(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_rebind(item) for item in value]
    return value


def _cleared_turn() -> list[AppServerEvent]:
    events = [
        _rebind(event)
        for event in (
            turn_started(),
            user_msg("hello again"),
            assistant_msg("Hello again."),
        )
    ]
    snapshot = json.loads(json.dumps(_cleared))
    snapshot["eventId"] += len(events)
    snapshot["history"] = [
        event["params"]["entry"]
        for event in events
        if event["method"] == "history/entryAdded"
    ]
    events.append({
        "method": "session/snapshot",
        "params": {"sessionId": _CLEARED_SESSION_ID, "state": snapshot},
    })
    events.append(_rebind(turn_completed()))
    return events


_first_turn = [
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
]

# The replay server numbers events as it emits them and the loader adds one
# queue drain per started turn, so the cleared state resumes after that many.
_cleared = json.loads(json.dumps(_state))
_cleared["session"]["id"] = _CLEARED_SESSION_ID
_cleared["history"] = []
_cleared["eventId"] = len(_first_turn) + 1

timeline: Timeline = [
    "hi\r",
    *_first_turn,
    "/clear\r",
    "hello again\r",
    *_cleared_turn(),
]

handshake = {
    "session/history/clear": {
        "state": _cleared,
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
