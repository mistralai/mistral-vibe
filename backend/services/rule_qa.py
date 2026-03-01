"""Interactive rule Q&A engine.

Implements PRD §5.2 *Rule Explanation Engine* — answers user questions
with citations and detects house-rule conflicts.
"""

from __future__ import annotations

import json
import logging

from backend.config import settings
from backend.models.moderation import RuleAnswer, RuleQuestion
from backend.services.mistral_client import chat_completion

logger = logging.getLogger(__name__)

_SYSTEM = """\
You are an expert board game rule assistant. Answer rule questions accurately
and concisely. Always cite the relevant official rule section when possible.

If the user has active house rules, check whether the answer conflicts with
any of them and flag the conflict.

Return JSON:
{
  "answer": "Your clear, concise answer",
  "citation": "Rule section reference or null",
  "has_house_rule_conflict": false,
  "official_rule": "The relevant official rule text",
  "house_rule_override": "The conflicting house rule text or empty string"
}
Return ONLY valid JSON.
"""


async def answer_rule_question(question: RuleQuestion) -> RuleAnswer:
    """Answer a game-rule question with optional citation."""
    house_rules_ctx = ""
    if question.house_rules:
        house_rules_ctx = "\n\nActive house rules:\n" + "\n".join(
            f"- {r}" for r in question.house_rules
        )

    user_msg = (
        f"Game: {question.game_name}\n"
        f"Question: {question.question}\n"
        f"Game context: {question.game_context}"
        f"{house_rules_ctx}"
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
            return _mock_answer(question)
        return RuleAnswer.model_validate(data)
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("Rule Q&A parse failed — returning raw text")
        return RuleAnswer(answer=raw[:1000])


def _mock_answer(q: RuleQuestion) -> RuleAnswer:
    return RuleAnswer(
        answer=f"Regarding '{q.question}' in {q.game_name}: Please consult the official rulebook for an authoritative answer. (Mock mode — no API key configured.)",
        citation="Rulebook §General",
        has_house_rule_conflict=False,
    )
