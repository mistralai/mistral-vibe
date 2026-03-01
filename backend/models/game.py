"""Game schema models — the normalised representation of a board game.

Mirrors the PRD §5.1 *Game Acquisition & Learning* output format.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GameComponent(BaseModel):
    """A named game component (piece, card, etc.)."""

    name: str
    description: str = ""
    quantity: int | None = None


class TurnPhase(BaseModel):
    """One phase within a turn cycle."""

    name: str
    description: str = ""
    actions: list[str] = Field(default_factory=list)


class GameSchema(BaseModel):
    """Fully normalised game rule schema (PRD §5.1 output)."""

    game_name: str
    player_count: str = ""  # e.g. "2-4"
    components: list[GameComponent] = Field(default_factory=list)
    setup: str = ""
    turn_structure: list[TurnPhase] = Field(default_factory=list)
    victory_conditions: list[str] = Field(default_factory=list)
    edge_cases: list[str] = Field(default_factory=list)
    scoring: str | None = None
    raw_source: str = ""  # original text before normalisation


class GameSearchResult(BaseModel):
    """Lightweight search hit returned to the frontend."""

    name: str
    player_count: str = ""
    complexity: int = Field(default=1, ge=1, le=3)
    description: str = ""


class GameSearchResponse(BaseModel):
    results: list[GameSearchResult]


class GameDetailResponse(BaseModel):
    """Full game detail including normalised schema."""

    id: int | None = None
    schema_: GameSchema = Field(alias="schema")

    model_config = {"populate_by_name": True}


class OCRUploadResponse(BaseModel):
    """Result of rulebook image OCR processing."""

    extracted_text: str
    game_schema: GameSchema
