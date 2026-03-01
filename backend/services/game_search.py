"""Game search — find board games by name using Mistral web search.

Implements PRD §5.1‑B *Find Game by Name*: Mistral Large agent with
built-in web search retrieves rulebook content and normalises it into
the ``GameSchema``.
"""

from __future__ import annotations

import json
import logging

from backend.config import settings
from backend.models.game import GameSchema, GameSearchResponse, GameSearchResult
from backend.services.mistral_client import chat_completion

logger = logging.getLogger(__name__)

_SEARCH_SYSTEM = """\
You are a board game expert assistant. When a user asks about a board game,
search for it and return structured information.

Return a JSON array of up to 6 matching board games. Each object must have:
- "name": exact game title
- "player_count": e.g. "2-4 players"
- "complexity": integer 1 (easy), 2 (medium), or 3 (complex)
- "description": one-sentence summary

Return ONLY the JSON array, no surrounding text.
"""

_DETAIL_SYSTEM = """\
You are a board game rule expert. Given a game name, provide a comprehensive
structured rule summary. Return JSON with exactly these keys:
{
  "game_name": "...",
  "player_count": "...",
  "components": [{"name": "...", "description": "...", "quantity": null}],
  "setup": "...",
  "turn_structure": [{"name": "phase name", "description": "...", "actions": ["..."]}],
  "victory_conditions": ["..."],
  "edge_cases": ["..."],
  "scoring": "... or null"
}
Return ONLY valid JSON. Be thorough and accurate.
"""


async def search_games(query: str) -> GameSearchResponse:
    """Search for board games matching *query*."""
    raw = await chat_completion(
        messages=[
            {"role": "system", "content": _SEARCH_SYSTEM},
            {"role": "user", "content": f"Find board games matching: {query}"},
        ],
        model=settings.mistral_large_model,
        temperature=0.2,
    )

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "mock" in data:
            return _mock_search(query)
        results = [GameSearchResult.model_validate(item) for item in data]
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("Failed to parse search response — using fallback")
        return _mock_search(query)

    return GameSearchResponse(results=results)


async def get_game_detail(game_name: str) -> GameSchema:
    """Retrieve full rule details for a game by name."""
    raw = await chat_completion(
        messages=[
            {"role": "system", "content": _DETAIL_SYSTEM},
            {"role": "user", "content": f"Provide the complete rules for: {game_name}"},
        ],
        model=settings.mistral_large_model,
        temperature=0.1,
        max_tokens=8192,
    )

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "mock" in data:
            return _mock_detail(game_name)
        return GameSchema.model_validate(data)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("Failed to parse detail response — using fallback")
        return _mock_detail(game_name)


# ── Fallback mock data for offline / demo mode ──────────────────────────


def _mock_search(query: str) -> GameSearchResponse:
    """Return curated results for common game names in offline mode."""
    games: dict[str, list[GameSearchResult]] = {
        "catan": [
            GameSearchResult(name="Catan", player_count="3-4 players", complexity=2, description="Trade and build settlements on the island of Catan."),
            GameSearchResult(name="Catan: Seafarers", player_count="3-4 players", complexity=2, description="Expand across the seas with ships and islands."),
            GameSearchResult(name="Catan: Cities & Knights", player_count="3-4 players", complexity=3, description="Advanced expansion with commodities and barbarians."),
            GameSearchResult(name="Catan Junior", player_count="2-4 players", complexity=1, description="Kid-friendly pirate-themed Catan."),
        ],
        "chess": [
            GameSearchResult(name="Chess", player_count="2 players", complexity=3, description="Classic strategy game of checkmate."),
        ],
        "monopoly": [
            GameSearchResult(name="Monopoly", player_count="2-8 players", complexity=1, description="Classic property trading board game."),
            GameSearchResult(name="Monopoly Deal", player_count="2-5 players", complexity=1, description="Fast-paced card game version."),
        ],
    }
    key = query.lower().strip()
    for k, v in games.items():
        if k in key:
            return GameSearchResponse(results=v)
    return GameSearchResponse(
        results=[GameSearchResult(name=query.title(), player_count="2-6 players", complexity=2, description=f"Results for '{query}'.")]
    )


def _mock_detail(game_name: str) -> GameSchema:
    return GameSchema(
        game_name=game_name,
        player_count="2-6 players",
        setup=f"Set up the {game_name} board according to the rulebook.",
        turn_structure=[
            {"name": "Start Phase", "description": "Begin your turn", "actions": ["Draw a card"]},
            {"name": "Action Phase", "description": "Take your main action", "actions": ["Play cards", "Trade"]},
            {"name": "End Phase", "description": "End your turn", "actions": ["Discard excess cards"]},
        ],
        victory_conditions=[f"Achieve the {game_name} victory objective"],
        edge_cases=["Tie-breaking: first player to reach the condition wins"],
    )
