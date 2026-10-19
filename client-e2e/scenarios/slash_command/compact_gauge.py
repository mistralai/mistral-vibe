"""A manual `/compact` drops the context gauge at once instead of waiting for fresh stats."""

from __future__ import annotations

from e2e.app_server.events import (
    SESSION_ID,
    assistant_msg,
    cleared_state,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_STATS = {
    "method": "session/statsUpdated",
    "params": {
        "sessionId": SESSION_ID,
        "emittedAt": 1_787_593_260_000,
        "stats": {"contextTokens": 12_500},
        "contextWindow": 200_000,
    },
}

# The same-session compaction answer: the live id stays, so only the gauge moves.
_state = cleared_state()
_state["session"]["id"] = SESSION_ID
handshake = {
    "session/compact": {
        "summary": "Compacted summary.",
        "state": _state,
        "sessionLog": {"enabled": False},
    }
}

timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Hi there."),
    _STATS,
    turn_completed(),
    "/compact\r",
]

capture_startup = False
screen_contains = ("0/200k tokens (0%)",)
screen_excludes = ("12.5k/200k tokens",)
