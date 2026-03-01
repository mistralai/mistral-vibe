"""House rule validation service.

Implements PRD §5.3 *House Rule Adaptation System* — validates
proposed house rules against base rules for contradictions and
simulates downstream impact.
"""

from __future__ import annotations

import json
import logging

from backend.config import settings
from backend.models.house_rules import (
    ContradictionDetail,
    HouseRuleValidationRequest,
    HouseRuleValidationResponse,
)
from backend.services.mistral_client import chat_completion

logger = logging.getLogger(__name__)

_SYSTEM = """\
You are a board game house rule analyst. Given a base game and a proposed house
rule, determine whether the house rule contradicts official rules or existing
house rules. Analyse balance, game length, and complexity impact.

Return JSON:
{
  "is_valid": true,
  "contradictions": [
    {"conflicting_rule": "...", "reason": "...", "severity": "warning"}
  ],
  "impact_summary": "Brief description of overall impact",
  "balance_impact": "positive | negative | neutral",
  "length_impact": "positive | negative | neutral",
  "complexity_impact": "positive | negative | neutral"
}

Severity levels: "info" (no conflict, just a note), "warning" (potential
issue), "error" (direct contradiction that would break the game).
If there are no contradictions, return an empty array.
Return ONLY valid JSON.
"""


async def validate_house_rule(req: HouseRuleValidationRequest) -> HouseRuleValidationResponse:
    """Validate a proposed house rule against the base game and existing house rules."""
    existing_ctx = ""
    if req.existing_house_rules:
        existing_ctx = "\n\nExisting house rules:\n" + "\n".join(
            f"- {r}" for r in req.existing_house_rules
        )

    user_msg = (
        f"Game: {req.game_name}\n"
        f"Proposed house rule: {req.proposed_rule}"
        f"{existing_ctx}"
    )

    raw = await chat_completion(
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        model=settings.magistral_medium_model,
        temperature=0.2,
        max_tokens=2048,
    )

    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "mock" in data:
            return _mock_validation(req)
        return HouseRuleValidationResponse.model_validate(data)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("House rule validation parse failed")
        return _mock_validation(req)


def _mock_validation(req: HouseRuleValidationRequest) -> HouseRuleValidationResponse:
    """Deterministic mock for offline use."""
    has_conflict = any(
        word in req.proposed_rule.lower()
        for word in ["robber", "trade", "steal", "unlimited"]
    )
    contradictions = []
    if has_conflict:
        contradictions.append(
            ContradictionDetail(
                conflicting_rule="Official trading / robber rules",
                reason="This house rule modifies a core mechanic. Verify carefully before adopting.",
                severity="warning",
            )
        )
    return HouseRuleValidationResponse(
        is_valid=not has_conflict,
        contradictions=contradictions,
        impact_summary="Mock analysis — connect a Mistral API key for real validation.",
        balance_impact="neutral",
        length_impact="neutral",
        complexity_impact="neutral",
    )
