"""House rule models — creation, validation, contradiction checking."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HouseRule(BaseModel):
    id: str = ""
    text: str
    is_active: bool = True


class HouseRuleValidationRequest(BaseModel):
    """Request to validate a proposed house rule against base rules."""

    game_name: str
    proposed_rule: str
    existing_house_rules: list[str] = Field(default_factory=list)


class ContradictionDetail(BaseModel):
    """Details of a detected contradiction."""

    conflicting_rule: str
    reason: str
    severity: str = "warning"  # info | warning | error


class HouseRuleValidationResponse(BaseModel):
    """Result of house rule contradiction analysis."""

    is_valid: bool
    contradictions: list[ContradictionDetail] = Field(default_factory=list)
    impact_summary: str = ""
    balance_impact: str = "neutral"  # positive | negative | neutral
    length_impact: str = "neutral"
    complexity_impact: str = "neutral"
