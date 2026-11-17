"""The transcript notices that show where a goal changed or worked.

Pure: given a change, return the notice to record, if any. The notice text is
for clients without a dedicated rendering; ``GoalNoticeDetail`` carries the
structured fields.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from vibe.app_server.models import (
    GoalNoticeDetail,
    GoalNoticeEvent,
    GoalStatus,
    GoalStatusReason,
    PublicGoal,
)

__all__ = ["GoalNotice", "change_notice", "continuation_notice", "with_turn_failure"]

# The turn error quoted in a notice: enough to name the cause, a row or two.
MAX_NOTICE_ERROR_CHARS = 300


@dataclass(frozen=True, slots=True)
class GoalNotice:
    message: str
    detail: GoalNoticeDetail
    level: str = "info"


_USER_EVENTS: dict[str, GoalNoticeEvent] = {
    "set": "set",
    "edit": "edited",
    "pause": "paused",
    "resume": "resumed",
    "clear": "cleared",
}

# Stops the transcript shows whoever caused them: the user sees why work ended.
_STOP_EVENTS: dict[GoalStatus, GoalNoticeEvent] = {
    GoalStatus.COMPLETE: "completed",
    GoalStatus.BLOCKED: "blocked",
    GoalStatus.BUDGET_LIMITED: "budget_limited",
    GoalStatus.USAGE_LIMITED: "usage_limited",
}

# Pauses the transcript shows when they are not the user's own action.
_PAUSE_EVENTS: dict[GoalStatusReason | None, GoalNoticeEvent] = {
    GoalStatusReason.INTERRUPTED: "interrupted",
    GoalStatusReason.MODEL: "paused",
}

_MESSAGES: dict[GoalNoticeEvent, str] = {
    "set": "Goal set",
    "edited": "Goal edited",
    "paused": "Goal paused",
    "resumed": "Goal resumed",
    "cleared": "Goal cleared",
    "continued": "Working on the goal",
    "completed": "Goal complete",
    "blocked": "Goal blocked",
    "budget_limited": "Goal budget reached",
    "usage_limited": "Goal stopped: no credit left",
    "interrupted": "Goal paused: turn interrupted",
}


def change_notice(
    action: str | None, previous: PublicGoal | None, goal: PublicGoal | None
) -> GoalNotice | None:
    """The notice for one committed change.

    ``action`` names a user action (``set``, ``edit``, ``pause``, ``resume``,
    ``clear``); ``None`` is any other change, which shows only when it stops
    the goal.
    """
    if action is not None:
        event = _USER_EVENTS.get(action)
        subject = goal if goal is not None else previous
        if event is None or subject is None:
            return None
        objective_changed = goal is not None and (
            event == "set"
            or (previous is not None and previous.objective != goal.objective)
        )
        return _notice(event, subject, objective=objective_changed)
    if goal is None or previous is None or goal.goal_id != previous.goal_id:
        return None
    if goal.status is previous.status:
        return None
    if goal.status is GoalStatus.PAUSED:
        # Paused by an interrupted turn, or by the model as the user asked in
        # the conversation; the user's own pause is an action above.
        event = _PAUSE_EVENTS.get(goal.status_reason)
        return None if event is None else _notice(event, goal)
    event = _STOP_EVENTS.get(goal.status)
    # A complete goal leaves the session, so its notice is what recalls it.
    return (
        None if event is None else _notice(event, goal, objective=event == "completed")
    )


def with_turn_failure(
    notice: GoalNotice, error: str | None, *, retryable: bool
) -> GoalNotice:
    """``notice`` saying which turn error stopped the goal, and what to do next.

    An error the same request meets again (an oversized image in the history,
    a context too long) fails a resumed goal straight away, so the notice
    points away from resuming.
    """
    reason = " ".join((error or "").split())
    if len(reason) > MAX_NOTICE_ERROR_CHARS:
        reason = f"{reason[: MAX_NOTICE_ERROR_CHARS - 1]}…"
    advice = (
        "Run /goal resume to try again."
        if retryable
        else "Resuming would fail the same way: /rewind to before the turn "
        "that caused it, or start a new session."
    )
    cause = f": the turn failed: {reason}" if reason else ": the turn failed"
    return replace(
        notice,
        message=f"{notice.message}{cause}. {advice}",
        detail=notice.detail.model_copy(update={"error": reason or None}),
    )


def continuation_notice(goal: PublicGoal, number: int) -> GoalNotice:
    return _notice("continued", goal, continuation=number)


def _notice(
    event: GoalNoticeEvent,
    goal: PublicGoal,
    *,
    objective: bool = False,
    continuation: int | None = None,
) -> GoalNotice:
    message = _MESSAGES[event]
    if objective:
        message = f"{message}: {goal.objective}"
    level = "warning" if event in {"blocked", "usage_limited"} else "info"
    return GoalNotice(
        message=message,
        level=level,
        detail=GoalNoticeDetail(
            event=event,
            goal_id=goal.goal_id,
            objective=goal.objective if objective else None,
            status=goal.status,
            status_reason=goal.status_reason,
            continuation=continuation,
            tokens_used=goal.tokens_used,
            token_budget=goal.token_budget,
            time_used_seconds=goal.time_used_seconds,
        ),
    )
