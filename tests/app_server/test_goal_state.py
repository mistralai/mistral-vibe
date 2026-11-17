from __future__ import annotations

import pytest

from vibe.app_server._goal_state import (
    MAX_OBJECTIVE_CHARS,
    GoalError,
    GoalSetRequest,
    apply_model_status,
    apply_user_set,
    create_goal,
)
from vibe.app_server.models import GoalStatus, GoalStatusReason, PublicGoal

_NOW = 1_000


def _goal(
    status: GoalStatus = GoalStatus.ACTIVE,
    *,
    tokens_used: int = 0,
    token_budget: int | None = None,
) -> PublicGoal:
    goal = create_goal(
        "Make the tests pass", token_budget=None, max_budget=None, now_ms=0
    )
    return goal.model_copy(
        update={
            "status": status,
            "tokens_used": tokens_used,
            "token_budget": token_budget,
        }
    )


def test_a_new_goal_is_active_with_fresh_counters() -> None:
    # Do
    goal = apply_user_set(
        None, GoalSetRequest(objective="  Ship it  "), max_budget=None, now_ms=_NOW
    )

    # Assert
    assert goal.objective == "Ship it"
    assert goal.status is GoalStatus.ACTIVE
    assert (goal.revision, goal.objective_revision) == (1, 1)
    assert (goal.tokens_used, goal.time_used_seconds) == (0, 0)


@pytest.mark.parametrize("objective", ["", "   ", "x" * (MAX_OBJECTIVE_CHARS + 1)])
def test_an_empty_or_oversized_objective_is_rejected(objective: str) -> None:
    # Do / Assert
    with pytest.raises(GoalError) as error:
        apply_user_set(
            None, GoalSetRequest(objective=objective), max_budget=None, now_ms=_NOW
        )
    assert error.value.kind == "invalid"


def test_a_budget_above_the_configured_cap_is_rejected() -> None:
    # Do / Assert
    with pytest.raises(GoalError, match="max_goal_token_budget"):
        apply_user_set(
            None,
            GoalSetRequest(objective="x", token_budget=200_000),
            max_budget=100_000,
            now_ms=_NOW,
        )


def test_replacing_a_goal_resets_its_identity_and_counters() -> None:
    # Prepare
    current = _goal(tokens_used=5_000)

    # Do
    replaced = apply_user_set(
        current,
        GoalSetRequest(
            objective="Another goal", replace=True, expected_goal_id=current.goal_id
        ),
        max_budget=None,
        now_ms=_NOW,
    )

    # Assert
    assert replaced.goal_id != current.goal_id
    assert replaced.tokens_used == 0
    assert replaced.status is GoalStatus.ACTIVE


def test_a_stale_revision_is_a_conflict() -> None:
    # Prepare
    current = _goal()

    # Do / Assert
    with pytest.raises(GoalError) as error:
        apply_user_set(
            current,
            GoalSetRequest(status="paused", expected_revision=current.revision + 1),
            max_budget=None,
            now_ms=_NOW,
        )
    assert error.value.kind == "conflict"


def test_editing_the_objective_keeps_the_goal_and_bumps_both_revisions() -> None:
    # Prepare
    current = _goal(tokens_used=300)

    # Do
    edited = apply_user_set(
        current,
        GoalSetRequest(objective="Make every test pass"),
        max_budget=None,
        now_ms=_NOW,
    )

    # Assert
    assert edited.goal_id == current.goal_id
    assert edited.tokens_used == 300
    assert edited.revision == current.revision + 1
    assert edited.objective_revision == current.objective_revision + 1


def test_a_budget_only_edit_keeps_the_objective_revision() -> None:
    # Prepare
    current = _goal()

    # Do
    edited = apply_user_set(
        current, GoalSetRequest(token_budget=50_000), max_budget=None, now_ms=_NOW
    )

    # Assert
    assert edited.revision == current.revision + 1
    assert edited.objective_revision == current.objective_revision


def test_an_unchanged_set_does_not_bump_the_revision() -> None:
    # Prepare
    current = _goal()

    # Do
    same = apply_user_set(
        current,
        GoalSetRequest(objective=current.objective, status="active"),
        max_budget=None,
        now_ms=_NOW,
    )

    # Assert
    assert same == current


@pytest.mark.parametrize(
    ("status", "budget", "expected"),
    [
        # Active: lowering the budget to usage or below stops it.
        (GoalStatus.ACTIVE, 100, GoalStatus.BUDGET_LIMITED),
        (GoalStatus.ACTIVE, 1_000, GoalStatus.ACTIVE),
        (GoalStatus.ACTIVE, None, GoalStatus.ACTIVE),
        # Budget limited: raising or removing the budget restarts it.
        (GoalStatus.BUDGET_LIMITED, 1_000, GoalStatus.ACTIVE),
        (GoalStatus.BUDGET_LIMITED, None, GoalStatus.ACTIVE),
        (GoalStatus.BUDGET_LIMITED, 100, GoalStatus.BUDGET_LIMITED),
        # Paused or blocked: a budget change never restarts it.
        (GoalStatus.PAUSED, 1_000, GoalStatus.PAUSED),
        (GoalStatus.BLOCKED, None, GoalStatus.BLOCKED),
    ],
)
def test_budget_changes_follow_the_status_table(
    status: GoalStatus, budget: int | None, expected: GoalStatus
) -> None:
    # Prepare
    current = _goal(status, tokens_used=500, token_budget=400)

    # Do
    edited = apply_user_set(
        current, GoalSetRequest(token_budget=budget), max_budget=None, now_ms=_NOW
    )

    # Assert
    assert edited.status is expected


@pytest.mark.parametrize(
    "status", [GoalStatus.PAUSED, GoalStatus.BLOCKED, GoalStatus.USAGE_LIMITED]
)
def test_resume_reactivates_a_stopped_goal(status: GoalStatus) -> None:
    # Prepare
    current = _goal(status)

    # Do
    resumed = apply_user_set(
        current, GoalSetRequest(status="active"), max_budget=None, now_ms=_NOW
    )

    # Assert
    assert resumed.status is GoalStatus.ACTIVE
    assert resumed.status_reason is None


def test_resuming_a_paused_goal_over_budget_lands_on_budget_limited() -> None:
    # Prepare
    current = _goal(GoalStatus.PAUSED, tokens_used=500, token_budget=400)

    # Do
    resumed = apply_user_set(
        current, GoalSetRequest(status="active"), max_budget=None, now_ms=_NOW
    )

    # Assert
    assert resumed.status is GoalStatus.BUDGET_LIMITED


def test_resuming_a_budget_limited_goal_asks_for_a_budget_change() -> None:
    # Prepare
    current = _goal(GoalStatus.BUDGET_LIMITED, tokens_used=500, token_budget=400)

    # Do / Assert
    with pytest.raises(GoalError, match="Raise or remove the budget"):
        apply_user_set(
            current, GoalSetRequest(status="active"), max_budget=None, now_ms=_NOW
        )


def test_pausing_records_the_user_as_the_reason() -> None:
    # Do
    paused = apply_user_set(
        _goal(), GoalSetRequest(status="paused"), max_budget=None, now_ms=_NOW
    )

    # Assert
    assert paused.status is GoalStatus.PAUSED
    assert paused.status_reason is GoalStatusReason.USER


def test_a_complete_goal_cannot_be_edited() -> None:
    # Do / Assert
    with pytest.raises(GoalError, match="complete"):
        apply_user_set(
            _goal(GoalStatus.COMPLETE),
            GoalSetRequest(objective="More"),
            max_budget=None,
            now_ms=_NOW,
        )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("complete", GoalStatus.COMPLETE),
        ("blocked", GoalStatus.BLOCKED),
        ("paused", GoalStatus.PAUSED),
    ],
)
def test_the_model_can_end_an_active_goal(status: str, expected: GoalStatus) -> None:
    # Prepare
    current = _goal()

    # Do
    updated = apply_model_status(current, status, now_ms=_NOW)  # type: ignore[arg-type]

    # Assert
    assert updated.status is expected
    assert updated.status_reason is GoalStatusReason.MODEL
    assert updated.revision == current.revision + 1


def test_the_model_cannot_move_a_goal_the_user_stopped() -> None:
    # Do / Assert
    with pytest.raises(GoalError) as error:
        apply_model_status(_goal(GoalStatus.PAUSED), "complete", now_ms=_NOW)
    assert error.value.kind == "conflict"
    assert "ask the user to run /goal resume" in str(error.value)


@pytest.mark.parametrize(
    "request_",
    [
        GoalSetRequest(objective="Edited", expected_revision=1),
        GoalSetRequest(objective="Edited", expected_goal_id="goal-gone"),
        GoalSetRequest(status="paused", expected_revision=1),
    ],
)
def test_an_edit_of_a_goal_that_is_gone_is_a_conflict(request_: GoalSetRequest) -> None:
    # Do / Assert
    with pytest.raises(GoalError) as error:
        apply_user_set(None, request_, max_budget=None, now_ms=_NOW)
    assert error.value.kind == "conflict"


def test_a_replace_creates_a_goal_even_when_the_expected_one_is_gone() -> None:
    # Do
    goal = apply_user_set(
        None,
        GoalSetRequest(objective="New", replace=True, expected_goal_id="goal-gone"),
        max_budget=None,
        now_ms=_NOW,
    )

    # Assert
    assert goal.objective == "New"


@pytest.mark.parametrize(
    "request_",
    [
        GoalSetRequest(objective="No budget given"),
        GoalSetRequest(objective="Budget removed", token_budget=None),
    ],
)
def test_a_goal_without_a_budget_gets_the_configured_cap(
    request_: GoalSetRequest,
) -> None:
    # Do
    goal = apply_user_set(None, request_, max_budget=100_000, now_ms=_NOW)

    # Assert
    assert goal.token_budget == 100_000


def test_removing_the_budget_under_a_cap_restores_the_cap() -> None:
    # Prepare
    current = _goal(token_budget=5_000)

    # Do
    edited = apply_user_set(
        current, GoalSetRequest(token_budget=None), max_budget=100_000, now_ms=_NOW
    )

    # Assert
    assert edited.token_budget == 100_000


def test_a_goal_the_model_creates_without_a_budget_gets_the_configured_cap() -> None:
    # Do
    goal = create_goal("Ship it", token_budget=None, max_budget=100_000, now_ms=_NOW)

    # Assert
    assert goal.token_budget == 100_000


def test_without_a_cap_a_goal_may_have_no_budget() -> None:
    # Do
    goal = create_goal("Ship it", token_budget=None, max_budget=None, now_ms=_NOW)

    # Assert
    assert goal.token_budget is None


def test_the_model_can_complete_a_goal_its_budget_stopped_mid_turn() -> None:
    # Prepare
    current = _goal(GoalStatus.BUDGET_LIMITED)

    # Do
    updated = apply_model_status(current, "complete", now_ms=_NOW)

    # Assert
    assert updated.status is GoalStatus.COMPLETE
    assert updated.status_reason is GoalStatusReason.MODEL


def test_a_budget_stopped_goal_cannot_be_blocked_or_paused_by_the_model() -> None:
    # Prepare
    current = _goal(GoalStatus.BUDGET_LIMITED)

    # Do / Assert
    for status in ("blocked", "paused"):
        with pytest.raises(GoalError):
            apply_model_status(current, status, now_ms=_NOW)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "reason",
    [
        GoalStatusReason.SUSPENDED,
        GoalStatusReason.INTERRUPTED,
        GoalStatusReason.MODEL,
        GoalStatusReason.USER,
    ],
)
def test_pausing_a_paused_goal_changes_nothing(reason: GoalStatusReason) -> None:
    # Prepare
    current = _goal(GoalStatus.PAUSED).model_copy(update={"status_reason": reason})

    # Do
    paused = apply_user_set(
        current, GoalSetRequest(status="paused"), max_budget=None, now_ms=_NOW
    )

    # Assert
    assert paused is current
