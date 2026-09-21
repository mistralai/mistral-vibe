"""An auto-compaction mid-turn hands the session off via `session/compacted`."""

from __future__ import annotations

from typing import Any

from e2e.app_server.events import (
    QUEUE_ITEM_ID,
    SESSION_ID,
    TURN_ID,
    assistant_msg,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import AppServerEvent, Timeline

# The compacted replacement session a `session/compacted` handoff moves to.
_COMPACTED = "00000000-0000-4000-8000-000000000002"
# The turn the follow-up prompt opens on the compacted session.
_TURN2 = "00000000-0000-4000-8000-000000000004"
_TS = 1787821245000


def _message(
    entry_id: str, session_id: str, role: str, text: str, turn_id: str = TURN_ID
) -> dict[str, Any]:
    return {
        "id": entry_id,
        "sessionId": session_id,
        "turnId": turn_id,
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


def _state(event_id: int) -> dict[str, Any]:
    """The replacement state a handoff carries (Python `handoff_active_turn`)."""
    return {
        "format": "vibe.public-session-state/v1",
        "eventId": event_id,
        "session": {
            "id": _COMPACTED,
            "rootSessionId": SESSION_ID,
            "parentSessionId": SESSION_ID,
            "title": None,
            "preview": "",
            "status": {"type": "idle"},
            "createdAt": 1787821243784,
            "updatedAt": _TS,
            "cwd": "/workspace",
        },
        # Compaction shrinks the model context, not the session log: the
        # history page keeps the conversation, rebound to the surviving id.
        "history": [
            _message("handoff-user", _COMPACTED, "user", "hello"),
            _message("handoff-assistant", _COMPACTED, "assistant", "Hi there."),
            _checkpoint(_COMPACTED),
        ],
        "historyBeforeCursor": None,
        "turns": [],
        "activeCallbacks": [],
        "retrying": None,
    }


# The auto-compaction path (`CompactEndEvent` in vibe/app_server/_turns.py)
# emits the handoff mid-turn and the turn completes on the compacted session.
# The replay server stamps the batch's eventIds in order — turn/started, the
# queue-drain, both messages, then this notification — so its watermark is 5,
# and `state.eventId` must match it (Python `_validate_handoff`).
_compacted: AppServerEvent = {
    "method": "session/compacted",
    "params": {
        "sessionId": _COMPACTED,
        "oldSessionId": SESSION_ID,
        "emittedAt": _TS,
        "state": _state(5),
        "sessionLog": {"enabled": False},
        "summaryLength": 42,
    },
}

# The turn completes on the compacted session (Python `_next_event_id` checks
# the notification's session against the state the handoff installed).
_completed_on_compacted: AppServerEvent = {
    "method": "turn/completed",
    "params": {
        "sessionId": _COMPACTED,
        "emittedAt": _TS,
        "turn": {
            "id": TURN_ID,
            "sessionId": _COMPACTED,
            "status": "completed",
            "startedAt": _TS,
            "completedAt": _TS,
            "error": None,
            "stopReason": None,
            "queueItemId": QUEUE_ITEM_ID,
        },
    },
}


def _entry_added(entry: dict[str, Any]) -> AppServerEvent:
    """Announce one history entry on the session it was rebound to."""
    return {
        "method": "history/entryAdded",
        "params": {
            "sessionId": entry["sessionId"],
            "emittedAt": _TS,
            "turnId": entry["turnId"],
            "entry": entry,
        },
    }


def _turn2(*, completed: bool) -> AppServerEvent:
    """The follow-up turn runs on the compacted session the handoff installed."""
    return {
        "method": "turn/completed" if completed else "turn/started",
        "params": {
            "sessionId": _COMPACTED,
            "emittedAt": _TS,
            "turn": {
                "id": _TURN2,
                "sessionId": _COMPACTED,
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
    _compacted,
    _completed_on_compacted,
    # The follow-up prompt runs on the compacted session the handoff installed
    # (Python `replace_state`): a client that missed the handoff enqueues this
    # against the dead `…0001` id instead.
    "again\r",
    _turn2(completed=False),
    _entry_added(_message("handoff-again-user", _COMPACTED, "user", "again", _TURN2)),
    _entry_added(
        _message(
            "handoff-again-assistant", _COMPACTED, "assistant", "Round two.", _TURN2
        )
    ),
    _turn2(completed=True),
]
