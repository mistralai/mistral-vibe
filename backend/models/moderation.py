"""Moderation and dispute resolution models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RuleQuestion(BaseModel):
    """A user's rule question during gameplay."""

    game_name: str
    question: str
    game_context: str = ""  # current game state context
    house_rules: list[str] = Field(default_factory=list)


class RuleAnswer(BaseModel):
    """AI-generated answer with citation."""

    answer: str
    citation: str | None = None
    has_house_rule_conflict: bool = False
    official_rule: str = ""
    house_rule_override: str = ""


class DisputeRequest(BaseModel):
    """A dispute raised during a game session."""

    session_id: int | None = None
    game_name: str
    description: str
    players_involved: list[str] = Field(default_factory=list)


class DisputeRuling(BaseModel):
    """AI-generated dispute resolution with citation hierarchy."""

    title: str
    body: str
    source: str = "official"  # official | faq | community | house
    citation: str | None = None
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)


class MoveValidationRequest(BaseModel):
    """Request to validate a declared player move."""

    game_name: str
    move_description: str
    game_state: dict[str, object] = Field(default_factory=dict)
    house_rules: list[str] = Field(default_factory=list)


class MoveValidationResponse(BaseModel):
    """Result of move validation."""

    is_valid: bool
    explanation: str = ""
    rule_reference: str | None = None


class ContentSafetyResult(BaseModel):
    """Content moderation screening result."""

    is_safe: bool
    categories: list[str] = Field(default_factory=list)
    explanation: str = ""
