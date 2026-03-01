"""Game session models — players, state, turns."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Player(BaseModel):
    id: str
    name: str
    color: str = "RED"
    score: int = 0
    is_current_turn: bool = False


class SessionCreate(BaseModel):
    """Request body to create a new game session."""

    game_id: int
    players: list[Player]


class SessionState(BaseModel):
    """Full game session state."""

    id: int | None = None
    game_id: int
    game_name: str = ""
    players: list[Player] = Field(default_factory=list)
    turn_count: int = 0
    status: str = "setup"  # setup | playing | finished
    house_rules: list[str] = Field(default_factory=list)
    history: list[str] = Field(default_factory=list)


class TurnAdvanceRequest(BaseModel):
    session_id: int


class ScoreUpdateRequest(BaseModel):
    session_id: int
    player_id: str
    delta: int


class SessionSummary(BaseModel):
    """Post-game summary stats."""

    game_name: str
    duration: str = ""
    winner_name: str | None = None
    winner_color: str | None = None
    total_turns: int = 0
    disputes_resolved: int = 0
    house_rules_active: int = 0
    rules_explained: int = 0
