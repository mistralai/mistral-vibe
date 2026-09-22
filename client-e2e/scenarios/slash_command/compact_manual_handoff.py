"""A manual `/compact` answers with a handed-off session; the client adopts it."""

from __future__ import annotations

from typing import Any

from e2e.app_server.events import (
    QUEUE_ITEM_ID,
    TURN_ID,
    assistant_msg,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import AppServerEvent, Timeline

# The live session is `session/start`'s (…0001); `replace_idle` hands the
# manual compaction off to a fresh session (…0002) when the id would change.
_LIVE = "00000000-0000-4000-8000-000000000001"
_HANDOFF = "00000000-0000-4000-8000-000000000002"
# The turn the follow-up prompt opens on the handed-off session.
_TURN2 = "00000000-0000-4000-8000-000000000004"
_TS = 1787821245000


def _message(entry_id: str, session_id: str, role: str, text: str) -> dict[str, Any]:
    return {
        "id": entry_id,
        "sessionId": session_id,
        "turnId": TURN_ID,
        "createdAt": _TS,
        "updatedAt": _TS,
        "generationStatus": "completed",
        "relatedEntryId": None,
        "type": "message",
        "role": role,
        "content": [{"type": "text", "text": text}],
        "source": "turn_start" if role == "user" else None,
        "userDisplayContent": None,
    }


def _checkpoint(session_id: str) -> dict[str, Any]:
    """The compaction checkpoint a compacted session opens with (`rebind_history_with_checkpoint`)."""
    return {
        "id": "checkpoint:compaction:00000000000000000000000000000042",
        "sessionId": session_id,
        "turnId": None,
        "createdAt": _TS,
        "updatedAt": _TS,
        "generationStatus": "completed",
        "relatedEntryId": None,
        "type": "checkpoint",
        "kind": "compaction",
        "message": "Context compacted",
        "details": {"summaryLength": 42},
    }


def _state() -> dict[str, Any]:
    """The replacement state the manual RPC answers with (`_compact` -> `replace_idle`)."""
    return {
        "format": "vibe.public-session-state/v1",
        "eventId": 5,
        "session": {
            "id": _HANDOFF,
            "rootSessionId": _LIVE,
            "parentSessionId": _LIVE,
            "title": None,
            "preview": "",
            "status": {"type": "idle"},
            "createdAt": 1787821243784,
            "updatedAt": _TS,
            "cwd": "/workspace",
        },
        "history": [
            _message("compacted-user", _HANDOFF, "user", "hello"),
            _message("compacted-assistant", _HANDOFF, "assistant", "Hi there."),
            _checkpoint(_HANDOFF),
        ],
        "historyBeforeCursor": None,
        "turns": [],
        "activeCallbacks": [],
        "retrying": None,
    }


# The manual compaction answer carries the handed-off session: this path emits
# no notification of its own, so the client must adopt the response's state
# (Python `_session_resources.compact` -> `projection.replace_state`).
handshake = {
    "session/compact": {
        "summary": "Compacted summary.",
        "state": _state(),
        "sessionLog": {"enabled": False},
    }
}


def _entry_added(entry: dict[str, Any]) -> AppServerEvent:
    """Announce one history entry on the handed-off session."""
    return {
        "method": "history/entryAdded",
        "params": {
            "sessionId": _HANDOFF,
            "emittedAt": _TS,
            "turnId": _TURN2,
            "entry": entry,
        },
    }


def _turn2(*, completed: bool) -> AppServerEvent:
    """The follow-up turn runs on the handed-off session."""
    return {
        "method": "turn/completed" if completed else "turn/started",
        "params": {
            "sessionId": _HANDOFF,
            "emittedAt": _TS,
            "turn": {
                "id": _TURN2,
                "sessionId": _HANDOFF,
                "status": "completed" if completed else "in_progress",
                "startedAt": _TS,
                "completedAt": _TS if completed else None,
                "error": None,
                "stopReason": None,
                "queueItemId": QUEUE_ITEM_ID,
            },
        },
    }


timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Hi there."),
    turn_completed(),
    "/compact\r",
    # The follow-up prompt targets the handed-off session: a client that
    # ignored the response state enqueues against the dead `…0001` id instead.
    "again\r",
    _turn2(completed=False),
    _entry_added(_message("again-user", _HANDOFF, "user", "again")),
    _entry_added(_message("again-assistant", _HANDOFF, "assistant", "Round two.")),
    _turn2(completed=True),
]
