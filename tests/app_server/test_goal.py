from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

import pytest

from vibe.app_server._goal import GoalChange, GoalController
from vibe.app_server._goal_state import GoalError
from vibe.app_server._goal_store import UnifiedGoalStore
from vibe.app_server._session_backend_port import SessionBackendError
from vibe.app_server.models import GoalStatus, GoalStatusReason
from vibe.app_server.protocol import (
    GoalClearResponse,
    GoalGetResponse,
    GoalSetResponse,
    ProtocolErrorCode,
)

_SESSION_ID = "session-1"


@dataclass
class FakeGoalHost:
    available: bool = True
    max_budget: int | None = None
    changes: list[GoalChange] = field(default_factory=list)

    def require_session(self, session_id: str) -> None:
        if session_id != _SESSION_ID:
            raise SessionBackendError(ProtocolErrorCode.NOT_FOUND, session_id)

    def goals_available(self) -> bool:
        return self.available

    def max_token_budget(self) -> int | None:
        return self.max_budget

    def goal_changed(self, change: GoalChange) -> None:
        self.changes.append(change)


def _controller(
    tmp_path: Path, host: FakeGoalHost | None = None
) -> tuple[GoalController, FakeGoalHost, UnifiedGoalStore]:
    host = host or FakeGoalHost()
    store = UnifiedGoalStore(tmp_path / "goal.json", persistent=lambda: True)
    return GoalController(host, store), host, store


async def _set(controller: GoalController, **params: Any) -> GoalSetResponse:
    result = await controller.dispatch("goal/set", {"sessionId": _SESSION_ID, **params})
    assert isinstance(result.response, GoalSetResponse)
    return result.response


@pytest.mark.asyncio
async def test_a_set_goal_is_persisted_before_the_response(tmp_path: Path) -> None:
    # Prepare
    controller, host, _store = _controller(tmp_path)

    # Do
    response = await _set(controller, objective="Make CI green", tokenBudget=1000)

    # Assert
    stored = json.loads((tmp_path / "goal.json").read_text())
    assert stored["goal"]["goalId"] == response.goal.goal_id
    assert stored["goal"]["tokenBudget"] == 1000
    assert [change.action for change in host.changes] == ["set"]


@pytest.mark.asyncio
async def test_a_restored_store_returns_the_same_goal(tmp_path: Path) -> None:
    # Prepare
    controller, _host, _store = _controller(tmp_path)
    created = await _set(controller, objective="Make CI green")
    restored = UnifiedGoalStore(tmp_path / "goal.json", persistent=lambda: True)

    # Do
    await restored.restore()

    # Assert
    assert restored.goal == created.goal


@pytest.mark.asyncio
async def test_a_retried_set_returns_the_recorded_answer(tmp_path: Path) -> None:
    # Prepare
    controller, host, _store = _controller(tmp_path)
    first = await _set(controller, objective="First", idempotencyKey="k-1")

    # Do
    retried = await _set(controller, objective="First", idempotencyKey="k-1")

    # Assert
    assert retried == first
    assert len(host.changes) == 1


@pytest.mark.asyncio
async def test_omitting_the_budget_keeps_it_and_null_removes_it(tmp_path: Path) -> None:
    # Prepare
    controller, _host, _store = _controller(tmp_path)
    await _set(controller, objective="Goal", tokenBudget=1000)

    # Do
    kept = await _set(controller, objective="Goal, refined")
    removed = await _set(controller, tokenBudget=None)

    # Assert
    assert kept.goal.token_budget == 1000
    assert removed.goal.token_budget is None


@pytest.mark.asyncio
async def test_a_stale_edit_is_a_conflict(tmp_path: Path) -> None:
    # Prepare
    controller, _host, _store = _controller(tmp_path)
    created = await _set(controller, objective="Goal")
    await _set(controller, objective="Goal, edited elsewhere")

    # Do / Assert
    with pytest.raises(SessionBackendError) as error:
        await _set(controller, status="paused", expectedRevision=created.goal.revision)
    assert error.value.code is ProtocolErrorCode.CONFLICT


@pytest.mark.asyncio
async def test_clear_removes_the_goal_file(tmp_path: Path) -> None:
    # Prepare
    controller, host, _store = _controller(tmp_path)
    created = await _set(controller, objective="Goal")

    # Do
    result = await controller.dispatch(
        "goal/clear", {"sessionId": _SESSION_ID, "expectedGoalId": created.goal.goal_id}
    )
    read = await controller.dispatch("goal/get", {"sessionId": _SESSION_ID})

    # Assert
    assert result.response == GoalClearResponse(cleared=True)
    assert read.response == GoalGetResponse(goal=None)
    assert not (tmp_path / "goal.json").exists()
    assert [change.action for change in host.changes] == ["set", "clear"]


@pytest.mark.asyncio
async def test_goals_are_unavailable_in_a_headless_session(tmp_path: Path) -> None:
    # Prepare
    controller, _host, _store = _controller(tmp_path, FakeGoalHost(available=False))

    # Do / Assert
    with pytest.raises(SessionBackendError) as error:
        await _set(controller, objective="Goal")
    assert error.value.code is ProtocolErrorCode.NOT_IMPLEMENTED


@pytest.mark.asyncio
async def test_the_model_cannot_replace_an_unfinished_goal(tmp_path: Path) -> None:
    # Prepare
    controller, _host, _store = _controller(tmp_path)
    await _set(controller, objective="The user's goal")

    # Do / Assert
    with pytest.raises(GoalError, match="Only the user") as error:
        await controller.run_tool(
            "create_goal", {"objective": "Another"}, call_id="call-1"
        )
    # Every client replaces a goal its own way: the model asks for the
    # action, not for the CLI's /goal command.
    assert "ask them to replace the goal" in str(error.value)
    assert "/goal" not in str(error.value)


@pytest.mark.asyncio
async def test_completing_asks_the_model_for_a_final_report(tmp_path: Path) -> None:
    # Prepare
    controller, host, _store = _controller(tmp_path)
    await _set(controller, objective="Goal")

    # Do
    result = await controller.run_tool(
        "update_goal", {"status": "complete"}, call_id="call-1"
    )

    # Assert
    assert result.goal is not None
    assert result.goal.status is GoalStatus.COMPLETE
    assert result.goal.status_reason is GoalStatusReason.MODEL
    assert result.instructions is not None
    assert host.changes[-1].source == "model"


@pytest.mark.asyncio
async def test_a_replayed_tool_call_does_not_apply_twice(tmp_path: Path) -> None:
    # Prepare
    controller, host, _store = _controller(tmp_path)
    await _set(controller, objective="Goal")
    first = await controller.run_tool(
        "update_goal", {"status": "complete"}, call_id="call-1"
    )

    # Do
    replayed = await controller.run_tool(
        "update_goal", {"status": "complete"}, call_id="call-1"
    )

    # Assert
    assert replayed == first
    assert len(host.changes) == 2


@pytest.mark.asyncio
async def test_a_corrupt_goal_file_fails_restore(tmp_path: Path) -> None:
    # Prepare
    (tmp_path / "goal.json").write_text("{not json")
    store = UnifiedGoalStore(tmp_path / "goal.json", persistent=lambda: True)

    # Do / Assert
    with pytest.raises(Exception, match="Failed to read the goal"):
        await store.restore()
    quarantined = await store.quarantine_corrupt_store()
    assert quarantined.exists()
    assert store.goal is None
