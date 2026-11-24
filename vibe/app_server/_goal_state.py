"""The durable goal's state machine, free of I/O.

Every user and model mutation is a pure function from the current goal to the
next one, so the store only persists what these functions return and the rules
can be tested without a session.
"""

from __future__ import annotations

from dataclasses import dataclass
import secrets
from typing import Literal

from vibe.app_server.models import GoalStatus, GoalStatusReason, PublicGoal

__all__ = [
    "KEEP_BUDGET",
    "MAX_OBJECTIVE_CHARS",
    "GoalError",
    "GoalSetRequest",
    "ModelGoalStatus",
    "apply_model_status",
    "apply_user_set",
    "budget_reached",
    "create_goal",
    "is_unfinished",
    "normalize_objective",
    "validate_budget",
]

MAX_OBJECTIVE_CHARS = 4000

type ModelGoalStatus = Literal["complete", "blocked", "paused"]
type ErrorKind = Literal["invalid", "conflict"]


class _KeepBudget:
    """Marks a ``goal/set`` that leaves the token budget as it is."""


KEEP_BUDGET = _KeepBudget()


class GoalError(ValueError):
    """A rejected goal mutation. ``kind`` picks the protocol error code."""

    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind: ErrorKind = kind


@dataclass(frozen=True, slots=True)
class GoalSetRequest:
    """A user ``goal/set``; ``None`` fields leave the goal as it is."""

    objective: str | None = None
    status: Literal["active", "paused"] | None = None
    token_budget: int | None | _KeepBudget = KEEP_BUDGET
    replace: bool = False
    expected_goal_id: str | None = None
    expected_revision: int | None = None


def normalize_objective(objective: str) -> str:
    text = objective.strip()
    if not text:
        raise GoalError("invalid", "The goal objective cannot be empty.")
    if len(text) > MAX_OBJECTIVE_CHARS:
        raise GoalError(
            "invalid",
            f"The goal objective is limited to {MAX_OBJECTIVE_CHARS} characters "
            f"({len(text)} given). Point to a file for a longer specification.",
        )
    return text


def validate_budget(budget: int | None, max_budget: int | None) -> int | None:
    """The budget to store; under a cap, no budget means the cap."""
    if budget is None:
        return max_budget
    if budget <= 0:
        raise GoalError("invalid", "The token budget must be positive.")
    if max_budget is not None and budget > max_budget:
        raise GoalError(
            "invalid",
            f"The token budget cannot exceed {max_budget} tokens "
            "(max_goal_token_budget).",
        )
    return budget


def is_unfinished(goal: PublicGoal) -> bool:
    return goal.status is not GoalStatus.COMPLETE


def budget_reached(goal: PublicGoal) -> bool:
    return goal.token_budget is not None and goal.tokens_used >= goal.token_budget


def create_goal(
    objective: str, *, token_budget: int | None, max_budget: int | None, now_ms: int
) -> PublicGoal:
    return PublicGoal(
        goal_id=f"goal-{secrets.token_hex(8)}",
        revision=1,
        objective_revision=1,
        objective=normalize_objective(objective),
        status=GoalStatus.ACTIVE,
        token_budget=validate_budget(token_budget, max_budget),
        created_at=now_ms,
        updated_at=now_ms,
    )


def apply_user_set(
    current: PublicGoal | None,
    request: GoalSetRequest,
    *,
    max_budget: int | None,
    now_ms: int,
) -> PublicGoal:
    """The goal after a user ``goal/set``, following the design's status table."""
    if (
        current is None
        and not request.replace
        and (
            request.expected_goal_id is not None
            or request.expected_revision is not None
        )
    ):
        # An edit of a goal that is gone (cleared, or completed) must not
        # create a new one. A replace overwrites nothing, so it still creates.
        raise GoalError("conflict", "The goal was cleared. Reload it.")
    if current is None or request.replace:
        return _create_or_replace(
            current, request, max_budget=max_budget, now_ms=now_ms
        )
    _check_expected(current, request)
    if current.status is GoalStatus.COMPLETE:
        raise GoalError(
            "invalid", "This goal is complete. Set a new goal to start again."
        )
    goal = current
    if request.objective is not None:
        objective = normalize_objective(request.objective)
        if objective != goal.objective:
            goal = goal.model_copy(
                update={
                    "objective": objective,
                    "objective_revision": goal.objective_revision + 1,
                }
            )
    if not isinstance(request.token_budget, _KeepBudget):
        goal = _with_budget(goal, validate_budget(request.token_budget, max_budget))
    # Pausing a paused goal changes nothing, whoever paused it.
    if request.status == "paused" and goal.status is not GoalStatus.PAUSED:
        goal = _with_status(goal, GoalStatus.PAUSED, GoalStatusReason.USER)
    elif request.status == "active":
        goal = _resumed(goal)
    if goal == current:
        return current
    return goal.model_copy(
        update={"revision": current.revision + 1, "updated_at": now_ms}
    )


def apply_model_status(
    current: PublicGoal, status: ModelGoalStatus, *, now_ms: int
) -> PublicGoal:
    """The goal after the model's ``update_goal``. Only an active goal moves.

    A goal the budget stopped mid-turn can still be completed: that turn is
    the model's wrap-up, and the work it finishes must not be stranded.
    """
    wrapping_up = current.status is GoalStatus.BUDGET_LIMITED and status == "complete"
    if current.status is not GoalStatus.ACTIVE and not wrapping_up:
        raise GoalError(
            "conflict",
            f"The goal is {current.status.value}, not active: only the user can "
            "change it now. To continue working on it, ask the user to resume "
            "the goal.",
        )
    new_status = {
        "complete": GoalStatus.COMPLETE,
        "blocked": GoalStatus.BLOCKED,
        "paused": GoalStatus.PAUSED,
    }[status]
    return _with_status(current, new_status, GoalStatusReason.MODEL).model_copy(
        update={"revision": current.revision + 1, "updated_at": now_ms}
    )


def _create_or_replace(
    current: PublicGoal | None,
    request: GoalSetRequest,
    *,
    max_budget: int | None,
    now_ms: int,
) -> PublicGoal:
    if current is not None:
        _check_expected(current, request)
    if request.objective is None:
        raise GoalError("invalid", "A new goal needs an objective.")
    goal = create_goal(
        request.objective,
        token_budget=(
            None
            if isinstance(request.token_budget, _KeepBudget)
            else request.token_budget
        ),
        max_budget=max_budget,
        now_ms=now_ms,
    )
    if request.status == "paused":
        goal = _with_status(goal, GoalStatus.PAUSED, GoalStatusReason.USER)
    return goal


def _check_expected(current: PublicGoal, request: GoalSetRequest) -> None:
    if request.expected_goal_id is not None and request.expected_goal_id != (
        current.goal_id
    ):
        raise GoalError("conflict", "The goal was replaced. Reload it and try again.")
    if request.expected_revision is not None and request.expected_revision != (
        current.revision
    ):
        raise GoalError("conflict", "The goal changed. Reload it and try again.")


def _with_budget(goal: PublicGoal, budget: int | None) -> PublicGoal:
    goal = goal.model_copy(update={"token_budget": budget})
    if goal.status is GoalStatus.ACTIVE and budget_reached(goal):
        return _with_status(goal, GoalStatus.BUDGET_LIMITED, None)
    if goal.status is GoalStatus.BUDGET_LIMITED and not budget_reached(goal):
        return _with_status(goal, GoalStatus.ACTIVE, None)
    return goal


def _resumed(goal: PublicGoal) -> PublicGoal:
    if goal.status is GoalStatus.ACTIVE:
        return goal
    if goal.status is GoalStatus.BUDGET_LIMITED:
        raise GoalError(
            "invalid",
            "The goal reached its token budget. Raise or remove the budget to "
            "resume it.",
        )
    if budget_reached(goal):
        return _with_status(goal, GoalStatus.BUDGET_LIMITED, None)
    return _with_status(goal, GoalStatus.ACTIVE, None)


def _with_status(
    goal: PublicGoal, status: GoalStatus, reason: GoalStatusReason | None
) -> PublicGoal:
    return goal.model_copy(update={"status": status, "status_reason": reason})
