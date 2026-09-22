"""Durable parent notification construction and queue transitions."""

from typing import Literal

from mistralai_vibe_local_harness.protocol import (
    RustContentBlock,
    RustHarnessNotification,
    RustSubagentNotificationSource,
    RustTextContentBlock,
)
from mistralai_vibe_local_harness.vibe._subagents._models import (
    ChildSessionRecord,
    ChildTurnOutcome,
    CompletedChildTurnOutcome,
    FailedChildTurnOutcome,
    PendingChildNotification,
    SubagentRuntimeState,
)


def pending_notification(
    state: SubagentRuntimeState,
    child: ChildSessionRecord,
    outcome: ChildTurnOutcome,
) -> PendingChildNotification:
    if isinstance(outcome, CompletedChildTurnOutcome):
        status: Literal["completed", "failed", "interrupted"] = "completed"
        level: Literal["info", "warning", "error"] = "info"
        message = f"Subagent {child.agent_name} completed turn {outcome.generation}."
        content: list[RustContentBlock] = [RustTextContentBlock(text=outcome.final_answer)]
    elif isinstance(outcome, FailedChildTurnOutcome):
        status = "failed"
        level = "error"
        message = outcome.failure.message
        content = []
    else:
        status = "interrupted"
        level = "warning"
        message = outcome.reason
        content = []
    return PendingChildNotification(
        sequence=state.notifications.next_sequence,
        agent_name=child.agent_name,
        generation=outcome.generation,
        notification=RustHarnessNotification(
            id=notification_id(child, outcome),
            source=RustSubagentNotificationSource(
                agent_name=child.agent_name,
                status=status,
            ),
            level=level,
            message=message,
            content=content,
        ),
    )


def notification_id(child: ChildSessionRecord, outcome: ChildTurnOutcome) -> str:
    return (
        f"subagent:{child.child_session_id}:{child.agent_name}:{outcome.generation}:{outcome.type}"
    )


__all__ = ["notification_id", "pending_notification"]
