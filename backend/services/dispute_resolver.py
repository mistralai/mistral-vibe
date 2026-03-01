"""Dispute resolution service.

Implements PRD §5.4‑C — provides verifiable rule citations with an
interpretation hierarchy: official → FAQ → community → house rule.
"""

from __future__ import annotations

import json
import logging

from backend.config import settings
from backend.models.moderation import DisputeRequest, DisputeRuling
from backend.services.mistral_client import chat_completion

logger = logging.getLogger(__name__)

_SYSTEM = """\
You are a neutral board game dispute arbiter. Given a dispute description,
provide a fair ruling with citations.

Use this interpretation hierarchy (highest priority first):
1. Official Rule — from the published rulebook
2. FAQ / Errata — from publisher clarifications
3. Community Consensus — widely accepted interpretations
4. House Rules — the group's custom rules

Return JSON:
{
  "title": "Brief ruling title",
  "body": "Detailed explanation of the ruling",
  "source": "official | faq | community | house",
  "citation": "Specific rule reference or null",
  "confidence": 0.9
}
Return ONLY valid JSON.
"""


async def resolve_dispute(req: DisputeRequest) -> DisputeRuling:
    """Resolve a gameplay dispute with cited reasoning."""
    players_str = ", ".join(req.players_involved) if req.players_involved else "unspecified"

    user_msg = (
        f"Game: {req.game_name}\n"
        f"Players involved: {players_str}\n"
        f"Dispute: {req.description}"
    )

    raw = await chat_completion(
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        model=settings.mistral_large_model,
        temperature=0.1,
        max_tokens=2048,
    )

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "mock" in data:
            return _mock_ruling(req)
        return DisputeRuling.model_validate(data)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("Dispute resolution parse failed")
        return _mock_ruling(req)


def _mock_ruling(req: DisputeRequest) -> DisputeRuling:
    return DisputeRuling(
        title="Ruling Pending",
        body=f"Regarding the dispute in {req.game_name}: '{req.description[:200]}'. "
        "Please consult the official rulebook. (Mock mode — no API key configured.)",
        source="official",
        citation="Rulebook §General",
        confidence=0.5,
    )
