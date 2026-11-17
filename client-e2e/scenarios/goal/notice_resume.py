"""A resumed history replays its goal notices in place, the continuation's above its output."""

from __future__ import annotations

from typing import Any

from e2e.app_server.events import goal, goal_notice
from e2e.app_server.scenario import Timeline, take_snapshot

_ID = "00000000-0000-4000-8000-000000000003"
_TURN = "turn-saved-continuation"
_OBJECTIVE = "Make the parity suite pass"


def _message(entry_id: str, role: str, text: str, turn: str | None) -> dict[str, Any]:
    return {
        "id": entry_id,
        "sessionId": _ID,
        "turnId": turn,
        "createdAt": 1787821243000,
        "updatedAt": 1787821243000,
        "generationStatus": "completed",
        "relatedEntryId": None,
        "type": "message",
        "role": role,
        "content": [{"type": "text", "text": text}],
        "source": None,
        "userDisplayContent": None,
    }


def _notice(**fields: Any) -> dict[str, Any]:
    return goal_notice(session_id=_ID, **fields)


_continued = _notice(event="continued", message="Working on the goal", continuation=1)
_continued["turnId"] = _TURN

_history = [
    _notice(
        event="set",
        message=f"Goal set: {_OBJECTIVE}",
        objective=_OBJECTIVE,
        tokens_used=0,
    ),
    _message("saved-a1", "assistant", "Ran the suite; two tests fail.", _TURN),
    _continued,
    _message("saved-a2", "assistant", "Both fixed; the suite passes.", _TURN),
    _notice(
        event="completed",
        message=f"Goal complete: {_OBJECTIVE}",
        objective=_OBJECTIVE,
        status="complete",
        tokens_used=40_000,
        time_used_seconds=4_320,
    ),
]

client_args = ("--resume", _ID)
handshake = {
    "session/resume": {
        "state": {
            "history": _history,
            "session": {"goal": goal("complete", tokens_used=40_000)},
        }
    }
}

timeline: Timeline = [take_snapshot()]

screen_contains = (
    f"◆ Goal set · {_OBJECTIVE}",
    "◆ Working on the goal · continuation 1 · 12.5K / 50K tokens",
    f"✓ Goal complete · 40K tokens · 1h12m · {_OBJECTIVE}",
)
