from __future__ import annotations

from vibe.core.watchdog.checkpoint_policy import (
    CheckpointRestorePolicy,
    RestoreLocation,
    RestoreVerdict,
)
from vibe.core.watchdog.handoff import RecoveryHandoff
from vibe.core.watchdog.models import RecoveryStrategy, RunPhase
from vibe.core.watchdog.verification import (
    VerificationInput,
    VerificationStatus,
    VerifierRegistry,
)


def handoff() -> RecoveryHandoff:
    return RecoveryHandoff(
        objective="Fix the parser without losing user changes",
        incident_id="incident-1",
        epoch=1,
        detector="repeated_call",
        evidence_fingerprint="evidence-1",
        phase=RunPhase.RECOVERY,
        repository_fingerprint="repo-1",
        changed_paths=tuple(f"src/file-{index}.py" for index in range(100)),
        prohibited_action="repeat identical test command",
        required_next_step="inspect a different failing layer",
        remaining_attempts=2,
    )


def test_handoff_cap_preserves_structured_facts() -> None:
    rendered = handoff().render(max_chars=500)

    assert len(rendered) <= 500
    assert "objective=Fix the parser" in rendered
    assert "incident=incident-1" in rendered
    assert "required=inspect a different failing layer" in rendered


def test_main_checkout_restore_is_always_denied() -> None:
    verdict = CheckpointRestorePolicy().decide(
        location=RestoreLocation.MAIN_CHECKOUT,
        ownership_proven=True,
        manual_edit_after_checkpoint=False,
        current_epoch=1,
        checkpoint_epoch=1,
    )

    assert verdict == RestoreVerdict.DENY


def test_manual_edit_requires_approval_in_watchdog_worktree() -> None:
    verdict = CheckpointRestorePolicy().decide(
        location=RestoreLocation.WATCHDOG_WORKTREE,
        ownership_proven=True,
        manual_edit_after_checkpoint=True,
        current_epoch=1,
        checkpoint_epoch=1,
    )

    assert verdict == RestoreVerdict.ASK


def test_context_verification_requires_different_next_action() -> None:
    registry = VerifierRegistry()
    failed = registry.verify(
        RecoveryStrategy.INJECT_CONTEXT,
        VerificationInput(
            previous_action_fingerprint="same", next_action_fingerprint="same"
        ),
    )
    passed = registry.verify(
        RecoveryStrategy.INJECT_CONTEXT,
        VerificationInput(
            previous_action_fingerprint="before", next_action_fingerprint="after"
        ),
    )

    assert failed.status == VerificationStatus.FAILED
    assert passed.status == VerificationStatus.PASSED
    assert not passed.completes_goal


def test_cancel_ack_without_idle_fails_verification() -> None:
    result = VerifierRegistry().verify(
        RecoveryStrategy.CANCEL_AND_CONTINUE,
        VerificationInput(target_terminal=True, agent_idle=False),
    )

    assert result.status == VerificationStatus.FAILED
