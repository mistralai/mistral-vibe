"""The goal side of a Unified Harness session adapter.

Owns the session's goal store and its life cycle: restore, suspension when the
session closes, the rewind hand-off, publishing each change to clients as a
``session/updated`` patch of ``PublicSession.goal``, and the per-turn work the
adapter reports: binding turns, accounting, and the notes the model receives.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Protocol

from vibe.app_server._goal import GOAL_TOOL_NAMES, GoalChange, GoalController, GoalHost
from vibe.app_server._goal_engine import (
    GoalEffects,
    TurnEnd,
    TurnOrigin,
    TurnOutcome,
    bind_turn,
    end_turn,
    observe_usage,
    settle_abandoned_turns,
    user_action_note,
    with_running_turns,
)
from vibe.app_server._goal_notices import (
    GoalNotice,
    change_notice,
    continuation_notice,
    with_turn_failure,
)
from vibe.app_server._goal_store import (
    GOAL_FILENAME,
    GoalAccounting,
    GoalStoreError,
    UnifiedGoalStore,
)
from vibe.app_server._session_backend_port import SessionBackendError
from vibe.app_server._time import now_ms
from vibe.app_server.models import (
    GoalStatus,
    GoalStatusReason,
    JsonPatchOperation,
    PublicEffectEntry,
    PublicGoal,
    PublicHistoryEntry,
    PublicSessionState,
    PublicTurn,
    PublicTurnStatus,
    TokenUsage,
)
from vibe.app_server.protocol import ProtocolErrorCode
from vibe.observability.logging import logger

__all__ = [
    "CLIENT_INTERRUPT",
    "GOAL_TOOL_ROUTES",
    "USER_INTERRUPT_REASONS",
    "GoalSessionHooks",
    "GoalTurnClock",
    "UnifiedGoalHost",
    "UnifiedSessionGoal",
    "billable_tokens",
    "goal_path",
    "goal_turn_outcome",
    "suspended",
    "turn_tool_effects",
]


def goal_path(session_dir: Path) -> Path:
    return session_dir / GOAL_FILENAME


def suspended(goal: PublicGoal | None) -> PublicGoal | None:
    """An active goal as a closed session keeps it: paused, waiting for the user."""
    if goal is None or goal.status is not GoalStatus.ACTIVE:
        return goal
    return goal.model_copy(
        update={
            "status": GoalStatus.PAUSED,
            "status_reason": GoalStatusReason.SUSPENDED,
            "revision": goal.revision + 1,
            "updated_at": now_ms(),
        }
    )


class GoalSessionHooks(Protocol):
    """What the goal needs of its session adapter."""

    def publish_goal_patch(self, patch: tuple[JsonPatchOperation, ...]) -> None: ...

    def publish_notice(self, message: str) -> None: ...

    async def tell_model(self, note: str) -> None:
        """Give the model a hidden note in its context; only called while idle."""
        ...

    def record_goal_event(self, name: str, properties: dict[str, Any]) -> None:
        """Send a telemetry event; never raises."""
        ...

    async def record_goal_notice(self, notice: GoalNotice, turn_id: str | None) -> None:
        """Persist one goal notice in the session's public history."""
        ...

    def goal_notice_recorded(self, goal_id: str, event: str) -> bool:
        """Whether a notice of ``event`` for that goal is already recorded."""
        ...


class UnifiedGoalHost(GoalHost):
    """Presents a Unified session adapter as a ``GoalHost``."""

    def __init__(
        self,
        *,
        require_session: Callable[[str], None],
        available: Callable[[], bool],
        max_token_budget: Callable[[], int | None],
        changed: Callable[[GoalChange], None],
    ) -> None:
        self._require_session = require_session
        self._available = available
        self._max_token_budget = max_token_budget
        self._changed = changed

    def require_session(self, session_id: str) -> None:
        self._require_session(session_id)

    def goals_available(self) -> bool:
        return self._available()

    def max_token_budget(self) -> int | None:
        return self._max_token_budget()

    def goal_changed(self, change: GoalChange) -> None:
        self._changed(change)


class UnifiedSessionGoal:
    """One root session's goal: store, controller, and life cycle."""

    def __init__(
        self,
        session_dir: Path,
        *,
        hooks: GoalSessionHooks,
        require_session: Callable[[str], None],
        available: Callable[[], bool],
        max_token_budget: Callable[[], int | None],
        persistent: Callable[[], bool],
    ) -> None:
        self._hooks = hooks
        # User actions the model has not been told about yet.
        self._pending_notes: list[str] = []
        self._notes_lock = asyncio.Lock()
        # Transcript notices not recorded yet, in order.
        self._pending_notices: list[tuple[GoalNotice, str | None]] = []
        self._flush_lock = asyncio.Lock()
        # The goal as it was before a stop the model or engine made in a running
        # turn; its notice waits for the turn to end.
        self._stopped_from: PublicGoal | None = None
        # The goal right after that stop, for when it has left the store by the
        # time its turn ends: a goal created after it in the same turn.
        self._stopped_to: PublicGoal | None = None
        # The stopped goal's leftover milliseconds at the stop.
        self._stopped_remainder_ms = 0
        # Where that stop's notice belongs in ``_pending_notices``. Notices from
        # here on were queued after the stop and wait so they cannot precede it.
        self._stop_at: int | None = None
        # Turns bound to the stopped goal. Their usage stays here when a later
        # goal drops the binding, so the notice can still charge the turn.
        self._stopped_turns: frozenset[str] = frozenset()
        # Latest tokens and milliseconds reported for a turn, until it ends.
        self._turn_usage: dict[str, tuple[int, int]] = {}
        # What a running turn had spent when a goal the model created replaced
        # the goal it stopped: the stopped goal keeps that, the new one is
        # charged only the rest of the turn.
        self._turn_offsets: dict[str, tuple[int, int]] = {}
        # A goal the model created during a running turn, which that turn joins.
        self._model_created: str | None = None
        # The goal clients last received, to skip unchanged patches.
        self._published: PublicGoal | None = None
        self.store = UnifiedGoalStore(goal_path(session_dir), persistent=persistent)
        self.controller = GoalController(
            UnifiedGoalHost(
                require_session=require_session,
                available=available,
                max_token_budget=max_token_budget,
                changed=self._changed,
            ),
            self.store,
        )

    @property
    def goal(self) -> PublicGoal | None:
        return self.store.goal

    async def restore(self) -> None:
        """Load the goal; an active one comes back paused, waiting for the user."""
        try:
            await self.store.restore()
        except GoalStoreError as exc:
            try:
                quarantine_path = await self.store.quarantine_corrupt_store()
            except GoalStoreError as recovery_exc:
                raise SessionBackendError(
                    ProtocolErrorCode.INTERNAL_ERROR, f"{exc}. {recovery_exc}"
                ) from recovery_exc
            logger.warning("Quarantined a corrupt goal", exc_info=exc)
            self._hooks.publish_notice(
                "The saved goal could not be restored. It was disabled and "
                f"preserved at {quarantine_path}."
            )
            return
        if self.store.accounting.turns:
            try:
                await self.store.update(settle_abandoned_turns)
            except GoalStoreError as exc:
                logger.warning(
                    "Failed to settle the goal's abandoned turns", exc_info=exc
                )
        await self._finish_completed_goal()
        await self.suspend()

    async def _finish_completed_goal(self) -> None:
        """Finish a goal the model completed in a turn the session never ended.

        The completion is reported and cleared when its turn ends; a session
        closed or killed during that turn leaves it ``complete`` on disk, so
        restore reports and clears it instead.
        """
        goal = self.store.goal
        if goal is None or goal.status is not GoalStatus.COMPLETE:
            return
        notice = change_notice(
            None, goal.model_copy(update={"status": GoalStatus.ACTIVE}), goal
        )
        # The notice may have landed before the clear did not: once is enough.
        if notice is not None and not self._hooks.goal_notice_recorded(
            goal.goal_id, "completed"
        ):
            self._pending_notices.append((notice, None))
            await self.flush_notices()
        await self._clear_complete_goal(goal.goal_id)

    async def suspend(self) -> None:
        """Pause an active goal so no automatic work starts without the user."""
        try:
            await self.store.flush()
        except GoalStoreError as exc:
            logger.warning("Failed to save the goal's running turns", exc_info=exc)
        if self.store.goal is None or self.store.goal.status is not GoalStatus.ACTIVE:
            return
        try:
            await self.store.update(
                lambda goal, accounting: (suspended(goal), accounting)
            )
        except GoalStoreError as exc:
            logger.warning("Failed to suspend the goal", exc_info=exc)

    async def adopt(self, goal: PublicGoal | None, accounting: GoalAccounting) -> None:
        """Take over the goal of the session this one replaces (a rewind)."""
        try:
            await self.store.replace(goal, accounting)
        except GoalStoreError as exc:
            raise SessionBackendError(
                ProtocolErrorCode.INTERNAL_ERROR, str(exc)
            ) from exc

    async def release(self) -> tuple[PublicGoal | None, GoalAccounting]:
        """Hand the goal over to a replacing session; this one keeps none."""
        handed = (self.store.goal, self.store.accounting)
        try:
            await self.store.replace(None)
        except GoalStoreError as exc:
            logger.warning("Failed to release the goal", exc_info=exc)
        return handed

    async def persist_after_promotion(self, *, was_ephemeral: bool) -> None:
        if not was_ephemeral:
            return
        try:
            await self.store.persist()
        except GoalStoreError as exc:
            logger.warning(
                "Failed to persist the goal after session promotion", exc_info=exc
            )
            self._hooks.publish_notice(
                "The session was saved, but its goal could not be saved. It stays "
                "active until this session closes."
            )

    def _changed(self, change: GoalChange) -> None:
        self._publish()
        _record_change(self._hooks, change.source, change.previous, change.goal)
        # A stop waits for the end of its turn so its notice has the final
        # counts. Notices queued after it wait too, or a new goal would show
        # before the goal it replaced had finished.
        if change.source == "model" and change.action == "set" and change.goal:
            self._model_created = change.goal.goal_id
        if change.source == "user" or change.action == "set":
            notice = change_notice(change.action, change.previous, change.goal)
            if notice is not None:
                self._pending_notices.append((notice, None))
        else:
            self._note_stop(change.previous, change.goal)
        if change.source == "user":
            note = user_action_note(change.action, change.previous, change.goal)
            if note is not None:
                self._pending_notes.append(note)

    async def flush_notices(self) -> None:
        """Record the pending transcript notices, in order, once each.

        Notices queued after a deferred stop wait with it. Recording the stop
        first keeps a later "Goal set" from appearing before the completion.
        """
        # The turn end (event stream) and a continuation (loop task) flush at
        # the same time; each notice must be recorded once, by one of them.
        async with self._flush_lock:
            while self._pending_notices and (
                self._stop_at is None or self._stop_at > 0
            ):
                pending = self._pending_notices[0]
                notice, turn_id = pending
                try:
                    await self._hooks.record_goal_notice(notice, turn_id)
                except Exception:
                    # Kept, in order, for the next flush: a notice is only
                    # dropped once it is recorded.
                    logger.warning("Failed to record a goal notice", exc_info=True)
                    return
                # Recording yields, and a stop may have been queued ahead of
                # this notice meanwhile: drop this one, wherever it now is.
                index = next(
                    (
                        i
                        for i, queued in enumerate(self._pending_notices)
                        if queued is pending
                    ),
                    None,
                )
                if index is None:
                    continue
                del self._pending_notices[index]
                if self._stop_at is not None and index < self._stop_at:
                    self._stop_at -= 1

    async def continuation_started(self, turn_id: str) -> None:
        """Bind the continuation turn and mark where it starts in the transcript."""
        await self.turn_started(turn_id, TurnOrigin.CONTINUATION)
        goal = self.store.goal
        # Only a turn bound to the active goal works on it; one started just
        # after a stop does not.
        if (
            goal is not None
            and goal.status is GoalStatus.ACTIVE
            and self.bound(turn_id)
        ):
            number = self.store.accounting.continuations
            self._pending_notices.append((continuation_notice(goal, number), turn_id))
        await self.flush_notices()

    async def flush_notes(self) -> None:
        """Deliver the notes about user actions, in order, once each.

        A note leaves the queue only once delivered: a failed delivery (say, a
        turn that started meanwhile) keeps it and the next idle moment retries.
        """
        # Two idle moments can flush at once; each note goes out once, by one.
        async with self._notes_lock:
            while self._pending_notes:
                note = self._pending_notes[0]
                try:
                    await self._hooks.tell_model(note)
                except Exception:
                    logger.warning("Failed to tell the model about a goal change")
                    return
                index = next(
                    (
                        i
                        for i, queued in enumerate(self._pending_notes)
                        if queued is note
                    ),
                    None,
                )
                if index is not None:
                    del self._pending_notes[index]

    async def track_turn(
        self, turn_id: str, origin: TurnOrigin, *, started: bool
    ) -> None:
        """Bind a starting turn; a running one binds only to a goal the model
        created in it, so that turn is charged too.
        """
        goal = self.store.goal
        if not started and (
            goal is None or goal.goal_id != self._model_created or self.bound(turn_id)
        ):
            return
        if not started:
            # Only the turn that created the goal joins it, and only once: a
            # later running turn must not bind to it on a resume.
            self._model_created = None
        if not started and turn_id in self._stopped_turns:
            self._turn_offsets[turn_id] = self._turn_usage.get(turn_id, (0, 0))
        await self.turn_started(turn_id, origin)

    async def turn_started(self, turn_id: str, origin: TurnOrigin) -> None:
        await self._apply(
            lambda goal, accounting: GoalEffects(
                goal, bind_turn(goal, accounting, turn_id=turn_id, origin=origin)
            )
        )

    async def turn_progressed(self, turn_id: str, *, tokens: int, time_ms: int) -> None:
        # Kept even after a replacement drops the binding: the stop notice is
        # the only remaining record of what the completed goal spent.
        if turn_id in self._stopped_turns or turn_id in self.store.accounting.turns:
            kept_tokens, kept_time = self._turn_usage.get(turn_id, (0, 0))
            self._turn_usage[turn_id] = (
                max(kept_tokens, tokens),
                max(kept_time, time_ms),
            )
        if turn_id not in self.store.accounting.turns:
            return
        spent_tokens, spent_time = self._turn_offsets.get(turn_id, (0, 0))
        # Running counts stay in memory; the file is written when the turn
        # ends or the goal changes.
        await self._apply(
            lambda goal, accounting: observe_usage(
                goal,
                accounting,
                turn_id=turn_id,
                turn_tokens=max(0, tokens - spent_tokens),
                turn_time_ms=max(0, time_ms - spent_time),
            ),
            accounting_only=True,
        )

    async def turn_ended(self, end: TurnEnd) -> None:
        # A goal the model created belongs to the turn that created it.
        self._model_created = None
        await self._apply(
            lambda goal, accounting: end_turn(goal, accounting, end, now_ms=now_ms())
        )
        await self._report_stop(end)
        await self.flush_notices()

    def _note_stop(self, previous: PublicGoal | None, goal: PublicGoal | None) -> None:
        """Remember a stop until its turn ends, and the turns then bound to it."""
        if self._stopped_from is not None or goal is None:
            return
        if change_notice(None, previous, goal) is None:
            return
        self._stopped_from = previous
        self._stopped_to = goal
        self._stop_at = len(self._pending_notices)
        # The stopped goal's own leftover milliseconds: a goal that replaces it
        # resets the accounting, so they must be read now.
        self._stopped_remainder_ms = self.store.accounting.time_remainder_ms
        bound = {
            turn_id: (turn.tokens, turn.time_ms)
            for turn_id, turn in self.store.accounting.turns.items()
            if turn.goal_id == goal.goal_id
        }
        self._stopped_turns = frozenset(bound)
        for turn_id, counted in bound.items():
            kept_tokens, kept_time = self._turn_usage.get(turn_id, (0, 0))
            self._turn_usage[turn_id] = (
                max(kept_tokens, counted[0]),
                max(kept_time, counted[1]),
            )

    async def _report_stop(self, end: TurnEnd) -> None:
        """Show the stop after its turn, with that turn charged; a complete
        goal is then done and leaves the session.
        """
        turn_id = end.turn_id
        previous, self._stopped_from = self._stopped_from, None
        stopped, self._stopped_to = self._stopped_to, None
        index = self._stop_at
        self._stop_at = None
        # A turn a new goal joined is split: the stopped goal keeps its share.
        offset = self._turn_offsets.pop(turn_id, None)
        spent = (
            (self._turn_usage.get(turn_id) if offset is None else offset)
            if turn_id in self._stopped_turns
            else None
        )
        for stopped_turn in self._stopped_turns:
            self._turn_usage.pop(stopped_turn, None)
        self._stopped_turns = frozenset()
        self._turn_usage.pop(turn_id, None)
        goal = self.store.goal
        reported = goal
        if stopped is not None and (goal is None or goal.goal_id != stopped.goal_id):
            # Replaced or cleared before the turn could be charged onto it.
            reported = _with_turn_usage(
                stopped, spent, carried_ms=self._stopped_remainder_ms
            )
        notice = change_notice(None, previous, reported)
        # Only a goal this failure blocked says why: a goal completed or
        # stopped at its budget before the turn failed keeps its own notice.
        if (
            notice is not None
            and end.outcome is TurnOutcome.FAILED
            and reported is not None
            and reported.status is GoalStatus.BLOCKED
            and reported.status_reason is GoalStatusReason.TURN_FAILED
        ):
            notice = with_turn_failure(
                notice, end.error_message, retryable=end.error_retryable
            )
        if notice is not None:
            at = len(self._pending_notices) if index is None else index
            self._pending_notices.insert(at, (notice, turn_id))
        if goal is None or goal.status is not GoalStatus.COMPLETE:
            return
        await self.flush_notices()
        if await self._clear_complete_goal(goal.goal_id):
            self._publish()

    async def _clear_complete_goal(self, complete_id: str) -> bool:
        """Clear a complete goal that is done; whether it was cleared.

        Only that goal, and only while still complete: recording its notice
        yields, so a goal set meanwhile must survive. The clear ends the goal's
        life, so it is reported like the user's ``/goal clear``.
        """
        cleared: list[PublicGoal] = []

        def clear_if_still_complete(
            current: PublicGoal | None, accounting: GoalAccounting
        ) -> tuple[PublicGoal | None, GoalAccounting]:
            if (
                current is None
                or current.goal_id != complete_id
                or current.status is not GoalStatus.COMPLETE
            ):
                return current, accounting
            cleared.append(current)
            return None, GoalAccounting()

        try:
            await self.store.update(clear_if_still_complete)
        except GoalStoreError as exc:
            logger.warning("Failed to clear the complete goal", exc_info=exc)
            return False
        if not cleared:
            return False
        _record_change(self._hooks, "engine", cleared[0], None)
        return True

    def bound(self, turn_id: str) -> bool:
        return turn_id in self.store.accounting.turns

    @property
    def stop_pending(self) -> bool:
        """Whether a stop waits for the end of its turn to be reported."""
        return self._stopped_from is not None

    async def _apply(
        self,
        step: Callable[[PublicGoal | None, GoalAccounting], GoalEffects],
        *,
        accounting_only: bool = False,
    ) -> GoalEffects:
        outcome: list[tuple[PublicGoal | None, GoalEffects]] = []

        def mutation(
            goal: PublicGoal | None, accounting: GoalAccounting
        ) -> tuple[PublicGoal | None, GoalAccounting]:
            # Read the goal under the store lock: a change committed while this
            # one waited must not be reported again.
            effects = step(goal, accounting)
            outcome.append((goal, effects))
            return effects.goal, effects.accounting

        try:
            await self.store.update(mutation, accounting_only=accounting_only)
        except GoalStoreError as exc:
            logger.warning("Failed to record goal accounting", exc_info=exc)
            return GoalEffects(self.store.goal, self.store.accounting)
        previous, effects = outcome[0]
        if effects.goal != previous:
            _record_change(self._hooks, "engine", previous, effects.goal)
            self._note_stop(previous, effects.goal)
        self._publish()
        return effects

    def _publish(self) -> None:
        live = self.live_goal
        if live == self._published:
            return
        self._published = live
        self._hooks.publish_goal_patch(goal_patch(live))

    @property
    def live_goal(self) -> PublicGoal | None:
        """The goal with its running turns counted, as clients see it.

        ``tokens_used`` and ``time_used_seconds`` are charged when a turn ends;
        until then this adds what the goal's running turns spent so far.
        """
        return with_running_turns(self.store.goal, self.store.accounting)


def _record_change(
    hooks: GoalSessionHooks,
    source: str,
    previous: PublicGoal | None,
    goal: PublicGoal | None,
) -> None:
    """Telemetry for one change. Never the objective: only ids, states, counts."""
    if goal is not None and (previous is None or previous.goal_id != goal.goal_id):
        hooks.record_goal_event(
            "vibe.goal.created",
            {
                "goal_id": goal.goal_id,
                "source": source,
                "has_token_budget": goal.token_budget is not None,
                "objective_chars": len(goal.objective),
            },
        )
        return
    if previous is None:
        return
    if goal is None:
        hooks.record_goal_event(
            "vibe.goal.deleted",
            {
                "goal_id": previous.goal_id,
                "source": source,
                "status": previous.status.value,
                **_counts(previous),
            },
        )
        return
    if goal.status is previous.status:
        _record_edit(hooks, source, previous, goal)
        return
    hooks.record_goal_event(
        "vibe.goal.updated",
        {
            "goal_id": goal.goal_id,
            "source": source,
            "previous_status": previous.status.value,
            "status": goal.status.value,
            "status_reason": (
                goal.status_reason.value if goal.status_reason is not None else None
            ),
            **_counts(goal),
        },
    )


def _record_edit(
    hooks: GoalSessionHooks, source: str, previous: PublicGoal, goal: PublicGoal
) -> None:
    """Telemetry for an edit that keeps the status: what changed, not to what."""
    has_objective_changed = goal.objective != previous.objective
    has_token_budget_changed = goal.token_budget != previous.token_budget
    if not (has_objective_changed or has_token_budget_changed):
        return
    hooks.record_goal_event(
        "vibe.goal.updated",
        {
            "goal_id": goal.goal_id,
            "source": source,
            "previous_status": previous.status.value,
            "status": goal.status.value,
            "has_objective_changed": has_objective_changed,
            "has_token_budget_changed": has_token_budget_changed,
            "objective_chars": len(goal.objective),
            **_counts(goal),
        },
    )


def _with_turn_usage(
    goal: PublicGoal, spent: tuple[int, int] | None, *, carried_ms: int = 0
) -> PublicGoal:
    """The goal plus one turn's usage, the same charge ``end_turn`` applies."""
    if spent is None:
        return goal
    tokens, time_ms = spent
    return goal.model_copy(
        update={
            "tokens_used": goal.tokens_used + tokens,
            "time_used_seconds": goal.time_used_seconds
            + (carried_ms + time_ms) // 1000,
        }
    )


def _counts(goal: PublicGoal) -> dict[str, Any]:
    return {
        "tokens_used": goal.tokens_used,
        "time_used_seconds": goal.time_used_seconds,
        "has_token_budget": goal.token_budget is not None,
    }


def goal_patch(goal: PublicGoal | None) -> tuple[JsonPatchOperation, ...]:
    # ``goal`` is omitted while unset, so ``add`` sets or replaces it and
    # ``remove`` drops it.
    if goal is None:
        return (JsonPatchOperation(op="remove", path="/goal"),)
    return (
        JsonPatchOperation(
            op="add", path="/goal", value=goal.model_dump(mode="json", by_alias=True)
        ),
    )


GOAL_TOOL_ROUTES = frozenset(f"vibe.{name}" for name in GOAL_TOOL_NAMES)

# The runtime's reason for an interrupt a client asked for.
CLIENT_INTERRUPT = "client interrupt"
# Interrupts the user asked for. A restart or a disconnect is not one.
USER_INTERRUPT_REASONS = frozenset({CLIENT_INTERRUPT, "approval cancel_turn"})


@dataclass(slots=True)
class _ClockedTurn:
    start_tokens: int
    started_at: float
    # Time spent waiting for the user (an approval, a question), not charged.
    waited: float = 0.0
    waiting_since: float | None = None


class GoalTurnClock:
    """Running token and time counts of the turns a goal may charge.

    Tokens are the session's billable total (root and subagents) minus what it
    was when the turn started. Time excludes every wait for the user.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.turns: dict[str, _ClockedTurn] = {}

    def start(self, turn_id: str, tokens: int, *, elapsed_s: float = 0.0) -> None:
        """Clock ``turn_id`` from ``tokens``, as if it began ``elapsed_s`` ago."""
        self.turns[turn_id] = _ClockedTurn(
            start_tokens=tokens, started_at=self._clock() - elapsed_s
        )

    def observe_callbacks(self, waiting: bool) -> None:
        now = self._clock()
        for turn in self.turns.values():
            if waiting and turn.waiting_since is None:
                turn.waiting_since = now
            elif not waiting and turn.waiting_since is not None:
                turn.waited += now - turn.waiting_since
                turn.waiting_since = None

    def progress(self, turn_id: str, tokens: int) -> tuple[int, int]:
        turn = self.turns.get(turn_id)
        if turn is None:
            return 0, 0
        now = self._clock()
        waited = turn.waited + (
            now - turn.waiting_since if turn.waiting_since is not None else 0.0
        )
        elapsed = max(0.0, now - turn.started_at - waited)
        return max(0, tokens - turn.start_tokens), int(elapsed * 1000)

    def ended(self, turn_id: str, tokens: int, *, duration_ms: int) -> tuple[int, int]:
        """What ``turn_id`` spent, given that it ran for ``duration_ms``.

        For a turn whose end was seen late: the clock has run on since it
        ended, so its time is the turn's own duration, less the waits the
        clock saw, never more than the clock counted.
        """
        used, clocked_ms = self.progress(turn_id, tokens)
        turn = self.turns.get(turn_id)
        waited_ms = int(turn.waited * 1000) if turn is not None else 0
        return used, max(0, min(clocked_ms, duration_ms - waited_ms))

    def forget(self, turn_id: str) -> None:
        self.turns.pop(turn_id, None)


def billable_tokens(
    root: PublicSessionState, children: Iterable[PublicSessionState]
) -> int:
    """Prompt minus cached plus completion tokens, root and subagents together."""
    return sum(_billable(state.session.token_usage) for state in (root, *children))


def _billable(usage: TokenUsage | None) -> int:
    if usage is None:
        return 0
    return max(0, usage.input_tokens - usage.cached_input_tokens) + usage.output_tokens


def turn_tool_effects(
    history: Iterable[PublicHistoryEntry], turn_id: str
) -> list[tuple[str, str]]:
    """``(tool_name, status)`` of the turn's settled tool calls."""
    return [
        (entry.detail.tool_name, entry.state.status)
        for entry in history
        if isinstance(entry, PublicEffectEntry)
        and entry.turn_id == turn_id
        and entry.state.status in {"completed", "failed", "cancelled", "skipped"}
    ]


def goal_turn_outcome(turn: PublicTurn, interrupt_reason: str | None) -> TurnOutcome:
    """How ``turn`` ended, given why it was interrupted, if it was."""
    match turn.status:
        case PublicTurnStatus.FAILED:
            return TurnOutcome.FAILED
        case PublicTurnStatus.INTERRUPTED:
            if interrupt_reason in USER_INTERRUPT_REASONS:
                return TurnOutcome.INTERRUPTED_BY_USER
            return TurnOutcome.INTERRUPTED
        case _:
            return TurnOutcome.COMPLETED
