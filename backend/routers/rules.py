"""Rule explanation, Q&A, house rules, and dispute resolution endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from backend.models.house_rules import HouseRuleValidationRequest, HouseRuleValidationResponse
from backend.models.moderation import (
    DisputeRequest,
    DisputeRuling,
    RuleAnswer,
    RuleQuestion,
)
from backend.services.dispute_resolver import resolve_dispute
from backend.services.house_rules import validate_house_rule
from backend.services.rule_qa import answer_rule_question

router = APIRouter(prefix="/api/rules", tags=["rules"])


@router.post("/qa", response_model=RuleAnswer)
async def ask_rule(body: RuleQuestion):
    """Ask a question about a game's rules (with citation)."""
    return await answer_rule_question(body)


@router.post("/house-rules/validate", response_model=HouseRuleValidationResponse)
async def validate_house(body: HouseRuleValidationRequest):
    """Validate a proposed house rule against the base game rules."""
    return await validate_house_rule(body)


@router.post("/dispute", response_model=DisputeRuling)
async def dispute(body: DisputeRequest):
    """Resolve a gameplay dispute with cited reasoning."""
    return await resolve_dispute(body)
