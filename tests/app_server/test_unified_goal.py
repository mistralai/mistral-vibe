from __future__ import annotations

from collections.abc import Awaitable, Callable
import json
from pathlib import Path
from typing import Any

import pytest

from vibe.app_server._goal_engine import TurnEnd, TurnOrigin, TurnOutcome
from vibe.app_server._goal_notices import GoalNotice
from vibe.app_server._unified_goal import (
    GoalTurnClock,
    UnifiedSessionGoal,
    goal_turn_outcome,
)
from vibe.app_server.models import (
    GoalNoticeDetail,
    GoalStatus,
    GoalStatusReason,
    JsonPatchOperation,
    PublicError,
    PublicTurn,
    PublicTurnStatus,
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_the_clock_counts_tokens_from_the_turn_start() -> None:
    # Prepare
    clock = GoalTurnClock(_FakeClock())
    clock.start("t-1", tokens=1_000)

    # Do
    tokens, _time_ms = clock.progress("t-1", tokens=1_250)

    # Assert
    assert tokens == 250


def test_the_clock_excludes_time_waiting_for_the_user() -> None:
    # Prepare
    now = _FakeClock()
    clock = GoalTurnClock(now)
    clock.start("t-1", tokens=0)

    # Do: 2 s of work, 30 s waiting on an approval, 3 s of work.
    now.now += 2
    clock.observe_callbacks(waiting=True)
    now.now += 30
    clock.observe_callbacks(waiting=False)
    now.now += 3
    _tokens, time_ms = clock.progress("t-1", tokens=0)

    # Assert
    assert time_ms == 5_000


def _turn(status: PublicTurnStatus, *, error_code: str | None = None) -> PublicTurn:
    return PublicTurn(
        id="t-1",
        session_id="s-1",
        status=status,
        started_at=0,
        error=PublicError(message="x", code=error_code) if error_code else None,
    )


@pytest.mark.parametrize(
    ("reason", "outcome"),
    [
        ("client interrupt", TurnOutcome.INTERRUPTED_BY_USER),
        ("approval cancel_turn", TurnOutcome.INTERRUPTED_BY_USER),
        ("Interrupted by process restart", TurnOutcome.INTERRUPTED),
        (None, TurnOutcome.INTERRUPTED),
    ],
)
def test_only_a_user_interrupt_counts_as_one(
    reason: str | None, outcome: TurnOutcome
) -> None:
    # Do / Assert
    assert goal_turn_outcome(_turn(PublicTurnStatus.INTERRUPTED), reason) is outcome


def test_a_failed_turn_is_failed() -> None:
    # Do / Assert
    assert (
        goal_turn_outcome(
            _turn(PublicTurnStatus.FAILED, error_code="usage_limit"), None
        )
        is TurnOutcome.FAILED
    )


class _FakeGoalHooks:
    def __init__(self) -> None:
        self.notices: list[GoalNotice] = []
        # The turn each notice was recorded under, in order.
        self.notice_turns: list[str | None] = []
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.on_notice: Callable[[GoalNotice], Awaitable[None]] | None = None

    def publish_goal_patch(self, patch: tuple[JsonPatchOperation, ...]) -> None:
        pass

    def publish_notice(self, message: str) -> None:
        pass

    async def tell_model(self, note: str) -> None:
        pass

    def record_goal_event(self, name: str, properties: dict[str, Any]) -> None:
        self.events.append((name, properties))

    def goal_notice_recorded(self, goal_id: str, event: str) -> bool:
        return any(
            notice.detail.goal_id == goal_id and notice.detail.event == event
            for notice in self.notices
        )

    async def record_goal_notice(self, notice: GoalNotice, turn_id: str | None) -> None:
        self.notices.append(notice)
        self.notice_turns.append(turn_id)
        if self.on_notice is not None:
            await self.on_notice(notice)


_SESSION_ID = "session-1"


def _session_goal(session_dir: Path, hooks: _FakeGoalHooks) -> UnifiedSessionGoal:
    return UnifiedSessionGoal(
        session_dir,
        hooks=hooks,
        require_session=lambda _session_id: None,
        available=lambda: True,
        max_token_budget=lambda: None,
        persistent=lambda: True,
    )


async def _set_goal(goal: UnifiedSessionGoal, **params: Any) -> None:
    await goal.controller.dispatch("goal/set", {"sessionId": _SESSION_ID, **params})


def _turn_end(turn_id: str) -> TurnEnd:
    return TurnEnd(
        turn_id=turn_id,
        origin=TurnOrigin.CONTINUATION,
        outcome=TurnOutcome.COMPLETED,
        error_code=None,
        tool_calls=1,
        succeeded_tool_calls=1,
        failed_tool_calls=0,
    )


@pytest.mark.asyncio
async def test_a_goal_set_while_the_complete_notice_is_recorded_is_kept(
    tmp_path: Path,
) -> None:
    """*Prepare*: A goal the model completed in a running turn, and a user who
    sets a new goal while the completion notice is being recorded.
    *Do*: End the turn.
    *Assert*: Only the complete goal is cleared; the new goal stays.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await _set_goal(goal, objective="First")
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    await goal.controller.run_tool(
        "update_goal", {"status": "complete"}, call_id="call-1"
    )

    async def set_next(notice: GoalNotice) -> None:
        if notice.detail.event == "completed":
            await _set_goal(goal, objective="Next", replace=True)

    hooks.on_notice = set_next

    # Do
    await goal.turn_ended(_turn_end("t-1"))

    # Assert
    assert goal.goal is not None
    assert goal.goal.objective == "Next"
    assert goal.goal.status is GoalStatus.ACTIVE


@pytest.mark.asyncio
async def test_a_goal_the_model_pauses_shows_a_paused_notice_after_its_turn(
    tmp_path: Path,
) -> None:
    """*Prepare*: A goal the model pauses in a running turn, as the user asked.
    *Do*: End the turn.
    *Assert*: One "Goal paused" notice is recorded, for that turn, once the
    turn ends.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await _set_goal(goal, objective="First")
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    await goal.controller.run_tool("update_goal", {"status": "paused"}, call_id="c-1")
    before = [notice.detail.event for notice in hooks.notices]

    # Do
    await goal.turn_ended(_turn_end("t-1"))

    # Assert
    assert "paused" not in before
    paused = [notice for notice in hooks.notices if notice.detail.event == "paused"]
    assert [notice.message for notice in paused] == ["Goal paused"]
    assert paused[0].detail.status_reason is GoalStatusReason.MODEL
    assert goal.goal is not None
    assert goal.goal.status is GoalStatus.PAUSED


@pytest.mark.asyncio
async def test_a_turn_left_running_at_close_is_not_counted_again_on_reopen(
    tmp_path: Path,
) -> None:
    """*Prepare*: A session closed while a goal turn that spent 500 tokens ran.
    *Do*: Reopen it twice.
    *Assert*: The goal is paused, charged those tokens once, and keeps no
    running turn.
    """
    # Prepare
    closed = _session_goal(tmp_path, _FakeGoalHooks())
    await closed.restore()
    await _set_goal(closed, objective="Keep going")
    await closed.turn_started("t-1", TurnOrigin.CONTINUATION)
    await closed.turn_progressed("t-1", tokens=500, time_ms=3_000)
    await closed.suspend()

    # Do
    reopened = _session_goal(tmp_path, _FakeGoalHooks())
    await reopened.restore()
    again = _session_goal(tmp_path, _FakeGoalHooks())
    await again.restore()

    # Assert
    live = again.live_goal
    assert live is not None
    assert live.status is GoalStatus.PAUSED
    assert live.tokens_used == 500
    assert live.time_used_seconds == 3
    assert again.store.accounting.turns == {}


@pytest.mark.asyncio
async def test_a_turn_the_reopen_carries_on_is_charged_once_when_it_ends(
    tmp_path: Path,
) -> None:
    """*Prepare*: A session whose process died while a goal turn that spent 500
    tokens ran, reopened with that turn still running.
    *Do*: Let the carried-on turn spend 300 more tokens, then end it.
    *Assert*: The goal comes back paused and is charged the whole turn once,
    800 tokens, when it ends.
    """
    # Prepare
    closed = _session_goal(tmp_path, _FakeGoalHooks())
    await closed.restore()
    await _set_goal(closed, objective="Keep going")
    await closed.turn_started("t-1", TurnOrigin.CONTINUATION)
    await closed.turn_progressed("t-1", tokens=500, time_ms=3_000)
    await closed.suspend()
    reopened = _session_goal(tmp_path, _FakeGoalHooks())
    await reopened.restore(running_turn_id="t-1")

    # Do
    await reopened.turn_progressed("t-1", tokens=800, time_ms=5_000)
    await reopened.turn_ended(_turn_end("t-1"))

    # Assert
    live = reopened.live_goal
    assert live is not None
    assert live.status is GoalStatus.PAUSED
    assert live.tokens_used == 800
    assert live.time_used_seconds == 5
    assert reopened.store.accounting.turns == {}


@pytest.mark.asyncio
async def test_a_goal_completed_then_replaced_in_one_turn_still_shows_complete(
    tmp_path: Path,
) -> None:
    """*Prepare*: A model that completes its goal, then creates a new one, in
    the same turn.
    *Do*: End the turn.
    *Assert*: The transcript records the original goal, its completion, then
    the new goal; the new goal stays.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await _set_goal(goal, objective="First")
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    await goal.controller.run_tool(
        "update_goal", {"status": "complete"}, call_id="call-1"
    )
    await goal.controller.run_tool(
        "create_goal", {"objective": "Second"}, call_id="call-2"
    )

    # Do
    await goal.turn_ended(_turn_end("t-1"))

    # Assert
    assert [notice.detail.event for notice in hooks.notices] == [
        "set",
        "completed",
        "set",
    ]
    assert [notice.detail.objective for notice in hooks.notices] == [
        "First",
        "First",
        "Second",
    ]
    assert goal.goal is not None
    assert goal.goal.objective == "Second"


@pytest.mark.asyncio
async def test_a_goal_the_model_sets_names_its_turn_in_the_notice_detail(
    tmp_path: Path,
) -> None:
    """*Prepare*: No goal, and a running turn.
    *Do*: The model creates a goal from that turn.
    *Assert*: The "Goal set" notice names the turn in its detail, and is still
    recorded without a turn, so the history places it by its time.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()

    # Do
    await goal.controller.run_tool(
        "create_goal", {"objective": "Ship it"}, call_id="call-1", turn_id="t-1"
    )
    await goal.flush_notices()

    # Assert
    assert [notice.detail.event for notice in hooks.notices] == ["set"]
    assert hooks.notices[0].detail.turn_id == "t-1"
    assert hooks.notices[0].detail.model_dump(by_alias=True)["turnId"] == "t-1"
    assert hooks.notice_turns == [None]


@pytest.mark.asyncio
async def test_a_goal_the_user_sets_names_no_turn(tmp_path: Path) -> None:
    """*Prepare*: A session with a running turn.
    *Do*: The user sets a goal.
    *Assert*: Its notice names no turn, in its detail or as its own.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await goal.turn_started("t-1", TurnOrigin.USER)

    # Do
    await _set_goal(goal, objective="Ship it")
    await goal.flush_notices()

    # Assert
    assert [notice.detail.turn_id for notice in hooks.notices] == [None]
    assert hooks.notice_turns == [None]


@pytest.mark.asyncio
async def test_a_replaced_completion_counts_its_turn_before_the_new_goal(
    tmp_path: Path,
) -> None:
    """*Prepare*: A turn that already spent tokens. The model completes the
    goal, spends more, then creates a new goal, and the turn spends still more.
    *Do*: Flush notices before the turn ends, then end the turn.
    *Assert*: Nothing is recorded early. The completion carries the turn's
    final tokens and time and precedes the new goal, which stays.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await _set_goal(goal, objective="First")
    await goal.flush_notices()
    hooks.notices.clear()
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    await goal.turn_progressed("t-1", tokens=100, time_ms=1_000)
    await goal.controller.run_tool(
        "update_goal", {"status": "complete"}, call_id="call-1"
    )
    await goal.turn_progressed("t-1", tokens=250, time_ms=2_000)
    await goal.controller.run_tool(
        "create_goal", {"objective": "Second"}, call_id="call-2"
    )
    await goal.turn_progressed("t-1", tokens=800, time_ms=5_000)
    await goal.flush_notices()

    # Do
    assert hooks.notices == []
    await goal.turn_ended(_turn_end("t-1"))

    # Assert
    assert [notice.detail.event for notice in hooks.notices] == ["completed", "set"]
    completed = hooks.notices[0].detail
    assert completed.tokens_used == 800
    assert completed.time_used_seconds == 5
    assert completed.objective == "First"
    assert hooks.notices[1].detail.objective == "Second"
    assert goal.goal is not None
    assert goal.goal.objective == "Second"


@pytest.mark.asyncio
async def test_a_turn_split_by_a_replacement_charges_each_goal_its_share(
    tmp_path: Path,
) -> None:
    """*Prepare*: A turn that completes its goal after 250 tokens over 2s, then
    creates a new goal the running turn joins, and ends at 800 tokens over 5s.
    *Do*: End the turn.
    *Assert*: The completion counts the 250 tokens and 2s spent before the new
    goal took over; the new goal is charged only the remaining 550 tokens and 3s.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await _set_goal(goal, objective="First")
    await goal.flush_notices()
    hooks.notices.clear()
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    await goal.controller.run_tool(
        "update_goal", {"status": "complete"}, call_id="call-1"
    )
    await goal.turn_progressed("t-1", tokens=250, time_ms=2_000)
    await goal.controller.run_tool(
        "create_goal", {"objective": "Second"}, call_id="call-2"
    )
    await goal.track_turn("t-1", TurnOrigin.CONTINUATION, started=False)
    await goal.turn_progressed("t-1", tokens=800, time_ms=5_000)

    # Do
    await goal.turn_ended(_turn_end("t-1"))

    # Assert
    [completed] = [n.detail for n in hooks.notices if n.detail.event == "completed"]
    assert (completed.tokens_used, completed.time_used_seconds) == (250, 2)
    assert goal.goal is not None
    assert goal.goal.objective == "Second"
    assert (goal.goal.tokens_used, goal.goal.time_used_seconds) == (550, 3)


@pytest.mark.asyncio
async def test_a_completion_notice_counts_the_turn_once(tmp_path: Path) -> None:
    """*Prepare*: A goal completed in a turn that spent 800 tokens over 5s.
    *Do*: End the turn.
    *Assert*: The completion notice charges that turn once, then the goal
    leaves the session.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await _set_goal(goal, objective="First")
    await goal.flush_notices()
    hooks.notices.clear()
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    await goal.turn_progressed("t-1", tokens=100, time_ms=1_000)
    await goal.controller.run_tool(
        "update_goal", {"status": "complete"}, call_id="call-1"
    )
    await goal.turn_progressed("t-1", tokens=800, time_ms=5_000)

    # Do
    await goal.turn_ended(_turn_end("t-1"))

    # Assert
    assert [notice.detail.event for notice in hooks.notices] == ["completed"]
    completed = hooks.notices[0].detail
    assert completed.tokens_used == 800
    assert completed.time_used_seconds == 5
    assert goal.goal is None


@pytest.mark.asyncio
async def test_a_completed_goal_cleared_at_turn_end_reports_its_deletion(
    tmp_path: Path,
) -> None:
    """*Prepare*: A goal the model completed in a turn.
    *Do*: End the turn.
    *Assert*: The goal's clear is reported once, as an engine deletion of a
    complete goal.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await _set_goal(goal, objective="First")
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    await goal.controller.run_tool(
        "update_goal", {"status": "complete"}, call_id="call-1"
    )
    hooks.events.clear()

    # Do
    await goal.turn_ended(_turn_end("t-1"))

    # Assert
    deleted = [props for name, props in hooks.events if name == "vibe.goal.deleted"]
    assert len(deleted) == 1
    assert deleted[0]["source"] == "engine"
    assert deleted[0]["status"] == "complete"
    assert goal.goal is None


@pytest.mark.asyncio
async def test_a_note_the_model_could_not_receive_waits_for_the_next_try(
    tmp_path: Path,
) -> None:
    """*Prepare*: A user pause the model must hear about, and a first delivery
    that fails (a turn started meanwhile).
    *Do*: Flush the notes twice.
    *Assert*: Nothing is delivered by the failed try; the retry delivers the
    note, once.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    told: list[str] = []
    fail = True

    async def tell_model(note: str) -> None:
        if fail:
            raise RuntimeError("a turn is running")
        told.append(note)

    hooks.tell_model = tell_model  # type: ignore[method-assign]
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await _set_goal(goal, objective="Keep going")

    # Do
    await goal.flush_notes()
    failed_try = list(told)
    fail = False
    await goal.flush_notes()
    await goal.flush_notes()

    # Assert
    assert failed_try == []
    assert len(told) == 1
    assert "Keep going" in told[0]


@pytest.mark.asyncio
async def test_a_resumed_model_created_goal_does_not_take_over_a_running_turn(
    tmp_path: Path,
) -> None:
    """*Prepare*: A goal the model created in a turn that then ended, paused by
    the user, and a new turn already running when the user resumes it.
    *Do*: Observe the running turn again after the resume.
    *Assert*: The running turn stays unbound: only the creating turn joins.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await goal.controller.run_tool(
        "create_goal", {"objective": "Write a.txt"}, call_id="call-1"
    )
    await goal.track_turn("t-1", TurnOrigin.USER, started=False)
    await goal.turn_ended(_turn_end("t-1"))
    await _set_goal(goal, status="paused")
    await goal.track_turn("t-2", TurnOrigin.USER, started=True)
    await _set_goal(goal, status="active")

    # Do
    await goal.track_turn("t-2", TurnOrigin.USER, started=False)

    # Assert
    assert not goal.bound("t-2")


@pytest.mark.asyncio
async def test_a_replaced_completion_keeps_its_own_leftover_time(
    tmp_path: Path,
) -> None:
    """*Prepare*: A goal with 200 ms carried from an earlier turn, completed
    after 500 ms of a turn that then creates a new goal; the new goal's share
    of that turn leaves it 900 ms over a whole second.
    *Do*: End the turn.
    *Assert*: The completion counts the old goal's own 200 + 500 ms (no whole
    second), not the new goal's 900 ms leftover.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await _set_goal(goal, objective="First")
    await goal.turn_started("t-0", TurnOrigin.CONTINUATION)
    await goal.turn_progressed("t-0", tokens=10, time_ms=200)
    await goal.turn_ended(_turn_end("t-0"))
    await goal.flush_notices()
    hooks.notices.clear()
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    await goal.turn_progressed("t-1", tokens=20, time_ms=500)
    await goal.controller.run_tool(
        "update_goal", {"status": "complete"}, call_id="call-1"
    )
    await goal.controller.run_tool(
        "create_goal", {"objective": "Second"}, call_id="call-2"
    )
    await goal.track_turn("t-1", TurnOrigin.CONTINUATION, started=False)
    await goal.turn_progressed("t-1", tokens=40, time_ms=1_400)

    # Do
    await goal.turn_ended(_turn_end("t-1"))

    # Assert
    [completed] = [n.detail for n in hooks.notices if n.detail.event == "completed"]
    assert completed.time_used_seconds == 0
    assert goal.store.accounting.time_remainder_ms == 900


@pytest.mark.asyncio
async def test_a_goal_completed_in_a_turn_the_session_never_ended_is_finished_on_reopen(
    tmp_path: Path,
) -> None:
    """*Prepare*: A goal the model completed in a turn, then the session closed
    before that turn ended.
    *Do*: Reopen the session.
    *Assert*: The completion is recorded and the goal is cleared.
    """
    # Prepare
    closed = _session_goal(tmp_path, _FakeGoalHooks())
    await closed.restore()
    await _set_goal(closed, objective="Ship it")
    await closed.turn_started("t-1", TurnOrigin.CONTINUATION)
    await closed.controller.run_tool(
        "update_goal", {"status": "complete"}, call_id="call-1"
    )

    # Do
    hooks = _FakeGoalHooks()
    reopened = _session_goal(tmp_path, hooks)
    await reopened.restore()

    # Assert
    assert [n.detail.event for n in hooks.notices] == ["completed"]
    assert hooks.notices[0].detail.objective == "Ship it"
    assert reopened.goal is None
    assert [
        (name, props["source"], props["status"]) for name, props in hooks.events
    ] == [("vibe.goal.deleted", "engine", "complete")]


@pytest.mark.asyncio
async def test_a_notice_that_failed_to_record_is_kept_for_the_next_flush(
    tmp_path: Path,
) -> None:
    """*Prepare*: A goal set while recording notices fails.
    *Do*: Flush, then flush again once recording works.
    *Assert*: The "Goal set" notice is recorded once, on the second flush.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    fail = True
    recorded: list[str] = []

    async def record_goal_notice(notice: GoalNotice, turn_id: str | None) -> None:
        if fail:
            raise OSError("disk full")
        recorded.append(notice.detail.event)

    hooks.record_goal_notice = record_goal_notice  # type: ignore[method-assign]
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await _set_goal(goal, objective="Keep going")

    # Do
    await goal.flush_notices()
    failed_flush = list(recorded)
    fail = False
    await goal.flush_notices()
    await goal.flush_notices()

    # Assert
    assert failed_flush == []
    assert recorded == ["set"]


@pytest.mark.asyncio
async def test_a_completion_already_recorded_is_not_repeated_on_reopen(
    tmp_path: Path,
) -> None:
    """*Prepare*: A goal completed in a turn the session never ended, whose
    completion notice was already recorded before the clear could land.
    *Do*: Reopen the session.
    *Assert*: The goal is cleared without a second completion notice.
    """
    # Prepare
    closed = _session_goal(tmp_path, _FakeGoalHooks())
    await closed.restore()
    await _set_goal(closed, objective="Ship it")
    await closed.turn_started("t-1", TurnOrigin.CONTINUATION)
    await closed.controller.run_tool(
        "update_goal", {"status": "complete"}, call_id="call-1"
    )
    hooks = _FakeGoalHooks()
    reopened = _session_goal(tmp_path, hooks)
    await reopened.store.restore()
    stored = reopened.store.goal
    assert stored is not None
    hooks.notices.append(
        GoalNotice(
            message="Goal complete: Ship it",
            detail=GoalNoticeDetail(
                event="completed", goal_id=stored.goal_id, status=stored.status
            ),
        )
    )

    # Do
    await reopened.restore()

    # Assert
    assert [n.detail.event for n in hooks.notices] == ["completed"]
    assert reopened.goal is None


@pytest.mark.asyncio
async def test_running_turn_counts_are_written_only_when_the_turn_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An active goal with a bound continuation turn.
    *Do*: Report the turn's usage many times, as streamed chunks do, then end it.
    *Assert*: ``goal.json`` is not rewritten while the turn runs, the live goal
    still shows the running counts, and the end of the turn writes them.
    """
    # Prepare
    import vibe.app_server._goal_store as goal_store_module

    writes: list[str] = []
    original = goal_store_module.atomic_replace

    async def counting_replace(path: Path, content: str, **kwargs: Any) -> None:
        writes.append(content)
        await original(path, content, **kwargs)

    monkeypatch.setattr(goal_store_module, "atomic_replace", counting_replace)
    goal = _session_goal(tmp_path, _FakeGoalHooks())
    await goal.restore()
    await _set_goal(goal, objective="Keep going")
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    writes.clear()

    # Do
    for chunk in range(1, 21):
        await goal.turn_progressed("t-1", tokens=chunk * 10, time_ms=chunk * 75)
    live = goal.live_goal
    writes_while_running = len(writes)
    await goal.turn_ended(_turn_end("t-1"))

    # Assert
    assert writes_while_running == 0
    assert live is not None
    assert live.tokens_used == 200
    assert len(writes) == 1
    reopened = _session_goal(tmp_path, _FakeGoalHooks())
    await reopened.restore()
    assert reopened.goal is not None
    assert reopened.goal.tokens_used == 200


@pytest.mark.asyncio
async def test_closing_mid_turn_writes_the_running_counts(tmp_path: Path) -> None:
    """*Prepare*: A goal turn whose usage is only in memory.
    *Do*: Close the session (suspend), then reopen it.
    *Assert*: The reopened goal is charged what the turn had spent.
    """
    # Prepare
    goal = _session_goal(tmp_path, _FakeGoalHooks())
    await goal.restore()
    await _set_goal(goal, objective="Keep going")
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    await goal.turn_progressed("t-1", tokens=300, time_ms=2_000)

    # Do
    await goal.suspend()
    reopened = _session_goal(tmp_path, _FakeGoalHooks())
    await reopened.restore()

    # Assert
    assert reopened.goal is not None
    assert reopened.goal.tokens_used == 300


@pytest.mark.asyncio
async def test_two_concurrent_flushes_record_each_notice_once(tmp_path: Path) -> None:
    """*Prepare*: Pending notices whose recording yields, as a file write does.
    *Do*: Flush from two callers at once, as a turn end (event stream) and a
    continuation (loop task) do.
    *Assert*: Nothing raises, and each notice is recorded once, in order.
    """
    import asyncio

    # Prepare
    hooks = _FakeGoalHooks()

    async def slow(_notice: GoalNotice) -> None:
        await asyncio.sleep(0.01)

    hooks.on_notice = slow
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await _set_goal(goal, objective="Keep going")

    # Do: one notice pending, then two.
    await asyncio.gather(goal.flush_notices(), goal.flush_notices())
    await _set_goal(goal, status="paused")
    await _set_goal(goal, status="active")
    await asyncio.gather(goal.flush_notices(), goal.flush_notices())

    # Assert
    assert [notice.detail.event for notice in hooks.notices] == [
        "set",
        "paused",
        "resumed",
    ]


@pytest.mark.asyncio
async def test_a_continuation_started_after_a_stop_records_no_notice(
    tmp_path: Path,
) -> None:
    """*Prepare*: A goal blocked by a failed turn.
    *Do*: Report a continuation that started anyway.
    *Assert*: It is not bound, and no "Working on the goal" notice is recorded.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await _set_goal(goal, objective="Keep going")
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    await goal.turn_ended(
        TurnEnd(
            turn_id="t-1",
            origin=TurnOrigin.CONTINUATION,
            outcome=TurnOutcome.FAILED,
            error_code="backend_error",
            tool_calls=0,
            succeeded_tool_calls=0,
            failed_tool_calls=0,
        )
    )
    assert goal.goal is not None
    assert goal.goal.status is GoalStatus.BLOCKED
    hooks.notices.clear()

    # Do
    await goal.continuation_started("t-2")

    # Assert
    assert not goal.bound("t-2")
    assert hooks.notices == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retryable", "advice"), [(True, "/goal resume"), (False, "/rewind")]
)
async def test_a_goal_blocked_by_a_failed_turn_says_why_and_what_to_do(
    tmp_path: Path, retryable: bool, advice: str
) -> None:
    """*Prepare*: A goal turn that fails with a long provider error.
    *Do*: End the turn.
    *Assert*: The blocked notice quotes the error, bounded, and suggests what
    to do: resume when the error may pass, rewind otherwise.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await _set_goal(goal, objective="Keep going")
    await goal.flush_notices()
    hooks.notices.clear()
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    error = "image dimensions exceed max allowed size: 3000 pixels " + "x" * 2_000

    # Do
    await goal.turn_ended(
        TurnEnd(
            turn_id="t-1",
            origin=TurnOrigin.CONTINUATION,
            outcome=TurnOutcome.FAILED,
            error_code="backend_error",
            tool_calls=0,
            succeeded_tool_calls=0,
            failed_tool_calls=0,
            error_message=error,
            error_retryable=retryable,
        )
    )

    # Assert
    [notice] = hooks.notices
    assert notice.detail.event == "blocked"
    assert notice.message.startswith("Goal blocked: the turn failed: image dimensions")
    assert advice in notice.message
    assert notice.detail.error is not None
    assert len(notice.detail.error) <= 300
    assert "Keep going" not in notice.message


@pytest.mark.asyncio
async def test_pausing_a_suspended_goal_records_no_edit_and_tells_the_model_nothing(
    tmp_path: Path,
) -> None:
    """*Prepare*: A goal that came back paused (suspended) on reopen.
    *Do*: The user runs ``/goal pause``.
    *Assert*: The goal's revision is unchanged, no notice is recorded and no
    note waits for the model.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    closed = _session_goal(tmp_path, hooks)
    await closed.restore()
    await _set_goal(closed, objective="Keep going")
    await closed.suspend()
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    before = goal.goal
    hooks.notices.clear()

    # Do
    await _set_goal(goal, status="paused")
    await goal.flush_notices()

    # Assert
    assert goal.goal == before
    assert hooks.notices == []
    assert goal._pending_notes == []


@pytest.mark.asyncio
async def test_a_turn_cannot_complete_an_objective_it_never_saw(tmp_path: Path) -> None:
    """*Prepare*: A goal turn bound at objective revision 1, then a user edit
    of the objective (revision 2) while it runs.
    *Do*: That turn calls ``update_goal complete``; then a turn bound at
    revision 2 does.
    *Assert*: The first is a conflict quoting the new objective, escaped, and
    the goal stays active; the second completes it.
    """
    from vibe.app_server._goal_state import GoalError

    # Prepare
    goal = _session_goal(tmp_path, _FakeGoalHooks())
    await goal.restore()
    await _set_goal(goal, objective="Fix the parser")
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    await _set_goal(goal, objective="Fix the parser & add <tests>")
    assert goal.goal is not None
    assert goal.goal.objective_revision == 2

    # Do
    with pytest.raises(GoalError) as stale:
        await goal.controller.run_tool(
            "update_goal", {"status": "complete"}, call_id="call-1", turn_id="t-1"
        )
    still = goal.goal
    await goal.turn_ended(_turn_end("t-1"))
    await goal.turn_started("t-2", TurnOrigin.CONTINUATION)
    result = await goal.controller.run_tool(
        "update_goal", {"status": "complete"}, call_id="call-2", turn_id="t-2"
    )

    # Assert
    assert stale.value.kind == "conflict"
    assert "Fix the parser &amp; add &lt;tests&gt;" in str(stale.value)
    assert still is not None
    assert still.status is GoalStatus.ACTIVE
    assert result.goal is not None
    assert result.goal.status is GoalStatus.COMPLETE


@pytest.mark.asyncio
async def test_other_model_updates_ignore_the_objective_revision(
    tmp_path: Path,
) -> None:
    """Only a completion is guarded: a turn on an older objective may still
    report a blocker.
    """
    # Prepare
    goal = _session_goal(tmp_path, _FakeGoalHooks())
    await goal.restore()
    await _set_goal(goal, objective="Fix the parser")
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    await _set_goal(goal, objective="Fix the parser and the lexer")

    # Do
    result = await goal.controller.run_tool(
        "update_goal", {"status": "blocked"}, call_id="call-1", turn_id="t-1"
    )

    # Assert
    assert result.goal is not None
    assert result.goal.status is GoalStatus.BLOCKED


@pytest.mark.asyncio
async def test_goal_tools_report_the_running_turn_usage(tmp_path: Path) -> None:
    """*Prepare*: A goal charged 1,000 tokens and 4 s by an ended turn, and a
    running turn that has spent 500 tokens and 2.5 s so far.
    *Do*: The model calls ``get_goal``, then completes the goal.
    *Assert*: Both results count the running turn, as the goal row does.
    """
    # Prepare
    goal = _session_goal(tmp_path, _FakeGoalHooks())
    await goal.restore()
    await _set_goal(goal, objective="Keep going", tokenBudget=10_000)
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    await goal.turn_progressed("t-1", tokens=1_000, time_ms=4_000)
    await goal.turn_ended(_turn_end("t-1"))
    await goal.turn_started("t-2", TurnOrigin.CONTINUATION)
    await goal.turn_progressed("t-2", tokens=500, time_ms=2_500)

    # Do
    read = await goal.controller.run_tool("get_goal", {}, call_id="call-1")
    completed = await goal.controller.run_tool(
        "update_goal", {"status": "complete"}, call_id="call-2", turn_id="t-2"
    )

    # Assert
    for result in (read, completed):
        assert result.goal is not None
        assert result.goal.tokens_used == 1_500
        assert result.goal.time_used_seconds == 6
        assert result.remaining_tokens == 8_500


@pytest.mark.asyncio
async def test_a_failed_turn_after_a_completion_keeps_the_completion_notice(
    tmp_path: Path,
) -> None:
    """*Prepare*: A goal the model completes in a turn that then fails.
    *Do*: End that failed turn.
    *Assert*: The notice is a plain "Goal complete", without the failure text
    or advice a goal blocked by the failure gets.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await _set_goal(goal, objective="Ship it")
    await goal.flush_notices()
    hooks.notices.clear()
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    await goal.controller.run_tool(
        "update_goal", {"status": "complete"}, call_id="call-1", turn_id="t-1"
    )

    # Do
    await goal.turn_ended(
        TurnEnd(
            turn_id="t-1",
            origin=TurnOrigin.CONTINUATION,
            outcome=TurnOutcome.FAILED,
            error_code="backend_error",
            tool_calls=1,
            succeeded_tool_calls=1,
            failed_tool_calls=0,
            error_message="stream reset",
            error_retryable=False,
        )
    )

    # Assert
    [notice] = hooks.notices
    assert notice.detail.event == "completed"
    assert notice.message == "Goal complete: Ship it"
    assert notice.detail.error is None


@pytest.mark.asyncio
async def test_two_concurrent_note_flushes_send_each_note_once(tmp_path: Path) -> None:
    """*Prepare*: Two notes about user actions, and a model whose delivery
    yields, as the runtime call does.
    *Do*: Flush the notes from two idle moments at once.
    *Assert*: Each note reaches the model once, in order.
    """
    import asyncio

    # Prepare
    sent: list[str] = []

    class _YieldingHooks(_FakeGoalHooks):
        async def tell_model(self, note: str) -> None:
            await asyncio.sleep(0.01)
            sent.append(note)

    goal = _session_goal(tmp_path, _YieldingHooks())
    await goal.restore()
    await _set_goal(goal, objective="Ship it")
    await _set_goal(goal, status="paused")

    # Do
    await asyncio.gather(goal.flush_notes(), goal.flush_notes())

    # Assert
    assert len(sent) == 2
    assert "set a new session goal" in sent[0]
    assert "paused the session goal" in sent[1]
    assert goal._pending_notes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("edit", "objective_changed", "budget_changed"),
    [
        ({"objective": "Ship the secret plan v2"}, True, False),
        ({"tokenBudget": 50_000}, False, True),
        ({"objective": "Ship the secret plan v3", "tokenBudget": 9_000}, True, True),
    ],
)
async def test_an_edit_that_keeps_the_status_records_an_update_event(
    tmp_path: Path, edit: dict[str, Any], objective_changed: bool, budget_changed: bool
) -> None:
    """*Prepare*: An active goal.
    *Do*: The user edits its objective, its budget, or both.
    *Assert*: One ``vibe.goal.updated`` says what changed, with the new
    objective's length but never its text.
    """
    # Prepare
    hooks = _FakeGoalHooks()
    goal = _session_goal(tmp_path, hooks)
    await goal.restore()
    await _set_goal(goal, objective="Ship the secret plan")
    hooks.events.clear()

    # Do
    await _set_goal(goal, **edit)

    # Assert
    [(name, properties)] = hooks.events
    assert name == "vibe.goal.updated"
    assert properties["has_objective_changed"] is objective_changed
    assert properties["has_token_budget_changed"] is budget_changed
    assert properties["status"] == properties["previous_status"] == "active"
    assert goal.goal is not None
    assert properties["objective_chars"] == len(goal.goal.objective)
    assert "secret plan" not in json.dumps(properties)


@pytest.mark.asyncio
async def test_goal_get_and_set_report_the_running_turn_usage(tmp_path: Path) -> None:
    """*Prepare*: A goal with a bound running turn that has spent 500 tokens
    and 2.5 s.
    *Do*: A client reads the goal, then edits its budget.
    *Assert*: Both answers count the running turn, as ``PublicSession.goal``.
    """
    from vibe.app_server.protocol import GoalGetResponse, GoalSetResponse

    # Prepare
    goal = _session_goal(tmp_path, _FakeGoalHooks())
    await goal.restore()
    await _set_goal(goal, objective="Keep going")
    await goal.turn_started("t-1", TurnOrigin.CONTINUATION)
    await goal.turn_progressed("t-1", tokens=500, time_ms=2_500)

    # Do
    read = await goal.controller.dispatch("goal/get", {"sessionId": _SESSION_ID})
    edited = await goal.controller.dispatch(
        "goal/set", {"sessionId": _SESSION_ID, "tokenBudget": 10_000}
    )

    # Assert
    assert isinstance(read.response, GoalGetResponse)
    assert isinstance(edited.response, GoalSetResponse)
    for answer in (read.response.goal, edited.response.goal):
        assert answer is not None
        assert answer.tokens_used == 500
        assert answer.time_used_seconds == 2
    assert edited.response.goal == goal.live_goal
