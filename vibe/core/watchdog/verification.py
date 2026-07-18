from __future__ import annotations

from enum import StrEnum, auto
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from vibe.core.watchdog.models import RecoveryStrategy


class VerificationStatus(StrEnum):
    PASSED = auto()
    FAILED = auto()
    NEEDS_USER = auto()


class VerificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    previous_action_fingerprint: str | None = None
    next_action_fingerprint: str | None = None
    target_terminal: bool = False
    agent_idle: bool = False
    previous_failure_count: int | None = None
    current_failure_count: int | None = None


class VerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: VerificationStatus
    reason: str
    completes_goal: bool = False


class StrategyVerifier(Protocol):
    def verify(self, evidence: VerificationInput) -> VerificationResult: ...


class ContextInjectionVerifier:
    def verify(self, evidence: VerificationInput) -> VerificationResult:
        passed = (
            evidence.previous_action_fingerprint is not None
            and evidence.next_action_fingerprint is not None
            and evidence.previous_action_fingerprint != evidence.next_action_fingerprint
        )
        return VerificationResult(
            status=VerificationStatus.PASSED if passed else VerificationStatus.FAILED,
            reason="next_action_changed" if passed else "same_or_missing_next_action",
        )


class CancellationVerifier:
    def verify(self, evidence: VerificationInput) -> VerificationResult:
        passed = evidence.target_terminal and evidence.agent_idle
        return VerificationResult(
            status=VerificationStatus.PASSED if passed else VerificationStatus.FAILED,
            reason="terminal_and_idle" if passed else "target_not_terminal_or_idle",
        )


class TestImprovementVerifier:
    def verify(self, evidence: VerificationInput) -> VerificationResult:
        passed = (
            evidence.previous_failure_count is not None
            and evidence.current_failure_count is not None
            and evidence.current_failure_count < evidence.previous_failure_count
        )
        return VerificationResult(
            status=VerificationStatus.PASSED if passed else VerificationStatus.FAILED,
            reason="failure_set_improved" if passed else "failure_set_not_improved",
        )


class VerifierRegistry:
    def __init__(self) -> None:
        self._verifiers: dict[RecoveryStrategy, StrategyVerifier] = {
            RecoveryStrategy.INJECT_CONTEXT: ContextInjectionVerifier(),
            RecoveryStrategy.CANCEL_AND_CONTINUE: CancellationVerifier(),
        }

    def verify(
        self, strategy: RecoveryStrategy, evidence: VerificationInput
    ) -> VerificationResult:
        verifier = self._verifiers.get(strategy)
        if verifier is None:
            return VerificationResult(
                status=VerificationStatus.NEEDS_USER, reason="no_automatic_verifier"
            )
        return verifier.verify(evidence)
