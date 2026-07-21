from __future__ import annotations

from vibe.core.pawgress.goal import Goal, GoalStatus


def test_goal_default_field_values():
    goal = Goal(goal_id="g1", description="Do the thing")

    assert goal.verify_command is None
    assert goal.repeat == 1
    assert goal.constraints == []
    assert goal.max_iterations == 8
    assert goal.status is GoalStatus.WORKING
    assert goal.iteration == 1
    assert goal.last_pass_count == 0
    assert not goal.completed
    assert goal.evidence == []


def test_goal_json_dump_round_trips_through_model_validate():
    goal = Goal(
        goal_id="g1",
        description="Fix the failing cache test",
        verify_command="pytest test_cache.py",
        repeat=5,
        constraints=["No new dependencies"],
        max_iterations=4,
        status=GoalStatus.VERIFYING,
        iteration=3,
        last_pass_count=2,
        completed=False,
        evidence=["partial"],
    )

    dumped = goal.model_dump(mode="json")
    restored = Goal.model_validate(dumped)

    assert restored == goal
    assert dumped["status"] == "verifying"
