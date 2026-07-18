from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from vibe.core.watchdog.models import RunPhase

MIN_TILT_SCORE = 0
MAX_TILT_SCORE = 100
DEFAULT_TILT_RECOVERY_THRESHOLD = 80


class TiltEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    detector: str
    phase: RunPhase
    evidence_count: int = Field(ge=1)
    exact_repeat_count: int = Field(ge=0)


class TiltEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    score: int = Field(ge=MIN_TILT_SCORE, le=MAX_TILT_SCORE)


class TiltEvaluator(Protocol):
    async def evaluate(self, request: TiltEvaluationRequest) -> TiltEvaluation: ...


class TiltScorePolicy:
    def __init__(
        self, *, recovery_threshold: int = DEFAULT_TILT_RECOVERY_THRESHOLD
    ) -> None:
        if not MIN_TILT_SCORE <= recovery_threshold <= MAX_TILT_SCORE:
            raise ValueError("TILT recovery threshold must be between 0 and 100")
        self.recovery_threshold = recovery_threshold

    def authorizes_context_injection(self, evaluation: TiltEvaluation) -> bool:
        return evaluation.score >= self.recovery_threshold


def parse_tilt_evaluation(content: str) -> TiltEvaluation:
    normalized = content.strip()
    if normalized.startswith("```") and normalized.endswith("```"):
        normalized = normalized[3:-3].strip()
        if normalized.startswith("json"):
            normalized = normalized[4:].strip()
    return TiltEvaluation.model_validate_json(normalized)
