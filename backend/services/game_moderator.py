"""Game moderation engine — turn management, rule enforcement, state tracking.

Implements PRD §5.4 *Game Moderation Engine*.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.config import settings
from backend.models.moderation import MoveValidationRequest, MoveValidationResponse
from backend.services.mistral_client import chat_completion

logger = logging.getLogger(__name__)

_MODERATION_SYSTEM = """\
You are a neutral board game moderator managing a live game session.
Your responsibilities:
1. Track turns and remind players of phase order.
2. Validate declared moves against the rules.
3. Detect potential illegal moves and explain why.
4. Keep the game flowing smoothly.

When asked to validate a move, return JSON:
{
  "is_valid": true,
  "explanation": "Why the move is valid or invalid",
  "rule_reference": "Rule section reference or null"
}
Return ONLY valid JSON.
"""

_EXPLANATION_SYSTEM = """\
You are a board game instructor. Explain the rules of a game clearly and
concisely. Adjust the depth to the requested mode.

Modes:
- "quickstart": 3-5 minute summary covering goal, turn flow, and win conditions.
- "stepbystep": Detailed walkthrough with setup and component explanation.
- "simulation": Narrate a sample round step by step.

Return your explanation as plain text (not JSON).
"""


async def validate_move(req: MoveValidationRequest) -> MoveValidationResponse:
    """Validate a player's declared move against game rules."""
    user_msg = (
        f"Game: {req.game_name}\n"
        f"Declared move: {req.move_description}\n"
        f"Current state: {json.dumps(req.game_state, default=str)}\n"
    )
    if req.house_rules:
        user_msg += "Active house rules:\n" + "\n".join(f"- {r}" for r in req.house_rules)

    raw = await chat_completion(
        messages=[
            {"role": "system", "content": _MODERATION_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        model=settings.mistral_large_model,
        temperature=0.1,
    )

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "mock" in data:
            return MoveValidationResponse(is_valid=True, explanation="Move accepted (mock mode).")
        return MoveValidationResponse.model_validate(data)
    except (json.JSONDecodeError, TypeError, ValueError):
        return MoveValidationResponse(is_valid=True, explanation=raw[:500])


async def explain_game(game_name: str, mode: str = "quickstart") -> str:
    """Generate a rule explanation in the requested mode."""
    raw = await chat_completion(
        messages=[
            {"role": "system", "content": _EXPLANATION_SYSTEM},
            {"role": "user", "content": f"Game: {game_name}\nMode: {mode}"},
        ],
        model=settings.magistral_medium_model,
        temperature=0.3,
        max_tokens=4096,
    )

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "mock" in data:
            return _mock_explanation(game_name, mode)
    except json.JSONDecodeError:
        pass

    return raw if raw else _mock_explanation(game_name, mode)


async def generate_simulation(game_name: str) -> str:
    """Simulate a sample round of the game."""
    return await explain_game(game_name, mode="simulation")


def _mock_explanation(game_name: str, mode: str) -> str:
    if mode == "quickstart":
        return (
            f"# {game_name} — Quick Start\n\n"
            f"**Goal**: Be the first to achieve the victory condition.\n\n"
            f"**Turn Flow**: On your turn, take one action, then pass to the next player.\n\n"
            f"**Winning**: Reach the target score or objective first.\n\n"
            f"*(Mock mode — connect a Mistral API key for detailed explanations.)*"
        )
    if mode == "stepbystep":
        return (
            f"# {game_name} — Step-by-Step Guide\n\n"
            f"## Setup\nArrange the board and distribute components.\n\n"
            f"## Turn Structure\n1. Draw phase\n2. Action phase\n3. End phase\n\n"
            f"## Victory\nAchieve the stated objective.\n\n"
            f"*(Mock mode)*"
        )
    return (
        f"# {game_name} — Simulated Round\n\n"
        f"**Round 1**: Player 1 takes their turn...\n"
        f"**Round 1**: Player 2 responds...\n\n"
        f"*(Mock mode — connect a Mistral API key for a full simulation.)*"
    )
