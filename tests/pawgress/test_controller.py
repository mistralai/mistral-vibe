from __future__ import annotations

import pytest

from vibe.core.pawgress.controller import GoalController
from vibe.core.pawgress.events import IslandStatus
from vibe.core.pawgress.goal import Goal, GoalStatus


def make_goal(**kwargs) -> Goal:
    base: dict[str, object] = {"goal_id": "g1", "description": "Do the thing"}
    base.update(kwargs)
    return Goal.model_validate(base)


@pytest.mark.asyncio
async def test_no_verify_goal_completes_on_first_turn_end():
    controller = GoalController(make_goal())

    decision = await controller.record_turn_end()

    assert decision.completed
    assert not decision.should_continue
    assert controller.goal.status is GoalStatus.COMPLETED
    assert controller.goal.completed


@pytest.mark.asyncio
async def test_failing_verify_continues_once_then_blocks_at_max_iterations():
    controller = GoalController(
        make_goal(verify_command="false", repeat=5, max_iterations=2)
    )

    first = await controller.record_turn_end()

    assert first.should_continue
    assert first.prompt is not None
    assert controller.goal.iteration == 2
    assert controller.goal.status is GoalStatus.WORKING

    second = await controller.record_turn_end()

    assert not second.should_continue
    assert not second.completed
    assert controller.goal.status is GoalStatus.BLOCKED


@pytest.mark.asyncio
async def test_passing_verify_completes_with_evidence_including_constraints():
    controller = GoalController(
        make_goal(
            verify_command="true",
            repeat=3,
            constraints=["No new dependencies", "Keep public API"],
        )
    )

    decision = await controller.record_turn_end()

    assert decision.completed
    assert controller.goal.status is GoalStatus.COMPLETED
    assert controller.goal.evidence
    assert "3/3 verification runs passed" in controller.goal.evidence
    assert "No new dependencies" in controller.goal.evidence
    assert "Keep public API" in controller.goal.evidence


def test_island_state_maps_status_and_reports_verification_progress():
    goal = make_goal(verify_command="pytest", repeat=5, last_pass_count=3)
    goal.status = GoalStatus.VERIFYING
    controller = GoalController(goal)

    state = controller.island_state(detail="checking")

    assert state.state is IslandStatus.VERIFYING
    assert state.detail == "checking"
    assert state.iteration == "1/8"
    verification = next(c for c in state.criteria if c.label == "Verification")
    assert not verification.done
    assert verification.progress == "3/5"


def test_island_state_maps_every_goal_status():
    for goal_status, island_status in (
        (GoalStatus.WORKING, IslandStatus.WORKING),
        (GoalStatus.VERIFYING, IslandStatus.VERIFYING),
        (GoalStatus.WAITING, IslandStatus.WAITING),
        (GoalStatus.BLOCKED, IslandStatus.BLOCKED),
        (GoalStatus.PAUSED, IslandStatus.PAUSED),
        (GoalStatus.COMPLETED, IslandStatus.COMPLETED),
    ):
        goal = make_goal()
        goal.status = goal_status
        assert GoalController(goal).island_state().state is island_status


@pytest.mark.asyncio
async def test_pause_prevents_continuation():
    controller = GoalController(make_goal(verify_command="false", repeat=2))
    controller.pause()

    decision = await controller.record_turn_end()

    assert not decision.should_continue
    assert not decision.completed
    assert controller.goal.status is GoalStatus.PAUSED


@pytest.mark.asyncio
async def test_stop_prevents_continuation():
    controller = GoalController(make_goal(verify_command="false", repeat=2))
    controller.stop()

    decision = await controller.record_turn_end()

    assert not decision.should_continue
    assert not decision.completed


def test_status_line_reports_working_verifying_and_complete_states():
    plain = GoalController(make_goal())
    assert plain.status_line() == "🐾 Pawgress · working"

    verifying = make_goal(verify_command="pytest", repeat=5, last_pass_count=2)
    verifying.status = GoalStatus.VERIFYING
    assert GoalController(verifying).status_line() == "🐾 Pawgress · verifying 2/5"

    done = make_goal()
    done.completed = True
    assert GoalController(done).status_line() == "🐾 Pawgress · complete"


def test_island_state_passes_context_and_usage_through():
    controller = GoalController(make_goal())

    state = controller.island_state(
        context_tokens=14_000,
        context_max=200_000,
        usage_used=170_000,
        usage_limit=500_000,
        usage_reset_seconds=40,
    )

    assert state.context_tokens == 14_000
    assert state.context_max == 200_000
    assert state.usage_used == 170_000
    assert state.usage_limit == 500_000
    assert state.usage_reset_seconds == 40


def test_island_state_context_usage_default_none():
    state = GoalController(make_goal()).island_state()
    assert state.context_tokens is None
    assert state.usage_limit is None
