"""Context-breakdown handshake builders for scenarios."""

from __future__ import annotations

from typing import Any

from e2e.app_server.events import SESSION_ID

BREAKDOWN: dict[str, Any] = {
    "contextWindow": 200_000,
    "compactionThreshold": 150_000,
    "usedTokens": 42_300,
    "measured": True,
    "totalCharacters": 169_200,
    "categories": [
        {"id": "system", "characters": 20_000, "estimatedTokens": 5_000},
        {"id": "tools", "characters": 48_000, "estimatedTokens": 12_000},
        {"id": "skills", "characters": 10_000, "estimatedTokens": 2_500},
        {"id": "conversation", "characters": 91_200, "estimatedTokens": 22_800},
    ],
}


def handshake(**overrides: Any) -> dict[str, Any]:
    breakdown = {**BREAKDOWN, **overrides}
    return {"session/context/read": {"breakdown": breakdown}}


def stats_updated(
    context_tokens: int = 42_300, context_window: int = 200_000
) -> dict[str, Any]:
    return {
        "method": "session/statsUpdated",
        "params": {
            "sessionId": SESSION_ID,
            "emittedAt": 1_787_593_260_000,
            "stats": {"contextTokens": context_tokens},
            "contextWindow": context_window,
        },
    }
