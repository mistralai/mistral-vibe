"""Live game moderation and move validation endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from backend.models.moderation import (
    ContentSafetyResult,
    MoveValidationRequest,
    MoveValidationResponse,
)
from backend.services.content_safety import screen_content
from backend.services.game_moderator import explain_game, generate_simulation, validate_move

router = APIRouter(prefix="/api/moderation", tags=["moderation"])


@router.post("/validate-move", response_model=MoveValidationResponse)
async def validate(body: MoveValidationRequest):
    """Validate a declared player move against game rules."""
    return await validate_move(body)


@router.get("/explain/{game_name}")
async def explain(game_name: str, mode: str = "quickstart"):
    """Get a rule explanation for a game.

    Modes: ``quickstart``, ``stepbystep``, ``simulation``.
    """
    text = await explain_game(game_name, mode=mode)
    return {"game_name": game_name, "mode": mode, "explanation": text}


@router.get("/simulate/{game_name}")
async def simulate(game_name: str):
    """Simulate a sample round of the game."""
    text = await generate_simulation(game_name)
    return {"game_name": game_name, "simulation": text}


@router.post("/screen-content", response_model=ContentSafetyResult)
async def screen(text: str):
    """Screen user text for harmful content before processing."""
    return await screen_content(text)
