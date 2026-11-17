from __future__ import annotations

import pytest

from vibe.app_server._goal_engine import (
    STREAK_LIMIT,
    TurnEnd,
    TurnOrigin,
    TurnOutcome,
    bind_turn,
    continuation_message,
    count_tools,
    end_turn,
    observe_usage,
    settle_abandoned_turns,
    user_action_note,
)
from vibe.app_server._goal_state import create_goal
from vibe.app_server._goal_store import GoalAccounting
from vibe.app_server.models import (
    GoalStatus,
    GoalStatusReason,
    PublicGoal,
    TurnErrorCode,
)


def _goal(
    *, budget: int | None = None, status: GoalStatus = GoalStatus.ACTIVE
) -> PublicGoal:
    goal = create_goal(
        "Ship <it> & test", token_budget=budget, max_budget=None, now_ms=0
    )
    return goal.model_copy(update={"status": status})


def _end(
    turn_id: str = "t-1",
    *,
    origin: TurnOrigin = TurnOrigin.CONTINUATION,
    outcome: TurnOutcome = TurnOutcome.COMPLETED,
    error_code: str | None = None,
    tool_calls: int = 1,
    succeeded: int = 1,
    failed: int = 0,
) -> TurnEnd:
    return TurnEnd(
        turn_id=turn_id,
        origin=origin,
        outcome=outcome,
        error_code=error_code,
        tool_calls=tool_calls,
        succeeded_tool_calls=succeeded,
        failed_tool_calls=failed,
    )


def _run_turn(
    goal: PublicGoal,
    accounting: GoalAccounting,
    end: TurnEnd,
    *,
    tokens: int = 100,
    time_ms: int = 2_000,
) -> tuple[PublicGoal | None, GoalAccounting]:
    accounting = bind_turn(goal, accounting, turn_id=end.turn_id, origin=end.origin)
    effects = observe_usage(
        goal, accounting, turn_id=end.turn_id, turn_tokens=tokens, turn_time_ms=time_ms
    )
    effects = end_turn(effects.goal, effects.accounting, end, now_ms=1)
    return effects.goal, effects.accounting


def test_a_continuation_is_charged_once() -> None:
    # Prepare
    goal, accounting = _goal(), GoalAccounting()

    # Do
    charged, accounting = _run_turn(goal, accounting, _end())
    assert charged is not None
    replayed = end_turn(charged, accounting, _end(), now_ms=2)

    # Assert
    assert (charged.tokens_used, charged.time_used_seconds) == (100, 2)
    assert accounting.continuations == 1
    assert replayed.goal == charged


def test_loop_turns_are_never_charged() -> None:
    # Do
    charged, accounting = _run_turn(
        _goal(), GoalAccounting(), _end(origin=TurnOrigin.LOOP)
    )

    # Assert
    assert charged is not None
    assert charged.tokens_used == 0
    assert accounting.continuations == 0


def test_a_user_turn_is_charged_and_resets_the_breakers() -> None:
    # Prepare
    accounting = GoalAccounting(no_progress_streak=2, failure_streak=2)

    # Do
    charged, accounting = _run_turn(
        _goal(), accounting, _end(origin=TurnOrigin.USER, tool_calls=0, succeeded=0)
    )

    # Assert
    assert charged is not None
    assert charged.tokens_used == 100
    assert (accounting.no_progress_streak, accounting.failure_streak) == (0, 0)


def test_reaching_the_budget_mid_turn_stops_the_goal() -> None:
    # Prepare
    goal = _goal(budget=150)
    accounting = bind_turn(
        goal, GoalAccounting(), turn_id="t-1", origin=TurnOrigin.CONTINUATION
    )

    # Do
    early = observe_usage(
        goal, accounting, turn_id="t-1", turn_tokens=100, turn_time_ms=0
    )
    late = observe_usage(
        early.goal, early.accounting, turn_id="t-1", turn_tokens=200, turn_time_ms=0
    )
    ended = end_turn(late.goal, late.accounting, _end(), now_ms=1)

    # Assert
    assert early.goal is not None
    assert early.goal.status is GoalStatus.ACTIVE
    assert late.goal is not None
    assert late.goal.status is GoalStatus.BUDGET_LIMITED
    assert ended.goal is not None
    assert ended.goal.tokens_used == 200
    assert ended.goal.status is GoalStatus.BUDGET_LIMITED


def test_no_progress_for_three_continuations_blocks_the_goal() -> None:
    # Prepare
    goal: PublicGoal | None = _goal()
    accounting = GoalAccounting()

    # Do
    for index in range(STREAK_LIMIT):
        assert goal is not None
        goal, accounting = _run_turn(
            goal, accounting, _end(f"t-{index}", tool_calls=0, succeeded=0)
        )

    # Assert
    assert goal is not None
    assert goal.status is GoalStatus.BLOCKED
    assert goal.status_reason is GoalStatusReason.NO_PROGRESS


def test_interrupted_continuations_do_not_trip_the_breakers() -> None:
    # Prepare
    goal: PublicGoal | None = _goal()
    accounting = GoalAccounting()

    # Do
    for index in range(STREAK_LIMIT):
        assert goal is not None
        goal, accounting = _run_turn(
            goal,
            accounting,
            _end(
                f"t-{index}", outcome=TurnOutcome.INTERRUPTED, tool_calls=0, succeeded=0
            ),
        )

    # Assert
    assert goal is not None
    assert goal.status is GoalStatus.ACTIVE
    assert accounting.no_progress_streak == 0
    assert accounting.failure_streak == 0


def test_three_continuations_with_only_failed_tools_block_the_goal() -> None:
    # Prepare
    goal: PublicGoal | None = _goal()
    accounting = GoalAccounting()

    # Do
    for index in range(STREAK_LIMIT):
        assert goal is not None
        goal, accounting = _run_turn(
            goal, accounting, _end(f"t-{index}", tool_calls=2, succeeded=0, failed=2)
        )

    # Assert
    assert goal is not None
    assert goal.status_reason is GoalStatusReason.TOOL_FAILURES


def test_a_loop_turn_neither_counts_nor_resets_the_breakers() -> None:
    # Prepare
    accounting = GoalAccounting(no_progress_streak=2)

    # Do
    _goal_after, accounting = _run_turn(
        _goal(), accounting, _end(origin=TurnOrigin.LOOP, tool_calls=0, succeeded=0)
    )

    # Assert
    assert accounting.no_progress_streak == 2


@pytest.mark.parametrize(
    ("outcome", "error_code", "status", "reason"),
    [
        (
            TurnOutcome.INTERRUPTED_BY_USER,
            None,
            GoalStatus.PAUSED,
            GoalStatusReason.INTERRUPTED,
        ),
        (
            TurnOutcome.FAILED,
            TurnErrorCode.USAGE_LIMIT.value,
            GoalStatus.USAGE_LIMITED,
            None,
        ),
        (
            TurnOutcome.FAILED,
            TurnErrorCode.BACKEND_ERROR.value,
            GoalStatus.BLOCKED,
            GoalStatusReason.TURN_FAILED,
        ),
        (TurnOutcome.INTERRUPTED, None, GoalStatus.ACTIVE, None),
    ],
)
def test_how_a_turn_ends_decides_the_goal_status(
    outcome: TurnOutcome,
    error_code: str | None,
    status: GoalStatus,
    reason: GoalStatusReason | None,
) -> None:
    # Do
    goal, _accounting = _run_turn(
        _goal(),
        GoalAccounting(),
        _end(origin=TurnOrigin.USER, outcome=outcome, error_code=error_code),
    )

    # Assert
    assert goal is not None
    assert goal.status is status
    assert goal.status_reason is reason


def test_a_turn_bound_to_a_replaced_goal_is_not_charged_to_the_new_one() -> None:
    # Prepare
    old = _goal()
    accounting = bind_turn(
        old, GoalAccounting(), turn_id="t-1", origin=TurnOrigin.CONTINUATION
    )
    new = _goal()

    # Do
    effects = observe_usage(
        new, accounting, turn_id="t-1", turn_tokens=500, turn_time_ms=0
    )
    ended = end_turn(effects.goal, effects.accounting, _end(), now_ms=1)

    # Assert
    assert ended.goal == new
    assert "t-1" not in ended.accounting.turns


def test_the_continuation_message_escapes_the_objective() -> None:
    # Do
    message = continuation_message(_goal(budget=1000))

    # Assert
    assert "Ship &lt;it&gt; &amp; test" in message
    assert "<it>" not in message
    assert "1000 remaining" in message


def test_goal_tools_and_refused_calls_are_not_progress() -> None:
    # Do
    counts = count_tools(
        [("get_goal", "completed"), ("bash", "skipped"), ("bash", "failed")],
        ignored=frozenset({"get_goal"}),
    )

    # Assert
    assert counts == (2, 0, 1)


def test_user_action_notes_cover_each_action() -> None:
    # Prepare
    goal = _goal()
    edited = goal.model_copy(
        update={"objective": "Other", "objective_revision": goal.objective_revision + 1}
    )

    # Do / Assert
    assert "new session goal" in (user_action_note("set", None, goal) or "")
    assert "changed the goal objective" in (
        user_action_note("edit", goal, edited) or ""
    )
    assert "budget" in (user_action_note("edit", goal, goal) or "")
    assert "paused" in (user_action_note("pause", goal, goal) or "")
    assert "cleared" in (user_action_note("clear", goal, None) or "")


def test_turns_a_closed_session_left_running_are_charged_once_and_dropped() -> None:
    # Prepare
    goal = _goal()
    accounting = bind_turn(
        goal, GoalAccounting(), turn_id="t-1", origin=TurnOrigin.CONTINUATION
    )
    effects = observe_usage(
        goal, accounting, turn_id="t-1", turn_tokens=500, turn_time_ms=3_000
    )

    # Do
    settled, settled_accounting = settle_abandoned_turns(
        effects.goal, effects.accounting
    )
    again = settle_abandoned_turns(settled, settled_accounting)

    # Assert
    assert settled is not None
    assert settled.tokens_used == 500
    assert settled.time_used_seconds == 3
    assert settled_accounting.turns == {}
    assert again == (settled, settled_accounting)
    replayed = end_turn(settled, settled_accounting, _end(), now_ms=1)
    assert replayed.goal == settled


def test_short_turns_add_up_to_whole_seconds() -> None:
    # Prepare
    goal: PublicGoal | None = _goal()
    accounting = GoalAccounting()

    # Do
    for index in range(4):
        assert goal is not None
        goal, accounting = _run_turn(
            goal, accounting, _end(f"t-{index}"), tokens=10, time_ms=600
        )

    # Assert
    assert goal is not None
    assert goal.time_used_seconds == 2
    assert accounting.time_remainder_ms == 400
