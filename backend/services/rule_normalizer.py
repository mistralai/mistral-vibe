"""Rule normaliser — structure raw text into the canonical GameSchema.

Wraps the Mistral Large + Structured Outputs call described in PRD §5.1.
"""

from __future__ import annotations

import json
import logging

from backend.config import settings
from backend.models.game import GameSchema
from backend.services.mistral_client import chat_completion

logger = logging.getLogger(__name__)

_SYSTEM = """\
You are a precise board-game rule structuring engine. Given raw game rule text,
extract and return structured JSON with these fields:
{
  "game_name": "...",
  "player_count": "...",
  "components": [{"name": "...", "description": "...", "quantity": null}],
  "setup": "...",
  "turn_structure": [{"name": "...", "description": "...", "actions": ["..."]}],
  "victory_conditions": ["..."],
  "edge_cases": ["..."],
  "scoring": "... or null"
}
Be exhaustive. Return ONLY valid JSON.
"""


async def normalise_rules(raw_text: str, game_name: str = "") -> GameSchema:
    """Normalise freeform rule text into a ``GameSchema``."""
    prompt = f"Game: {game_name}\n\nRaw rules:\n{raw_text[:12000]}" if game_name else raw_text[:12000]

    raw = await chat_completion(
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        model=settings.mistral_large_model,
        temperature=0.1,
        max_tokens=8192,
    )

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "mock" in data:
            return GameSchema(game_name=game_name or "Unknown", raw_source=raw_text[:500])
        return GameSchema.model_validate(data)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("Rule normalisation parse failed")
        return GameSchema(game_name=game_name or "Unknown", raw_source=raw_text[:500])
