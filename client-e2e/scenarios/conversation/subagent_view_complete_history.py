"""Opening a child whose history outgrows the tail pages in the older entries, in order."""

from __future__ import annotations

from e2e.app_server.events import (
    child_entry,
    child_history_state,
    child_session_updated,
    subagent,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, resize

_PROMPT = "delegate the investigation"
_CHILD_SESSION_ID = "child-session"


def _message(index: int, role: str, source: str | None = None) -> dict:
    entry = {
        "id": f"message-{index}",
        "type": "message",
        "role": role,
        "content": [{"type": "text", "text": f"message {index}"}],
        "generationStatus": "completed",
        "turnId": "turn-child",
        "createdAt": 1_787_593_260_000 + index,
        "updatedAt": 1_787_593_260_000 + index,
        "relatedEntryId": None,
    }
    if source is not None:
        entry["source"] = source
    return child_entry(entry, _CHILD_SESSION_ID)


# The tail read returns a full window (20 entries), so the client pages the rest backwards from its first id.
_OLDER = [
    _message(0, "user", "turn_start"),
    _message(1, "assistant"),
    _message(2, "user", "turn_steer"),
]
_TAIL = [_message(index, "assistant") for index in range(3, 23)]

handshake = {
    f"session/read#{_CHILD_SESSION_ID}": child_history_state(_CHILD_SESSION_ID, _TAIL),
    f"session/history/list#{_CHILD_SESSION_ID}": {"items": _OLDER, "nextCursor": None},
}

request_methods = frozenset({"session/read", "session/history/list"})

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

screen_contains = ("message 0", "message 1", "message 2", "message 22")

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent("find the grouping code"),
    child_session_updated(),
    turn_completed(),
    {"release_after": 6},
    resize(80, 120),  # tall enough to show the whole child transcript at once
    "\x1b[B",  # Down — focus the list on the Main row
    "\x1b[B",  # Down — highlight the child row
    "\r",  # Enter — tail read, then the older page merged ahead of it
]
