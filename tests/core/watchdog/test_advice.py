from __future__ import annotations

import pytest

from vibe.core.watchdog import (
    RecoveryAdvice,
    RecoveryAdviceRequest,
    RecoveryStrategy,
    validate_recovery_advice,
)


def request(strategy: RecoveryStrategy) -> RecoveryAdviceRequest:
    return RecoveryAdviceRequest(
        strategy=strategy,
        objective="Fix parser tests",
        detector="repeated_call",
        evidence=("evidence",),
        attempted_strategies=(RecoveryStrategy.INJECT_CONTEXT,),
        available_tools=("bash", "edit"),
        failed_tool="bash",
    )


def test_recovery_advice_accepts_bounded_alternate_tool() -> None:
    advice = RecoveryAdvice(
        strategy=RecoveryStrategy.ALTERNATE_TOOL,
        reason="edit the failing guard",
        tool="edit",
    )

    validate_recovery_advice(advice, request(RecoveryStrategy.ALTERNATE_TOOL))


def test_recovery_advice_rejects_same_tool_and_destructive_command() -> None:
    with pytest.raises(ValueError, match="must differ"):
        validate_recovery_advice(
            RecoveryAdvice(
                strategy=RecoveryStrategy.ALTERNATE_TOOL,
                reason="repeat bash",
                tool="bash",
            ),
            request(RecoveryStrategy.ALTERNATE_TOOL),
        )
    with pytest.raises(ValueError, match="destructive"):
        validate_recovery_advice(
            RecoveryAdvice(
                strategy=RecoveryStrategy.REWRITE_COMMAND,
                reason="unsafe cleanup",
                command="rm -rf build",
            ),
            request(RecoveryStrategy.REWRITE_COMMAND),
        )
