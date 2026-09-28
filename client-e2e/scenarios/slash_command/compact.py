"""A manual `/compact` pins the compaction RPC and settles on its response."""

from __future__ import annotations

from typing import Any

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

# The live session is `session/start`'s (…0001); the canned `session/read`
# state (…0003, "Saved implementation") is a different, unrelated session.
_LIVE = "00000000-0000-4000-8000-000000000001"
_TS = 1787821245000


def _message(entry_id: str, session_id: str, role: str, text: str) -> dict[str, Any]:
    return {
        "id": entry_id,
        "sessionId": session_id,
        "turnId": None,
        "createdAt": _TS,
        "updatedAt": _TS,
        "generationStatus": "completed",
        "relatedEntryId": None,
        "type": "message",
        "role": role,
        "content": [{"type": "text", "text": text}],
    }


def _checkpoint(session_id: str) -> dict[str, Any]:
    """The compaction checkpoint a compacted session opens with
    (`rebind_history_with_checkpoint` in vibe/app_server/_root_session.py).
    """
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


def _history(session_id: str) -> list[dict[str, Any]]:
    # Compaction shrinks the model context, not the session log: the history
    # page keeps the conversation, rebound to the surviving session id.
    return [
        _message("compacted-user", session_id, "user", "hello"),
        _message("compacted-assistant", session_id, "assistant", "Hi there."),
        _checkpoint(session_id),
    ]


def _session(session_id: str) -> dict[str, Any]:
    return {
        "id": session_id,
        "rootSessionId": _LIVE,
        "parentSessionId": None if session_id == _LIVE else _LIVE,
        "title": None,
        "preview": "",
        "status": {"type": "idle"},
        "createdAt": 1787821243784,
        "updatedAt": _TS,
        "cwd": "/workspace",
    }


def _state(session_id: str, event_id: int) -> dict[str, Any]:
    return {
        "format": "vibe.public-session-state/v1",
        "eventId": event_id,
        "session": _session(session_id),
        "history": _history(session_id),
        "historyBeforeCursor": None,
        "turns": [],
        "activeCallbacks": [],
        "retrying": None,
    }


# The manual same-session compaction answer (`_compact` → `replace_idle` in
# vibe/app_server/_handler.py): the session id stays, the state carries the
# checkpointed history, and this path emits no notification of its own — the
# client settles on the response (Python `_run_compact`'s finally block).
# Pinning it keeps the client on the live session instead of the unrelated
# `…0003` state the default fixture derivation would deep-copy from
# `session/read`.
handshake = {
    "session/compact": {
        "summary": "Compacted summary.",
        "state": _state(_LIVE, 5),
        "sessionLog": {"enabled": False},
    }
}

timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Hi there."),
    turn_completed(),
    "/compact\r",
]
