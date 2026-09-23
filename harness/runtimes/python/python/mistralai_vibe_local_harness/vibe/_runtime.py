"""Durable Harness Step Protocol driving and Runtime-owned effect recovery."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
import secrets
import time
from typing import TYPE_CHECKING, Literal, cast

from pydantic import JsonValue, TypeAdapter

from mistralai_vibe_local_harness import HarnessSession
from mistralai_vibe_local_harness.protocol import (
    RustAcceptedApplyResult,
    RustAction,
    RustActionsNextAction,
    RustBackgroundProcessNotificationSource,
    RustCapabilitiesChange,
    RustCompactEvent,
    RustCompletionFailedEvent,
    RustCompletionModelInputResyncRequestedEvent,
    RustContextCompactedObservation,
    RustContextCompactionFailedObservation,
    RustDeterminismContext,
    RustEvent,
    RustFailTurnEvent,
    RustFilesystemAction,
    RustFilesystemFailedEvent,
    RustHarnessCapabilitySet,
    RustHarnessConfig,
    RustHarnessInput,
    RustHarnessNotification,
    RustHarnessSettings,
    RustHookCallActionBase,
    RustHookFailedEvent,
    RustInterruptEvent,
    RustInvalidStateRejection,
    RustKeepActionDirective,
    RustLLMCallAction,
    RustNotificationEvent,
    RustPendingCompletionAction,
    RustPluginContextDefinition,
    RustProtocolError,
    RustProvidedToolCallAction,
    RustReconfigureEvent,
    RustRefreshActionDirective,
    RustRejectedApplyResult,
    RustRuntimeBuiltinToolCallAction,
    RustSessionInspection,
    RustSessionTransition,
    RustSettingsChange,
    RustSkillDefinition,
    RustToolCallAction,
    RustToolFailedEvent,
    RustToolFailureResult,
    RustToolSucceededEvent,
    parse_apply_result,
)
from mistralai_vibe_local_harness.session_protocol import (
    JsonObject,
    PublicSessionState,
    TitleSource,
)
from mistralai_vibe_local_harness.vibe._errors import (
    HarnessInvalidSessionStoreError,
    HarnessStaleTurnError,
)
from mistralai_vibe_local_harness.vibe._observability import (
    add_recovery_failure,
    record_effect_reconciliation,
    record_session_operation,
)
from mistralai_vibe_local_harness.vibe._process_actions import (
    MutatingProcessAction,
    ProcessActionError,
    ValidatedProcessList,
    ValidatedProcessOutput,
    ValidatedProcessStart,
    ValidatedProcessStop,
    ValidatedProcessWrite,
    manager_error,
    process_error,
    process_failed,
    process_id,
    process_request_sha256,
    process_succeeded,
    resolve_process_start,
    validate_process_action,
    validate_process_config,
)
from mistralai_vibe_local_harness.vibe._processes._backend import PtyBackend
from mistralai_vibe_local_harness.vibe._processes._manager import (
    ProcessManagerError,
    ProcessTerminalSnapshot,
    SessionProcessManager,
)
from mistralai_vibe_local_harness.vibe._processes._output import (
    InvalidCursorError,
    OutputUnavailableError,
    ProcessOutputStore,
)
from mistralai_vibe_local_harness.vibe._projection import (
    ProjectionDelta,
    ProjectionUpdate,
    SessionProjector,
)
from mistralai_vibe_local_harness.vibe._runtime_config import (
    CompletionDelta,
    LocalRuntimeAdapterConfig,
)
from mistralai_vibe_local_harness.vibe._storage import (
    CommandReservedRecordV1,
    CoreInputRecordV1,
    HarnessStoreCapacityError,
    JournalRecordV1,
    ManagedProcessV1,
    PendingInternalCommand,
    PluginLockV1,
    PreparedProcessStartV1,
    ProjectionStateV1,
    RuntimeActionV1,
    RuntimeStateV3,
    SessionIdentity,
    StoredSession,
    UnifiedSessionStore,
    canonical_json,
    restore_core_from_checkpoint,
    sha256_json,
)

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.vibe._subagents._configuration import (
        ResolvedSubagentConfiguration,
    )
    from mistralai_vibe_local_harness.vibe._subagents._controller import (
        SubagentController,
    )

type ActionExecutor = Callable[[RustAction], Awaitable[RustEvent]]
type ActionRecoverer = Callable[[RuntimeActionV1, RustAction], Awaitable[RustEvent]]
type EventSink = Callable[[JsonObject], Awaitable[None] | None]
type ActionAppliedSink = Callable[[RustAction, RustEvent], Awaitable[None] | None]
type ApprovalRequester = Callable[[RustRuntimeBuiltinToolCallAction], Awaitable[bool]]
type WorkStateCallback = Callable[[], Awaitable[None] | None]
type ResponseFactory = Callable[[RustSessionTransition], JsonValue]
type RuntimeStateUpdate = Callable[[RuntimeStateV3], RuntimeStateV3]
type RecoveryMode = Literal[
    "reconnect_or_fail", "redeliver", "idempotent_retry", "reconcile", "fail"
]

_ACTION_ADAPTER = TypeAdapter(RustAction)
_CAPABILITY_RECONFIGURE_METHOD = "runtime/capabilities/reconfigure"
_SETTINGS_RECONFIGURE_METHOD = "runtime/settings/reconfigure"
_EVENT_ADAPTER = TypeAdapter(RustEvent)
_PROCESS_ACTION_OVERHEAD_BYTES = 256 * 1024
_PROCESS_LIFETIME_OVERHEAD_BYTES = 128 * 1024
# Half the 64 MiB per-segment cap: far enough below it that a single transition's
# appends always fit, high enough to avoid compacting every turn. A long
# non-quiescent turn that crosses this mark folds the journal into a fresh
# generation at the top of ``_apply`` rather than marching to the cap.
_JOURNAL_COMPACTION_HIGH_WATER_BYTES = 32 * 1024 * 1024
_PROCESS_DURABILITY_RETRY_DELAYS_SECONDS = (1, 2, 4, 8, 16, 30)

logger = logging.getLogger(__name__)
process_logger = logging.getLogger("vibe.unified_harness.processes")


@dataclass(frozen=True, slots=True)
class DurableCommandResult:
    response: JsonValue
    transition: RustSessionTransition | None
    replayed_receipt: bool = False


@dataclass(frozen=True, slots=True)
class ContextCompactionSuccess:
    summary: str


@dataclass(frozen=True, slots=True)
class ContextCompactionFailure:
    error: RustProtocolError


type ContextCompactionResult = ContextCompactionSuccess | ContextCompactionFailure


@dataclass(frozen=True, slots=True)
class _ReplayedCoreInput:
    sequence: int
    observed_at: int
    transition: RustSessionTransition


@dataclass(frozen=True, slots=True)
class _PendingProcessActionResult:
    action: RustAction
    durable: RuntimeActionV1
    event: RustEvent
    failed: bool
    completed: asyncio.Future[None]


@dataclass(frozen=True, slots=True)
class _PendingProcessCoreInput:
    command: RustEvent
    payload: RustHarnessInput
    transition: RustSessionTransition
    update: ProjectionUpdate
    reservation_owner: str
    wait_for_completion: bool
    completed: asyncio.Future[None]


class DurableSessionRuntime:  # noqa: PLR0904 - implements the Runtime port surface
    """Own one Core together with its write-ahead recovery protocol.

    Core inputs and results are serialized while independent Actions may run
    concurrently. An exact retry joins the live command already driving its
    Actions. A Core input is journaled before any resulting action is
    dispatched, action intent is durable before the executor runs, and the
    executor's result event is durable before it is supplied to Core.
    """

    def __init__(  # noqa: PLR0913 - explicit Runtime dependencies
        self,
        *,
        config: RustHarnessConfig,
        store: UnifiedSessionStore,
        core: HarnessSession,
        projection: PublicSessionState,
        projection_watermark: int,
        projection_sequence: int,
        replayed_transitions: tuple[RustSessionTransition, ...] = (),
        replayed_core_inputs: tuple[_ReplayedCoreInput, ...] = (),
        execute_action: ActionExecutor | None = None,
        recover_action: ActionRecoverer | None = None,
        event_sink: EventSink | None = None,
        process_manager: SessionProcessManager | None = None,
        process_config: LocalRuntimeAdapterConfig | None = None,
        request_process_approval: ApprovalRequester | None = None,
        identity: SessionIdentity | None = None,
    ) -> None:
        self._config = config
        self._post_recovery_capabilities: RustHarnessCapabilitySet | None = None
        self._post_recovery_settings: RustHarnessSettings | None = None
        self._store = store
        self._core = core
        self._identity = identity
        from mistralai_vibe_local_harness.vibe._storage import ProjectionStateV1

        self._projector = SessionProjector(
            ProjectionStateV1(
                session_id=store.session_id,
                snapshot_sequence=projection_sequence,
                watermark=projection_watermark,
                snapshot=projection,
            )
        )
        self._replayed_transition = (
            replayed_transitions[-1] if replayed_transitions else None
        )
        self._replayed_core_inputs = replayed_core_inputs
        self._execute_action = execute_action or _unavailable_action
        self._recover_action = recover_action
        self._event_sink = event_sink
        self._process_manager = process_manager
        self._process_config = process_config
        if process_config is not None:
            validate_process_config(process_config)
            if (
                process_manager is not None
                and process_config.command_environment
                != process_manager.backend.command_environment
            ):
                raise ValueError("process manager and command environment do not match")
        self._request_process_approval = request_process_approval
        self._process_start_barriers: dict[
            str, asyncio.Future[Literal["accepted", "failed"]]
        ] = {}
        self._terminal_tasks: dict[str, asyncio.Task[None]] = {}
        self._pending_terminal_snapshots: dict[str, ProcessTerminalSnapshot] = {}
        self._pending_process_action_results: dict[
            str, _PendingProcessActionResult
        ] = {}
        self._pending_process_core_input: _PendingProcessCoreInput | None = None
        self._terminal_retry_task: asyncio.Task[None] | None = None
        self._terminal_retry_wakeup = asyncio.Event()
        self._logged_terminal_processes: set[str] = set()
        self._process_action_reservations: dict[str, tuple[int, int, str | None]] = {}
        self._process_lifetime_reservations: dict[str, int] = {}
        self._process_callback_reservations: dict[str, str] = {}
        self._process_recovery_initialized = False
        self._work_state_callback: WorkStateCallback | None = None
        self._action_applied_sink: ActionAppliedSink | None = None
        self._subagent_controller: SubagentController | None = None
        # A restored pending transition may come either from replaying Core inputs or
        # directly from a stored generation. Both paths lost the Runtime's disposable
        # model-input cache and must refresh it before the next provider call.
        self._model_input_resync_required = bool(replayed_transitions)
        self._lock = asyncio.Lock()
        self._in_flight_commands: dict[str, asyncio.Future[DurableCommandResult]] = {}
        self._cancelled_commands: set[str] = set()
        self._unanswered_commands: dict[str, ResponseFactory] = {}
        self._closed = False

    @classmethod
    def restore(
        cls,
        *,
        config: RustHarnessConfig,
        store: UnifiedSessionStore,
        stored: StoredSession,
        execute_action: ActionExecutor | None = None,
        recover_action: ActionRecoverer | None = None,
        event_sink: EventSink | None = None,
        process_manager: SessionProcessManager | None = None,
        process_config: LocalRuntimeAdapterConfig | None = None,
        request_process_approval: ApprovalRequester | None = None,
    ) -> DurableSessionRuntime:
        core, transitions = stored.restore_core_with_transitions(config)
        replayed_config = stored.replayed_config(config)
        records = tuple(
            record for record in stored.journal if isinstance(record, CoreInputRecordV1)
        )
        replayed_core_inputs = tuple(
            _ReplayedCoreInput(
                sequence=record.sequence,
                observed_at=record.payload.input.determinism.time_unix_ms,
                transition=transition,
            )
            for record, transition in zip(records, transitions, strict=True)
        )
        replayed_transitions = transitions
        if (
            not replayed_transitions
            and stored.runtime_state.pending_transition is not None
        ):
            replayed_transitions = (stored.runtime_state.pending_transition,)
        runtime = cls(
            config=replayed_config,
            store=store,
            core=core,
            projection=stored.projection_state.snapshot,
            projection_watermark=stored.projection_state.watermark,
            projection_sequence=stored.projection_state.snapshot_sequence,
            replayed_transitions=replayed_transitions,
            replayed_core_inputs=replayed_core_inputs,
            execute_action=execute_action,
            recover_action=recover_action,
            event_sink=event_sink,
            process_manager=process_manager,
            process_config=process_config,
            request_process_approval=request_process_approval,
            identity=stored.runtime_state.identity,
        )
        if replayed_config.capabilities != config.capabilities:
            runtime._post_recovery_capabilities = config.capabilities
        if replayed_config.settings != config.settings:
            runtime._post_recovery_settings = config.settings
        runtime._restore_process_storage_reservations(stored)
        return runtime

    @property
    def inspection(self) -> RustSessionInspection:
        return RustSessionInspection.model_validate_json(self._core.inspect())

    @property
    def projection(self) -> PublicSessionState:
        return self._projector.projection.snapshot

    @property
    def watermark(self) -> int:
        return self._projector.projection.watermark

    @property
    def session_id(self) -> str:
        return self._store.session_id

    @property
    def runtime_state(self) -> RuntimeStateV3:
        return self._store.load().runtime_state

    @property
    def identity(self) -> SessionIdentity:
        """Who this Session is: its own id, its root, and its parent if it has one.

        Callers reach for this constantly -- deciding whether a Session is a
        subagent is the first thing most Host operations do -- so it is worth
        remembering. Identity is written once, when the store is created, and no
        generation ever rewrites it, which is what makes remembering it safe.
        """
        if self._identity is None:
            self._identity = self.runtime_state.identity
        return self._identity

    @property
    def config(self) -> RustHarnessConfig:
        return self._config.model_copy(deep=True)

    def configure_action_executor(self, execute_action: ActionExecutor) -> None:
        self._execute_action = execute_action

    def configure_action_applied_sink(self, sink: ActionAppliedSink) -> None:
        self._action_applied_sink = sink

    def configure_subagent_controller(self, controller: SubagentController) -> None:
        if (
            self._subagent_controller is not None
            and self._subagent_controller is not controller
        ):
            raise RuntimeError("subagent controller is already configured")
        self._subagent_controller = controller

    @property
    def pending_action_ids(self) -> frozenset[str]:
        return frozenset(action.action_id for action in self.inspection.pending_actions)

    async def commit_runtime_state(self, runtime_state: RuntimeStateV3) -> None:
        """Publish a complete private state generation at a safe effect boundary."""
        async with self._lock:
            self._guard_open()
            await self._catch_up_projection()
            stored = self._store.load()
            if (
                runtime_state.snapshot_sequence
                != stored.runtime_state.snapshot_sequence
            ):
                raise RuntimeError(
                    "Runtime-state update used a stale snapshot sequence"
                )
            await self._commit_runtime_state_off_thread(stored, runtime_state)

    async def update_runtime_state(self, update: RuntimeStateUpdate) -> None:
        """Atomically update private state against the latest recovery sequence."""
        async with self._lock:
            self._guard_open()
            await self._catch_up_projection()
            stored = self._store.load()
            runtime_state = update(stored.runtime_state.model_copy(deep=True))
            if (
                runtime_state.snapshot_sequence
                != stored.runtime_state.snapshot_sequence
            ):
                raise RuntimeError("Runtime-state update changed the snapshot sequence")
            if runtime_state == stored.runtime_state:
                return
            await self._commit_runtime_state_off_thread(stored, runtime_state)

    async def _commit_runtime_state_off_thread(
        self, stored: StoredSession, runtime_state: RuntimeStateV3
    ) -> None:
        """Run a private-state commit on a worker thread, holding ``self._lock`` throughout.

        The commit serialises the Core and rewrites a whole generation, which costs tens
        to hundreds of milliseconds once a transcript grows; on the event loop that is a
        contiguous freeze of every other session. It is shielded because the body retires
        ``self._core``: abandoning it mid-swap releases ``self._lock`` while a worker is
        still installing a replacement, so ``close`` retires the wrong Core and the new one
        leaks. Shielding keeps cancellation as atomic as it was while this ran inline.
        """
        commit = asyncio.create_task(
            asyncio.to_thread(self._commit_runtime_state_locked, stored, runtime_state),
            name=f"runtime-state-commit-{self._store.session_id}",
        )
        try:
            await asyncio.shield(commit)
        except asyncio.CancelledError:
            await commit
            raise

    def _core_advanced_since(
        self, stored: StoredSession, inspection: RustSessionInspection
    ) -> bool:
        """Report whether the Core has taken input since ``stored`` was published.

        Every Core input is journalled as it is applied, so a journal carrying none
        usually means the Core still holds exactly the checkpoint in ``stored``. Two
        states break that, both of them a write that failed after the Core had already
        accepted the input: a process input parks its record in
        ``_pending_process_core_input``, and a plain one leaves no trace at all. The
        cursor the last generation published is what catches the case nothing else
        records: the Core numbers its own inputs and now carries that sequence across
        generations, so standing anywhere past the published cursor is proof it took
        input the stored checkpoint does not contain.
        """
        if self._pending_process_core_input is not None:
            return True
        if any(isinstance(record, CoreInputRecordV1) for record in stored.journal):
            return True
        return inspection.last_input_id > stored.runtime_state.core_last_input_id

    def _capture_core_generation(self) -> tuple[dict[str, JsonValue], int]:
        """Capture the Core's checkpoint and its accepted-input cursor at one instant.

        The checkpoint carries no delivery state, so the cursor is persisted beside it
        and handed back on restore. Capturing both here keeps them describing the same
        Core: a generation that records one without the other would resume a sequence
        the checkpoint never reached.
        """
        checkpoint = cast(dict[str, JsonValue], json.loads(self._core.checkpoint()))
        return checkpoint, self.inspection.last_input_id

    def _commit_runtime_state_locked(
        self, stored: StoredSession, runtime_state: RuntimeStateV3
    ) -> None:
        inspection = self.inspection
        pending_transition = (
            self._replayed_transition
            if inspection.status in {"running", "compacting"}
            else None
        )
        projection_state = stored.projection_state.model_copy(
            update={"snapshot_sequence": runtime_state.snapshot_sequence}
        )
        # Most private-state edits publish a generation the Core did not contribute to:
        # a subagent lifecycle commits about 23 times while the Core takes input on only
        # a handful of them. Serialising the Core to rediscover the checkpoint we already
        # hold costs ~94 ms per MB of transcript, so reuse it when nothing has landed.
        checkpoint = (
            cast(dict[str, JsonValue], json.loads(self._core.checkpoint()))
            if self._core_advanced_since(stored, inspection)
            else stored.checkpoint
        )
        # The cursor has to describe the checkpoint beside it. Both readings come from
        # the same locked instant, and a skipped commit republishes the stored pair
        # unchanged, so the two never drift apart.
        core_last_input_id = inspection.last_input_id
        runtime_state = runtime_state.model_copy(
            update={
                "pending_transition": pending_transition,
                "core_last_input_id": core_last_input_id,
                "core_capabilities": self._config.capabilities,
                "core_settings": self._config.settings,
                "core_plugins": list(self._config.plugins),
            }
        )
        self._store.write_generation(
            checkpoint=checkpoint,
            runtime_state=runtime_state,
            projection_state=projection_state,
            core_action_ids=self.pending_action_ids,
        )
        self._projector = self._projector.rebased(projection_state)
        self._replayed_transition = pending_transition
        self._replayed_core_inputs = ()

    async def deliver_notification(
        self, notification: RustHarnessNotification
    ) -> RustSessionTransition:
        async with self._lock:
            self._guard_open()
            await self._catch_up_projection()
            transition = await self._apply(
                RustNotificationEvent(notification=notification)
            )
        return await self._drive(transition, recovering=False)

    async def fail_pending_action(
        self, *, action_id: str, expected_turn_id: str, error: RustProtocolError
    ) -> RustSessionTransition:
        async with self._lock:
            self._guard_open()
            await self._catch_up_projection()
            transition = await self._apply(
                RustFailTurnEvent(
                    action_id=action_id, expected_turn_id=expected_turn_id, error=error
                )
            )
        await self._reconcile_subagent_receipts()
        return transition

    def configure_process_runtime(
        self,
        manager: SessionProcessManager,
        config: LocalRuntimeAdapterConfig,
        request_approval: ApprovalRequester | None,
    ) -> None:
        self._process_manager = manager
        self._process_config = config
        self._request_process_approval = request_approval

    def configure_process_config(self, config: LocalRuntimeAdapterConfig) -> None:
        validate_process_config(config)
        if (
            self._process_manager is not None
            and config.command_environment
            != self._process_manager.backend.command_environment
        ):
            raise ValueError(
                "command environment cannot change while a Session is loaded"
            )
        self._process_config = config

    def configure_work_state_callback(self, callback: WorkStateCallback) -> None:
        self._work_state_callback = callback

    @property
    def has_active_process_work(self) -> bool:
        manager = self._process_manager
        if manager is not None and manager.has_live_processes():
            return True
        if self._process_action_reservations or self._process_lifetime_reservations:
            return True
        if self._pending_process_core_input is not None:
            return True
        return any(
            not task.done() or (not task.cancelled() and task.exception() is not None)
            for task in self._terminal_tasks.values()
        )

    def submit_terminal_snapshot(self, snapshot: ProcessTerminalSnapshot) -> None:
        existing = self._terminal_tasks.get(snapshot.process_id)
        if existing is not None and not existing.done():
            return
        self._pending_terminal_snapshots[snapshot.process_id] = snapshot
        self._schedule_terminal_snapshot(snapshot)
        self._terminal_retry_wakeup.set()

    def _schedule_terminal_snapshot(self, snapshot: ProcessTerminalSnapshot) -> None:
        task = asyncio.create_task(
            self._commit_terminal_snapshot(snapshot),
            name=f"process-terminal-{snapshot.process_id[-8:]}",
        )
        self._terminal_tasks[snapshot.process_id] = task
        task.add_done_callback(self._terminal_task_done)

    async def recover(self) -> RustSessionTransition | None:
        """Reconcile the last durable pending transition, if any."""
        self._terminal_retry_wakeup.set()
        started = time.perf_counter()
        try:
            await self._close_restarted_approval_callbacks()
            await self._recover_processes_after_restart()
            idle = False
            async with self._lock:
                self._guard_open()
                await self._catch_up_projection()
                if not self.inspection.pending_actions:
                    await self._settle_idle_locked()
                    record_session_operation(
                        time.perf_counter() - started,
                        operation="replay",
                        outcome="success",
                        source_backend="unified",
                    )
                    idle = True
                    transition = None
                else:
                    transition = self._replayed_transition
                    if transition is None:
                        raise RuntimeError(
                            "recoverable Core checkpoint has no replayed transition"
                        )
            if idle:
                await self._wait_for_terminal_updates()
                return None
            assert transition is not None
            terminal = await self._drive(transition, recovering=True)
            await self._wait_for_terminal_updates()
            async with self._lock:
                self._guard_open()
                # A recovery that lands blocked (an approval still outstanding, say)
                # keeps its pending actions, so the deferred configuration is adopted
                # by whichever drive next leaves the session idle instead.
                await self._settle_idle_locked()
        except Exception as exc:
            add_recovery_failure(failure_code=type(exc).__name__, phase="replay")
            record_session_operation(
                time.perf_counter() - started,
                operation="replay",
                outcome="failure",
                source_backend="unified",
            )
            logger.warning(
                "Unified session replay failed",
                extra={
                    "harness_backend": "unified",
                    "session_id": self._store.session_id,
                    "store_format": "mistral.vibe.unified-session-store/v1",
                    "restore_phase": "replay",
                    "failure_code": type(exc).__name__,
                },
                exc_info=exc,
            )
            raise
        record_session_operation(
            time.perf_counter() - started,
            operation="replay",
            outcome="success",
            source_backend="unified",
        )
        stored = await asyncio.to_thread(self._store.load)
        logger.info(
            "Unified session replay completed",
            extra={
                "harness_backend": "unified",
                "session_id": self._store.session_id,
                "recovery_journal_sequence": stored.runtime_state.snapshot_sequence,
            },
        )
        return terminal

    async def adopt_restored_configuration(self) -> None:
        """Settle a Session that binds idle and never recovers.

        ``recover`` settles a Session with a turn to replay. A child bound idle or
        held has none, and would otherwise spend its whole life on the capability
        set its journal replayed and on a reservation no later caller flushes --
        so this runs even with nothing deferred, since the flush is what lets the
        store go quiescent again.
        """
        async with self._lock:
            self._guard_open()
            await self._settle_idle_locked()

    async def drive_transition(
        self, transition: RustSessionTransition
    ) -> RustSessionTransition:
        """Drive an already-admitted command without delaying its caller."""
        terminal = await self._drive(transition, recovering=False)
        async with self._lock:
            self._guard_open()
            await self._settle_idle_locked()
        return terminal

    async def command(
        self,
        *,
        client_command_id: str,
        method: str,
        params: dict[str, JsonValue],
        command: RustEvent,
        response_factory: ResponseFactory,
        accepted_public_history_entries: Sequence[JsonObject] = (),
    ) -> DurableCommandResult:
        self._terminal_retry_wakeup.set()
        transition: RustSessionTransition | None = None
        owned: asyncio.Future[DurableCommandResult] | None = None
        try:
            async with self._lock:
                self._guard_open()
                await self._catch_up_projection()
                reservation = await asyncio.to_thread(
                    self._store.reserve_command, client_command_id, method, params
                )
                if reservation.completed:
                    return DurableCommandResult(
                        response=reservation.response,
                        transition=None,
                        replayed_receipt=True,
                    )
                in_flight = self._in_flight_commands.get(client_command_id)
                if in_flight is None:
                    # Admission publishes the turn before model execution starts, so an
                    # interrupt can cancel its caller while _apply is still returning.
                    owned = asyncio.get_running_loop().create_future()
                    self._in_flight_commands[client_command_id] = owned
                    transition = (
                        await self._apply(command)
                        if reservation.newly_reserved
                        else await self._resume_reserved_command(command)
                    )
                    # Shutdown must not answer commands Core has not accepted.
                    self._unanswered_commands[client_command_id] = response_factory
                    if accepted_public_history_entries:
                        existing_ids = {
                            entry_id
                            for entry in self._projector.projection.snapshot.history.entries
                            if isinstance(entry_id := entry.get("id"), str)
                        }
                        missing = [
                            entry
                            for entry in accepted_public_history_entries
                            if not isinstance(entry_id := entry.get("id"), str)
                            or entry_id not in existing_ids
                        ]
                        if missing:
                            now = time.time_ns() // 1_000_000
                            await self._record_projection_update(
                                self._projector.append_public_history_entries(
                                    missing, observed_at=now
                                )
                            )
            if in_flight is not None:
                completed = await asyncio.shield(in_flight)
                return DurableCommandResult(
                    response=completed.response, transition=None, replayed_receipt=True
                )
            assert transition is not None
            assert owned is not None
            terminal = await self._drive(
                transition, recovering=not reservation.newly_reserved
            )
            async with self._lock:
                self._guard_open()
                response = response_factory(terminal)
                await self._answer_command_locked(client_command_id, response)
                await self._settle_idle_locked()
                result = DurableCommandResult(response=response, transition=terminal)
            owned.set_result(result)
            return result
        except BaseException as error:
            if owned is None:
                raise
            if isinstance(error, asyncio.CancelledError):
                owned.cancel()
                self._cancelled_commands.add(client_command_id)
            else:
                # A command that failed has an outcome only its caller can
                # describe, so the Runtime stops holding an answer for it.
                self._unanswered_commands.pop(client_command_id, None)
                owned.set_exception(error)
                owned.exception()
            raise
        finally:
            if owned is not None:
                async with self._lock:
                    if self._in_flight_commands.get(client_command_id) is owned:
                        del self._in_flight_commands[client_command_id]

    async def _answer_command_locked(
        self, client_command_id: str, response: JsonValue
    ) -> None:
        """Write a reservation's outcome down, and only then give up the duty to write it.

        Every answer goes through here so that a receipt is answered once, and
        the claim is what makes that true: it is dropped once the write is
        known to have landed, never before. Dropping it earlier would let a
        cancelled caller leave ``close`` with nothing to wait for while the
        worker was still writing, and ``close`` would retire the Core -- and
        skip the fold that puts the Session back at an exported boundary --
        around a write still in flight.

        The write is shielded for the same reason ``_compact_store`` shields
        its fold: a worker thread does not stop when the task awaiting it is
        cancelled, so the honest thing for a canceller to do is wait for it.
        """
        write = asyncio.create_task(
            asyncio.to_thread(self._store.succeed_command, client_command_id, response),
            name=f"receipt-{client_command_id}",
        )
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            await write
            raise
        finally:
            # A write that failed leaves the claim standing: the receipt is
            # still unanswered, and the Session still owes an answer.
            if not write.cancelled() and write.exception() is None:
                self._unanswered_commands.pop(client_command_id, None)

    async def _answer_abandoned_commands_locked(self) -> None:
        """Answer the receipts of commands whose turn ended without their caller.

        A command reserves its receipt before Core sees it and answers it once
        the turn it started is through. A Session is shut down by cancelling
        the tasks driving its commands, and Core reaches its terminal
        transition well before the task carrying it has finished writing that
        turn down -- so a shutdown routinely lands between the two and strands
        the reservation. An open reservation is the Session holding a command
        in flight: left open, the Session is never quiescent again, no
        checkpoint is exported, the journal never drains, and every later fork,
        rewind and compaction is refused for want of an export.

        Core holding no pending Actions is what makes the answer honest. It
        says the turn these commands started is over, so their outcome can only
        be the response they would have been answered with. A Session stopped
        while its turn was still running keeps its reservations instead, which
        is what lets a client re-send the command and have the turn picked up
        where it left off -- and a Session that never reached this code at all,
        because the process died, keeps them for the same reason.

        The fold runs whether or not there was an answer to write, because a
        caller cancelled after its own answer landed leaves the journal
        unfolded just the same, and a Session is read back from its exported
        boundary: the next reader may be a fork that never resumes it first.
        """
        if self.inspection.pending_actions:
            return
        terminal = self._replayed_transition
        for client_command_id, response_factory in tuple(
            self._unanswered_commands.items()
        ):
            response = response_factory(terminal) if terminal is not None else {}
            await self._answer_command_locked(client_command_id, response)
            logger.info(
                "Unified session answered a command receipt its caller left open",
                extra={
                    "harness_backend": "unified",
                    "session_id": self._store.session_id,
                    "store_format": "mistral.vibe.unified-session-store/v1",
                    "client_command_id": client_command_id,
                },
            )
        await self._compact_store()

    async def command_without_driving_actions(
        self,
        *,
        client_command_id: str,
        method: str,
        params: dict[str, JsonValue],
        command: RustEvent,
        response_factory: ResponseFactory,
    ) -> DurableCommandResult:
        async with self._lock:
            self._guard_open()
            await self._catch_up_projection()
            reservation = await asyncio.to_thread(
                self._store.reserve_command, client_command_id, method, params
            )
            if reservation.completed:
                return DurableCommandResult(
                    response=reservation.response,
                    transition=None,
                    replayed_receipt=True,
                )
            transition = (
                await self._apply(command)
                if reservation.newly_reserved
                else await self._resume_reserved_command(command)
            )
            response = response_factory(transition)
            await asyncio.to_thread(
                self._store.succeed_command, client_command_id, response
            )
            return DurableCommandResult(response=response, transition=transition)

    async def append_public_history_entries(self, entries: list[JsonObject]) -> None:
        if not entries:
            return
        async with self._lock:
            self._guard_open()
            await self._catch_up_projection()
            now = time.time_ns() // 1_000_000
            await self._record_projection_update(
                self._projector.append_public_history_entries(entries, observed_at=now)
            )

    async def append_provisional_completion_content(
        self, delta: CompletionDelta
    ) -> None:
        """Project a provisional fragment of an in-flight completion.

        Best-effort publication from the completion executor: a projection
        failure must not fail the turn, so it surfaces to the caller (which
        treats the sink as advisory) rather than this method swallowing it.
        """
        async with self._lock:
            self._guard_open()
            await self._catch_up_projection()
            now = time.time_ns() // 1_000_000
            await self._record_projection_update(
                self._projector.append_provisional_completion_content(
                    delta, observed_at=now
                )
            )

    async def rename_session(
        self,
        title: str,
        *,
        observed_at: int | None = None,
        source: TitleSource = "manual",
    ) -> None:
        async with self._lock:
            self._guard_open()
            await self._catch_up_projection()
            when = (
                observed_at if observed_at is not None else time.time_ns() // 1_000_000
            )
            await self._record_projection_update(
                self._projector.rename_session(title, observed_at=when, source=source)
            )

    def register_callback(
        self, callback_id: str, kind: str, routing: dict[str, JsonValue]
    ) -> None:
        self._guard_open()
        self._store.register_callback(callback_id, kind, routing)
        logger.info(
            "Unified callback state changed",
            extra={
                "harness_backend": "unified",
                "session_id": self._store.session_id,
                "callback_id": callback_id,
                "callback_state": "pending",
            },
        )

    async def register_callback_durable(
        self, callback_id: str, kind: str, routing: dict[str, JsonValue]
    ) -> None:
        async with self._lock:
            owner = self._store.current_journal_reservation_owner
            await asyncio.to_thread(self.register_callback, callback_id, kind, routing)
            if owner is not None:
                self._process_callback_reservations[callback_id] = owner

    def resolve_callback(
        self, callback_id: str, result: JsonValue, *, failed: bool = False
    ) -> None:
        self._guard_open()
        self._store.resolve_callback(callback_id, result, failed=failed)
        logger.info(
            "Unified callback state changed",
            extra={
                "harness_backend": "unified",
                "session_id": self._store.session_id,
                "callback_id": callback_id,
                "callback_state": "failed" if failed else "resolved",
            },
        )

    async def resolve_callback_durable(
        self, callback_id: str, result: JsonValue, *, failed: bool = False
    ) -> None:
        async with self._lock:
            owner = self._process_callback_reservations.get(callback_id)
            with self._store.use_journal_reservation(owner):
                await asyncio.to_thread(
                    self.resolve_callback, callback_id, result, failed=failed
                )
            self._process_callback_reservations.pop(callback_id, None)

    async def reconfigure_skills(self, skills: Sequence[RustSkillDefinition]) -> None:
        await self.reconfigure_capability_dimension(
            lambda live: live.model_copy(update={"skills": list(skills)}, deep=True)
        )

    async def reconfigure_plugins(
        self, plugins: Sequence[RustPluginContextDefinition]
    ) -> None:
        async with self._lock:
            self._guard_open()
            updated = self._config.model_copy(
                update={"plugins": list(plugins)}, deep=True
            )
            if updated == self._config:
                return
            self._config = updated

    async def reconfigure_capability_dimension(
        self, update: Callable[[RustHarnessCapabilitySet], RustHarnessCapabilitySet]
    ) -> None:
        async with self._lock:
            self._guard_open()
            await self._flush_pending_capability_command()
            live = self._config.capabilities
            merged = update(live)
            if merged == live:
                return
            await self._reconfigure_capabilities_locked(
                merged, _capability_revision(merged)
            )

    async def reconfigure_settings(self, settings: RustHarnessSettings) -> None:
        params = _settings_reconfigure_params(settings)
        async with self._lock:
            self._guard_open()
            pending = await self._flush_pending_settings_command()
            # A caller reconfiguring now outranks the set a restore deferred:
            # without this, a resume that stays mid-turn would reinstate the
            # restore-time settings over this one the next time Core goes idle.
            self._post_recovery_settings = None
            if pending is not None and pending.params == params:
                return
            await self._command_without_actions_locked(
                client_command_id=self._store.resolve_internal_command_id("settings"),
                method=_SETTINGS_RECONFIGURE_METHOD,
                params=params,
                command=_settings_reconfigure_command(params),
                response_factory=lambda _transition: {},
                compact=False,
            )
            await self._accept_settings_config(params)

    async def record_model_change(self, model: str) -> None:
        """Record a move to a different model. The Session decides it is one.

        Under the same lock as every other projection mutation: a concurrent
        update would otherwise race the watermark and drop the entry.
        """
        async with self._lock:
            self._guard_open()
            update = self._projector.apply_model_change(
                model, observed_at=time.time_ns() // 1_000_000
            )
            await self._record_projection_update(update)

    async def compact_context(
        self, *, client_command_id: str, instructions: str
    ) -> ContextCompactionResult:
        result = await self.command(
            client_command_id=client_command_id,
            method="app_server/session/compact",
            params={"instructions": instructions},
            command=RustCompactEvent(extra_instructions=instructions),
            response_factory=_context_compaction_response,
        )
        return _parse_context_compaction_response(result.response)

    async def replace_quiescent_context(
        self, *, checkpoint: dict[str, JsonValue], projection: PublicSessionState
    ) -> None:
        async with self._lock:
            self._guard_open()
            await self._catch_up_projection()
            stored = self._store.load()
            if not stored.runtime_state.quiescent or stored.journal:
                raise RuntimeError(
                    "cannot replace context while Runtime work is pending"
                )
            sequence = stored.runtime_state.snapshot_sequence
            watermark = stored.projection_state.watermark + 1
            projection_state = ProjectionStateV1(
                session_id=self._store.session_id,
                snapshot_sequence=sequence,
                watermark=watermark,
                snapshot=projection,
            )
            self._store.write_generation(
                checkpoint=checkpoint,
                runtime_state=_runtime_state_for_replaced_context(
                    stored.runtime_state
                ).model_copy(
                    update={
                        "core_capabilities": self._config.capabilities,
                        "core_settings": self._config.settings,
                        "core_plugins": list(self._config.plugins),
                    }
                ),
                projection_state=projection_state,
                core_action_ids=self.pending_action_ids,
            )
            refreshed = self._store.load()
            replacement = _rebuilt_core(refreshed.restore_core(self._config), 0)
            previous = self._core
            self._core = replacement
            previous.close()
            self._projector = SessionProjector(refreshed.projection_state)
            self._replayed_transition = None
            self._replayed_core_inputs = ()
            self._model_input_resync_required = False
            if self._event_sink is not None:
                event = {
                    "type": "session_state_updated",
                    "eventId": watermark,
                    "sessionId": self._store.session_id,
                    "state": projection.model_dump(mode="json", by_alias=True),
                }
                emitted = self._event_sink(cast(JsonObject, event))
                if isinstance(emitted, Awaitable):
                    await emitted

    async def reconfigure_capabilities(
        self, capabilities: RustHarnessCapabilitySet, *, revision: str
    ) -> None:
        params = _capability_reconfigure_params(capabilities, revision)
        async with self._lock:
            self._guard_open()
            pending = await self._flush_pending_capability_command()
            self._post_recovery_capabilities = None
            if pending is not None and pending.params == params:
                return
            await self._reconfigure_capabilities_locked(capabilities, revision)

    async def _flush_pending_capability_command(self) -> PendingInternalCommand | None:
        return await self._flush_pending_internal_command(
            namespace="capabilities",
            method=_CAPABILITY_RECONFIGURE_METHOD,
            command=_capability_reconfigure_command,
            accept=self._accept_capability_config,
        )

    async def _flush_pending_settings_command(self) -> PendingInternalCommand | None:
        return await self._flush_pending_internal_command(
            namespace="settings",
            method=_SETTINGS_RECONFIGURE_METHOD,
            command=_settings_reconfigure_command,
            accept=self._accept_settings_config,
        )

    async def _flush_pending_internal_command(
        self,
        *,
        namespace: str,
        method: str,
        command: Callable[[dict[str, JsonValue]], RustEvent],
        accept: Callable[[dict[str, JsonValue]], Awaitable[None]],
    ) -> PendingInternalCommand | None:
        """Finish the namespace's interrupted operation, or abandon what cannot be.

        Only one shape is abandoned: a reservation an earlier build folded into
        a snapshot without the parameters to resume it. Left reserved it holds
        the store off quiescence forever, and raised over it fails every restore
        of the session, so those stores are healed on open and counted. Anything
        else reaching this point is a state that should not exist, and is raised
        over rather than papered across.
        """
        await self._abandon_orphaned_internal_commands(namespace)
        pending = self._store.pending_internal_command(namespace)
        if pending is None:
            return None
        if pending.method != method:
            raise HarnessInvalidSessionStoreError(
                self._store.session_id,
                f"pending {namespace} command has an unexpected method",
            )
        await self._command_without_actions_locked(
            client_command_id=pending.client_command_id,
            method=pending.method,
            params=pending.params,
            command=command(pending.params),
            response_factory=lambda _transition: {},
            compact=False,
        )
        await accept(pending.params)
        return pending

    async def _abandon_orphaned_internal_commands(self, namespace: str) -> None:
        """Settle reservations left unresumable by the build that wrote them.

        Counted, not just logged: the population this heals is closed, so the
        measure of whether it stays closed is that this stops firing.
        """
        for orphan in self._store.orphaned_internal_commands(namespace):
            await asyncio.to_thread(
                self._store.fail_command, orphan.client_command_id, orphan.reason
            )
            add_recovery_failure(
                failure_code="OrphanedInternalReservation", phase="replay"
            )
            logger.warning(
                "Unified session abandoned an unresumable internal command",
                extra={
                    "harness_backend": "unified",
                    "session_id": self._store.session_id,
                    "store_format": "mistral.vibe.unified-session-store/v1",
                    "client_command_id": orphan.client_command_id,
                    "abandon_reason": orphan.reason,
                },
            )

    async def _reconfigure_capabilities_locked(
        self, capabilities: RustHarnessCapabilitySet, revision: str
    ) -> None:
        params = _capability_reconfigure_params(capabilities, revision)
        await self._command_without_actions_locked(
            client_command_id=self._store.resolve_internal_command_id("capabilities"),
            method=_CAPABILITY_RECONFIGURE_METHOD,
            params=params,
            command=_capability_reconfigure_command(params),
            response_factory=lambda _transition: {},
            compact=False,
        )
        await self._accept_capability_config(params)

    async def guard_plugin_rewrite(self) -> PluginLockV1:
        """Reject a re-pin this session cannot accept, and report the lock in force.

        The conditions ``rewrite_plugins`` enforces when it applies, offered
        ahead of time: preparing a new set swaps the caller's live plugins, so
        a request refused only at apply time would already have replaced what
        it was refused from replacing. The lock returned is the one a caller
        re-binds should the apply half fail anyway.
        """
        async with self._lock:
            self._guard_open()
            self._reject_running_core()
            stored = await asyncio.to_thread(self._load_quiescent)
            return stored.runtime_state.plugin_lock

    async def rewrite_plugins(
        self,
        *,
        plugin_lock: PluginLockV1,
        config: RustHarnessConfig,
        subagents: ResolvedSubagentConfiguration | None = None,
    ) -> None:
        """Replace the pinned set of an idle session: lock and Core together.

        One generation carries both, so a reader can never see a Core built
        against plugins the lock does not name. Nothing durable is written
        until the replacement Core exists, which is what leaves a failed re-pin
        with the previous lock recorded.
        """
        async with self._lock:
            self._guard_open()
            self._reject_running_core()
            await asyncio.to_thread(self._rewrite_plugins_sync, plugin_lock, config)
        # After the durable write: the new Core advertises agent types filtered
        # against this table, so a controller holding the previous one would refuse
        # to spawn a type the model can now see.
        controller = self._subagent_controller
        if controller is not None and subagents is not None:
            await controller.rebind_agent_types(
                subagents.bindings, subagents.policy_ceiling
            )

    async def interrupt(
        self, *, expected_turn_id: str, reason: str | None = None
    ) -> RustSessionTransition:
        async with self._lock:
            self._guard_open()
            await self._catch_up_projection()
            pending_completion = any(
                isinstance(action, RustPendingCompletionAction)
                for action in self.inspection.pending_actions
            )
            transition = await self._apply(
                RustInterruptEvent(expected_turn_id=expected_turn_id, reason=reason)
            )
            if pending_completion:
                self._model_input_resync_required = True
            await self._close_cancelled_command_receipts()
            if not self.inspection.pending_actions:
                await self._compact_store()
        await self._reconcile_subagent_receipts()
        return transition

    async def _close_cancelled_command_receipts(self) -> None:
        """Answer the reservations of the commands an interrupt cancelled.

        A command reserves its receipt before it starts and resolves it once it
        is through, so the task an interrupt cancels leaves its reservation
        open. Nothing closes it afterwards: the caller is gone, and a client
        that re-sent the same command would only be told the work it asked to
        stop was already done -- which it is, by the interrupt. An open
        reservation, though, is the Session holding a command in flight, so it
        is never quiescent again: no checkpoint is exported, the journal never
        drains, and every later rewind, fork and compaction is refused for want
        of an export. The interrupt is the last word on those commands, so it
        is the interrupt that writes the outcome down.

        Only commands this Runtime cancelled are closed. A reservation left
        behind by a process that stopped, by contrast, is one a client may
        still re-send to have the turn picked up where it left off.

        The outcome recorded is the empty response every command driven this
        way answers with -- starting a turn is the only one there is.
        """
        cancelled = self._cancelled_commands
        self._cancelled_commands = set()
        if not cancelled:
            return
        stored = await asyncio.to_thread(self._store.load)
        for receipt in stored.runtime_state.command_receipts:
            if receipt.state == "reserved" and receipt.client_command_id in cancelled:
                await self._answer_command_locked(receipt.client_command_id, {})

    async def close(self) -> None:
        controller = self._subagent_controller
        self._subagent_controller = None
        if controller is not None:
            await controller.close()
        manager = self._process_manager
        if manager is not None:
            await manager.shutdown()
        try:
            await self._wait_for_terminal_updates()
        except Exception:
            self._ensure_terminal_retry(immediate=True)
        self._terminal_retry_wakeup.set()
        if self._terminal_retry_task is not None:
            await asyncio.shield(self._terminal_retry_task)
        async with self._lock:
            if self._closed:
                return
            await self._answer_abandoned_commands_locked()
            self._closed = True
            self._core.close()

    async def _resume_reserved_command(
        self, command: RustEvent
    ) -> RustSessionTransition:
        if self._replayed_transition is not None:
            return self._replayed_transition
        return await self._apply(command)

    async def _core_holds_input(
        self, client_command_id: str, command: RustEvent
    ) -> bool:
        """Whether the journal shows Core consumed the input this reservation made.

        A reconfigure is answered rather than applied only on this evidence, and
        the evidence has to belong to the reservation: an equal command applied
        earlier proves nothing, because a later configuration may have replaced
        it since. So the search runs over the inputs recorded after the
        reservation was made, and where that is depends on whether the fold has
        been past it. A reservation still in the journal names its own starting
        point. One the fold carried into the snapshot predates every record the
        journal now holds, which makes the whole journal its 'after'.

        The replayed transition is not evidence either: a reservation outlives
        whatever is applied after it, so answering from it settles the receipt
        with a transition that is not the command's, and the Runtime ends up
        configured for a capability set Core never received.
        """
        stored = await asyncio.to_thread(self._store.load)

        def reserved_here(record: JournalRecordV1) -> bool:
            return (
                isinstance(record, CommandReservedRecordV1)
                and record.payload.client_command_id == client_command_id
            )

        after_reservation = not any(reserved_here(record) for record in stored.journal)
        for record in stored.journal:
            if reserved_here(record):
                after_reservation = True
                continue
            if not after_reservation:
                continue
            if (
                isinstance(record, CoreInputRecordV1)
                and record.payload.input.command == command
            ):
                return True
        return False

    async def _command_without_actions_locked(
        self,
        *,
        client_command_id: str,
        method: str,
        params: dict[str, JsonValue],
        command: RustEvent,
        response_factory: ResponseFactory,
        compact: bool = True,
    ) -> DurableCommandResult:
        self._guard_open()
        await self._catch_up_projection()
        reservation = await asyncio.to_thread(
            self._store.reserve_command, client_command_id, method, params
        )
        if reservation.completed:
            return DurableCommandResult(
                response=reservation.response, transition=None, replayed_receipt=True
            )
        transition: RustSessionTransition | None = None
        if reservation.newly_reserved or not await self._core_holds_input(
            client_command_id, command
        ):
            transition = await self._apply(command)
            if transition.actions:
                raise RuntimeError("Core capability reconfiguration emitted actions")
        response = response_factory(transition) if transition is not None else {}
        await asyncio.to_thread(
            self._store.succeed_command, client_command_id, response
        )
        if compact and not self.inspection.pending_actions:
            await self._compact_store()
        return DurableCommandResult(response=response, transition=transition)

    async def _accept_capability_config(self, params: dict[str, JsonValue]) -> None:
        capabilities = RustHarnessCapabilitySet.model_validate(
            params.get("capabilities")
        )
        self._config = self._config.model_copy(
            update={"capabilities": capabilities}, deep=True
        )
        if not self.inspection.pending_actions:
            await self._compact_store()

    async def _settle_idle_locked(self) -> None:
        """Adopt the configuration a restore deferred, then fold the journal."""
        if self.inspection.pending_actions:
            return
        await self._restore_current_configuration_locked()
        await self._compact_store()

    async def _restore_current_configuration_locked(self) -> None:
        # Finishing the reserved command is what lets the store go quiescent again:
        # a reservation an interrupted reconfigure left behind holds compaction off
        # until some later caller flushes it, and for a session that reconfigures
        # nothing else that caller never comes. Both namespaces are flushed for the
        # same reason: a settings reservation holds the store open just as a
        # capability one does, and nothing else on a restore path answers it.
        # Flushing may itself install the deferred set, which
        # ``_take_deferred_capabilities`` then reports as done.
        await self._flush_pending_settings_command()
        await self._flush_pending_capability_command()
        capabilities = self._take_deferred_capabilities()
        if capabilities is not None:
            await self._reconfigure_capabilities_locked(
                capabilities, _capability_revision(capabilities)
            )
        await self._restore_current_settings_locked()

    async def _restore_current_settings_locked(self) -> None:
        """Adopt the settings a restore deferred to replay under the journalled set.

        Replay runs the Core under the settings its journal was written with, so
        the restored Core is still configured for the process that crashed. Core
        refuses a reconfigure mid-turn, which is why this waits for idle, exactly
        as the capability set does.
        """
        settings = self._post_recovery_settings
        self._post_recovery_settings = None
        if settings is None or settings == self._config.settings:
            return
        await self._flush_pending_settings_command()
        params = _settings_reconfigure_params(settings)
        await self._command_without_actions_locked(
            client_command_id=self._store.resolve_internal_command_id("settings"),
            method=_SETTINGS_RECONFIGURE_METHOD,
            params=params,
            command=_settings_reconfigure_command(params),
            response_factory=lambda _transition: {},
            compact=False,
        )
        await self._accept_settings_config(params)

    def _take_deferred_capabilities(self) -> RustHarnessCapabilitySet | None:
        """Claim the live capability set ``restore`` deferred, if Core still lacks it."""
        capabilities = self._post_recovery_capabilities
        self._post_recovery_capabilities = None
        if capabilities is None or capabilities == self._config.capabilities:
            return None
        return capabilities

    async def _accept_settings_config(self, params: dict[str, JsonValue]) -> None:
        settings = RustHarnessSettings.model_validate(params.get("settings"))
        self._config = self._config.model_copy(update={"settings": settings}, deep=True)
        if not self.inspection.pending_actions:
            await self._compact_store()

    async def _drive(
        self, transition: RustSessionTransition, *, recovering: bool
    ) -> RustSessionTransition:
        current = transition
        in_flight: dict[str, tuple[RustAction, asyncio.Task[RustEvent]]] = {}
        finished: asyncio.Queue[str] = asyncio.Queue()
        await self._reconcile_subagent_receipts()
        try:
            while True:
                recovering_transition = recovering
                await self._start_transition_actions(
                    current, in_flight, finished, recovering=recovering
                )
                recovering = False
                if not in_flight:
                    if recovering_transition and self.pending_action_ids:
                        raise RuntimeError(
                            "Core retained pending actions without durable Runtime work"
                        )
                    return current

                action_id = await finished.get()
                scheduled = in_flight.get(action_id)
                if scheduled is None:
                    continue
                action, task = scheduled
                event = await task
                async with self._lock:
                    self._guard_open()
                    pending_ids = {
                        pending.action_id for pending in self.inspection.pending_actions
                    }
                    if action.action_id not in pending_ids:
                        del in_flight[action_id]
                        continue
                    owner = (
                        action.action_id
                        if action.action_id in self._process_action_reservations
                        else None
                    )
                    with self._store.use_journal_reservation(owner):
                        current = await self._apply(event)
                    pending_ids = {
                        pending.action_id for pending in self.inspection.pending_actions
                    }
                await self._acknowledge_process_result_applied(action, event)
                await self._reconcile_subagent_receipts()
                if self._action_applied_sink is not None:
                    emitted = self._action_applied_sink(action, event)
                    if isinstance(emitted, Awaitable):
                        await emitted
                del in_flight[action_id]
                abandoned = [
                    in_flight.pop(pending_id)[1]
                    for pending_id in tuple(in_flight)
                    if pending_id not in pending_ids
                ]
                await _cancel_tasks(abandoned)
        except BaseException:
            await self._settle_actions_after_drive_failure(in_flight)
            raise

    async def _start_transition_actions(
        self,
        transition: RustSessionTransition,
        in_flight: dict[str, tuple[RustAction, asyncio.Task[RustEvent]]],
        finished: asyncio.Queue[str],
        *,
        recovering: bool,
    ) -> None:
        async with self._lock:
            self._guard_open()
            stored = self._store.load()
            pending_ids = {
                pending.action_id for pending in self.inspection.pending_actions
            }
            durable_actions = {
                action.action_id: action for action in stored.runtime_state.actions
            }
            candidates = [(action, recovering) for action in transition.actions]
            if recovering and isinstance(transition.next, RustActionsNextAction):
                scheduled_ids = {action.action_id for action, _ in candidates}
                for directive in transition.next.directives:
                    if not isinstance(directive, RustKeepActionDirective):
                        continue
                    if (
                        directive.action_id in in_flight
                        or directive.action_id in scheduled_ids
                    ):
                        continue
                    durable = durable_actions.get(directive.action_id)
                    if durable is None:
                        raise RuntimeError(
                            f"Core retained action {directive.action_id!r} without durable intent"
                        )
                    candidates.append((action_from_recovery_state(durable), True))

            for action, action_recovering in candidates:
                if action.action_id not in pending_ids or action.action_id in in_flight:
                    continue
                task = asyncio.create_task(
                    self._resolve_action(action, recovering=action_recovering),
                    name=f"harness-action:{action.action_id}",
                )
                task.add_done_callback(
                    lambda _task, completed_id=action.action_id: finished.put_nowait(
                        completed_id
                    )
                )
                in_flight[action.action_id] = (action, task)

    async def _reconcile_subagent_receipts(self) -> None:
        controller = self._subagent_controller
        if controller is None:
            return
        await controller.reconcile_actions(self.pending_action_ids)

    async def _settle_actions_after_drive_failure(
        self, tasks: dict[str, tuple[RustAction, asyncio.Task[RustEvent]]]
    ) -> None:
        admitted: list[tuple[RustAction, asyncio.Task[RustEvent]]] = []
        async with self._lock:
            stored = await asyncio.to_thread(self._store.load)
            durable_actions = {
                item.action_id: item for item in stored.runtime_state.actions
            }
            for action_id, pair in tasks.items():
                durable = durable_actions.get(action_id)
                if durable is not None and durable.process_request_sha256 is not None:
                    admitted.append(pair)
                else:
                    pair[1].cancel()
        cancelled = [
            task
            for _, task in tasks.values()
            if all(task is not item[1] for item in admitted)
        ]
        if cancelled:
            await asyncio.gather(*cancelled, return_exceptions=True)
        for action, task in admitted:
            event = await asyncio.shield(task)
            async with self._lock:
                pending_ids = {
                    item.action_id for item in self.inspection.pending_actions
                }
                if action.action_id not in pending_ids:
                    continue
                owner = (
                    action.action_id
                    if action.action_id in self._process_action_reservations
                    else None
                )
                with self._store.use_journal_reservation(owner):
                    await self._apply(event)
            await self._acknowledge_process_result_applied(action, event)
            if self._action_applied_sink is not None:
                emitted = self._action_applied_sink(action, event)
                if isinstance(emitted, Awaitable):
                    await emitted

    async def _resolve_action(  # noqa: PLR0912, PLR0914, PLR0915 - one branch per action kind
        self, action: RustAction, *, recovering: bool
    ) -> RustEvent:
        capacity_failure: RustEvent | None = None
        async with self._lock:  # noqa: PLR1702 - resolution runs under one lock scope
            self._guard_open()
            stored = await asyncio.to_thread(self._store.load)
            durable = next(
                (
                    candidate
                    for candidate in stored.runtime_state.actions
                    if candidate.action_id == action.action_id
                ),
                None,
            )
            tool_action = (
                action
                if isinstance(
                    action,
                    RustRuntimeBuiltinToolCallAction | RustProvidedToolCallAction,
                )
                else None
            )
            compaction_action = (
                action
                if isinstance(action, RustLLMCallAction)
                and action.purpose == "compaction"
                else None
            )
            if durable is not None and durable.state in {"succeeded", "failed"}:
                if durable.result_pruned:
                    # Unreachable by construction: a result is only dropped for an
                    # Action Core no longer holds, and Core only redelivers ones it
                    # does. Say so plainly rather than validating None, so that if the
                    # reasoning is ever wrong the session log names the cause.
                    raise RuntimeError(
                        f"Action {durable.action_id} was redelivered after its result was pruned"
                    )
                if tool_action is not None:
                    await self._ensure_action_started(tool_action)
                elif compaction_action is not None:
                    await self._ensure_action_started(compaction_action)
                return _EVENT_ADAPTER.validate_python(durable.result)
            if (
                durable is None
                and isinstance(action, RustLLMCallAction)
                and self._model_input_resync_required
            ):
                action = await self._resynchronize_completion_action(action)
                self._model_input_resync_required = False
                compaction_action = action if action.purpose == "compaction" else None
            if durable is None:
                request = cast(
                    JsonValue,
                    action.model_dump(mode="json", by_alias=True, exclude_none=True),
                )
                if isinstance(
                    action, RustRuntimeBuiltinToolCallAction
                ) and action.call.name.startswith("process."):
                    config = self._process_config
                    if config is not None and self._process_manager is not None:
                        try:
                            validate_process_action(
                                action, session_id=self._store.session_id, config=config
                            )
                        except ProcessActionError:
                            pass
                        else:
                            denied_start = (
                                action.call.name == "process.start"
                                and config.tool_modes.get("process.start", "allow")
                                == "deny"
                            )
                            if not denied_start:
                                try:
                                    await self._reserve_process_action_storage(
                                        action, stored
                                    )
                                except HarnessStoreCapacityError:
                                    capacity_failure = _process_persistence_failure(
                                        action, self._store.session_id
                                    )
                owner = (
                    action.action_id
                    if action.action_id in self._process_action_reservations
                    else None
                )
                with self._store.use_journal_reservation(owner):
                    await asyncio.to_thread(
                        self._store.record_action_intent,
                        action_id=action.action_id,
                        kind=_action_kind(action),
                        request=request,
                        recovery_mode=_recovery_mode(action),
                        lease_id=action.action_id,
                    )
                    if tool_action is not None:
                        await self._ensure_action_started(tool_action)
                    elif compaction_action is not None:
                        await self._ensure_action_started(compaction_action)
                durable = next(
                    candidate
                    for candidate in (
                        await asyncio.to_thread(self._store.load)
                    ).runtime_state.actions
                    if candidate.action_id == action.action_id
                )
                recovering = False
                if capacity_failure is not None:
                    value = cast(
                        JsonValue,
                        capacity_failure.model_dump(
                            mode="json", by_alias=True, exclude_none=True
                        ),
                    )
                    await asyncio.to_thread(
                        self._store.record_action_result,
                        action.action_id,
                        value,
                        failed=True,
                    )
            elif tool_action is not None:
                await self._ensure_action_started(tool_action)
        if capacity_failure is not None:
            return capacity_failure
        reconcile_started = time.perf_counter() if recovering else None
        try:
            controller = self._subagent_controller
            if (
                controller is not None
                and isinstance(action, RustRuntimeBuiltinToolCallAction)
                and controller.handles(action)
            ):
                event = await controller.execute(action, recovering=recovering)
            elif isinstance(
                action, RustRuntimeBuiltinToolCallAction
            ) and action.call.name.startswith("process."):
                with self._store.use_journal_reservation(action.action_id):
                    event = await self._resolve_process_action(
                        durable, action, recovering=recovering
                    )
            elif recovering:
                event = await self._recover(durable, action)
            else:
                event = await self._execute_action(action)
        except asyncio.CancelledError:
            event = _failed_action_event(action, "Action cancelled")
            await self._record_action_result(
                action, durable, event, reconcile_started=reconcile_started, failed=True
            )
            raise
        except Exception as exc:
            event = _failed_action_event(action, str(exc))
        failed = isinstance(
            event,
            RustCompletionFailedEvent
            | RustFailTurnEvent
            | RustHookFailedEvent
            | RustToolFailedEvent
            | RustFilesystemFailedEvent,
        )
        try:
            await self._record_action_result(
                action,
                durable,
                event,
                reconcile_started=reconcile_started,
                failed=failed,
            )
        except Exception:
            if action.action_id not in self._process_action_reservations:
                raise
            await self._retry_process_action_result(
                action, durable, event, failed=failed
            )
        return event

    async def _record_action_result(
        self,
        action: RustAction,
        durable: RuntimeActionV1,
        event: RustEvent,
        *,
        reconcile_started: float | None,
        failed: bool,
    ) -> None:
        if reconcile_started is not None:
            record_effect_reconciliation(
                time.perf_counter() - reconcile_started,
                kind=durable.kind,
                outcome="failure" if failed else "success",
            )
        value = cast(
            JsonValue, event.model_dump(mode="json", by_alias=True, exclude_none=True)
        )
        async with self._lock:
            self._guard_open()
            owner = (
                action.action_id
                if action.action_id in self._process_action_reservations
                else None
            )
            with self._store.use_journal_reservation(owner):
                await asyncio.to_thread(
                    self._store.record_action_result,
                    action.action_id,
                    value,
                    failed=failed,
                )
            completed = next(
                item
                for item in (
                    await asyncio.to_thread(self._store.load)
                ).runtime_state.actions
                if item.action_id == action.action_id
            )
        if (
            self._process_manager is not None
            and completed.process_request_sha256 is not None
        ):
            self._process_manager.acknowledge(
                action.action_id, completed.process_request_sha256
            )

    async def _resolve_process_action(  # noqa: PLR0911 - one return per process action
        self,
        durable: RuntimeActionV1,
        action: RustRuntimeBuiltinToolCallAction,
        *,
        recovering: bool,
    ) -> RustEvent:
        config = self._process_config
        manager = self._process_manager
        if config is None or manager is None:
            return process_failed(
                action,
                process_error(
                    "tool_denied",
                    "Tool execution denied by approval policy",
                    {"tool": action.call.name},
                ),
            )
        try:
            request = validate_process_action(
                action, session_id=self._store.session_id, config=config
            )
        except ProcessActionError as error:
            return process_failed(action, error.error)
        if config.process_authority != "host_shell":
            return process_failed(
                action,
                process_error(
                    "tool_denied",
                    "Tool execution denied by approval policy",
                    {"tool": action.call.name},
                ),
            )
        if recovering:
            recovered = await self._recover_process_action(durable, action, request)
            if recovered is not None:
                return recovered
        try:
            if isinstance(request, ValidatedProcessStart):
                return await self._start_process(
                    durable, action, request, config, manager
                )
            if isinstance(request, ValidatedProcessOutput):
                return await self._read_process_output(action, request, manager)
            if isinstance(request, ValidatedProcessWrite):
                return await self._write_process(durable, action, request, manager)
            if isinstance(request, ValidatedProcessList):
                return await self._list_processes(action)
            return await self._stop_process(durable, action, request, manager)
        except ProcessActionError as error:
            return process_failed(action, error.error)
        except ProcessManagerError as error:
            return process_failed(action, manager_error(error))

    async def _start_process(
        self,
        durable: RuntimeActionV1,
        action: RustRuntimeBuiltinToolCallAction,
        request: ValidatedProcessStart,
        config: LocalRuntimeAdapterConfig,
        manager: SessionProcessManager,
    ) -> RustEvent:
        prepared = durable.prepared_process_start
        if prepared is None:
            denial = await self._approve_process_start(action, config)
            if denial is not None:
                return process_failed(action, denial)
            created_at = _timestamp()
            prepared = PreparedProcessStartV1(
                process_id=request.process_id,
                start_action_id=action.action_id,
                start_call_id=action.call_id,
                manager_instance_id=manager.manager_instance_id,
                command=request.command,
                cwd=str(request.cwd),
                command_environment=cast(
                    Literal["unix", "git_bash", "powershell"],
                    config.command_environment,
                ),
                created_at=created_at,
            )
        start_request = resolve_process_start(
            request,
            backend=manager.backend,
            configured_shell=config.shell,
            created_at=prepared.created_at,
        )
        self._start_barrier(request.process_id)
        digest = process_request_sha256(request)
        durable = await self._dispatch_process_operation(
            durable,
            action,
            process_id=request.process_id,
            digest=digest,
            prepared=prepared,
            manager=manager,
        )
        try:
            started = await manager.start(action.action_id, digest, start_request)
        except ProcessManagerError as error:
            if error.details.get("stage") == "post_spawn":
                snapshot = manager.terminal_snapshot(request.process_id)
                if snapshot is not None:
                    await self._record_started_process(
                        prepared,
                        snapshot,
                        start_outcome="failed",
                        failure_stage="post_spawn",
                    )
            raise
        running = ManagedProcessV1(
            process_id=prepared.process_id,
            start_action_id=prepared.start_action_id,
            start_call_id=prepared.start_call_id,
            manager_instance_id=prepared.manager_instance_id,
            command=prepared.command,
            cwd=prepared.cwd,
            command_environment=prepared.command_environment,
            pty_backend=started.pty_backend,
            start_outcome="accepted",
            start_failure_stage=None,
            status="running",
            exit_code=None,
            created_at=prepared.created_at,
            started_at=started.started_at,
            finished_at=None,
        )
        try:
            existing = await self._find_process(request.process_id)
            if existing is None:
                async with self._lock:
                    self._guard_open()
                    with self._store.use_journal_reservation(request.process_id):
                        await asyncio.to_thread(
                            self._store.record_process_state, running
                        )
        except Exception:
            if await self._find_process(request.process_id) == running:
                return process_succeeded(
                    action, {"processId": request.process_id, "status": "running"}
                )
            snapshot = await manager.rollback_start(request.process_id)
            await self._record_started_process(
                prepared, snapshot, start_outcome="failed", failure_stage="persistence"
            )
            raise ProcessActionError(
                process_error(
                    "process_start_failed",
                    "Background process could not be started",
                    {"processId": request.process_id, "stage": "persistence"},
                )
            ) from None
        return process_succeeded(
            action, {"processId": request.process_id, "status": "running"}
        )

    async def _write_process(
        self,
        durable: RuntimeActionV1,
        action: RustRuntimeBuiltinToolCallAction,
        request: ValidatedProcessWrite,
        manager: SessionProcessManager,
    ) -> RustEvent:
        process = await self._require_process(request.process_id)
        if process.status != "running":
            raise ProcessActionError(_process_not_running(process))
        digest = process_request_sha256(request)
        await self._dispatch_process_operation(
            durable,
            action,
            process_id=request.process_id,
            digest=digest,
            prepared=None,
            manager=manager,
        )
        result = await manager.write(
            action.action_id, digest, request.process_id, request.data
        )
        return process_succeeded(
            action,
            {
                "processId": result.process_id,
                "status": result.status,
                "bytesWritten": result.bytes_written,
            },
        )

    async def _stop_process(
        self,
        durable: RuntimeActionV1,
        action: RustRuntimeBuiltinToolCallAction,
        request: ValidatedProcessStop,
        manager: SessionProcessManager,
    ) -> RustEvent:
        process = await self._require_process(request.process_id)
        if process.status != "running":
            return process_succeeded(action, _stop_result(process))
        digest = process_request_sha256(request)
        await self._dispatch_process_operation(
            durable,
            action,
            process_id=request.process_id,
            digest=digest,
            prepared=None,
            manager=manager,
        )
        await manager.stop(action.action_id, digest, request.process_id)
        snapshot = manager.terminal_snapshot(request.process_id)
        if snapshot is not None:
            self.submit_terminal_snapshot(snapshot)
        try:
            await self._wait_for_terminal_updates(request.process_id)
        except Exception as error:
            raise ProcessActionError(
                process_error(
                    "process_io_failed",
                    "Background process I/O failed",
                    {
                        "processId": request.process_id,
                        "operation": "stop",
                        "stage": "terminal_persistence",
                    },
                )
            ) from error
        terminal = await self._require_process(request.process_id)
        return process_succeeded(action, _stop_result(terminal))

    async def _read_process_output(
        self,
        action: RustRuntimeBuiltinToolCallAction,
        request: ValidatedProcessOutput,
        manager: SessionProcessManager,
    ) -> RustEvent:
        process = await self._require_process(request.process_id)
        try:
            if process.status == "running" and manager.has_live_process(
                request.process_id
            ):
                page, _, _ = await manager.output(
                    request.process_id,
                    from_end=request.from_end,
                    cursor=request.cursor,
                    wait_ms=request.wait_ms,
                    max_bytes=request.max_bytes,
                )
            else:
                output = await asyncio.to_thread(
                    ProcessOutputStore.recover,
                    self._store.session_root,
                    request.process_id,
                )
                page = await asyncio.to_thread(
                    output.read,
                    from_end=request.from_end,
                    cursor=request.cursor,
                    max_bytes=request.max_bytes,
                )
        except OutputUnavailableError as error:
            raise ProcessActionError(
                process_error(
                    "process_output_unavailable",
                    "Background process output is unavailable",
                    {"processId": request.process_id},
                )
            ) from error
        except InvalidCursorError as error:
            raise ProcessActionError(
                process_error(
                    "invalid_cursor",
                    "Cursor is outside retained process output",
                    {
                        "processId": request.process_id,
                        "cursor": error.cursor,
                        "outputStartCursor": error.output_start_cursor,
                        "bytesAvailable": error.bytes_available,
                    },
                )
            ) from error
        except OSError as error:
            raise ProcessActionError(
                process_error(
                    "process_io_failed",
                    "Background process I/O failed",
                    {
                        "processId": request.process_id,
                        "operation": "output",
                        "stage": "read",
                    },
                )
            ) from error
        try:
            await self._wait_for_terminal_updates(request.process_id)
        except Exception as error:
            raise ProcessActionError(
                process_error(
                    "process_io_failed",
                    "Background process I/O failed",
                    {
                        "processId": request.process_id,
                        "operation": "output",
                        "stage": "terminal_persistence",
                    },
                )
            ) from error
        process = await self._require_process(request.process_id)
        return process_succeeded(
            action,
            {
                "processId": request.process_id,
                "status": process.status,
                "exitCode": process.exit_code,
                "output": page.output.decode("utf-8", errors="replace"),
                "outputStartCursor": page.output_start_cursor,
                "nextCursor": page.next_cursor,
                "bytesAvailable": page.bytes_available,
                "hasMore": page.has_more,
                "truncatedBefore": page.truncated_before,
            },
        )

    async def _list_processes(
        self, action: RustRuntimeBuiltinToolCallAction
    ) -> RustEvent:
        try:
            await self._wait_for_terminal_updates()
        except Exception as error:
            raise ProcessActionError(
                process_error(
                    "process_io_failed",
                    "Background process I/O failed",
                    {"operation": "list", "stage": "terminal_persistence"},
                )
            ) from error
        async with self._lock:
            self._guard_open()
            processes = sorted(
                (await asyncio.to_thread(self._store.load)).runtime_state.processes,
                key=lambda process: (process.created_at, process.process_id),
            )
        return process_succeeded(
            action,
            {
                "processes": cast(
                    JsonValue,
                    [
                        {
                            "processId": process.process_id,
                            "command": process.command,
                            "status": process.status,
                            "exitCode": process.exit_code,
                            "outputPath": None,
                        }
                        for process in processes
                    ],
                )
            },
        )

    async def _approve_process_start(
        self,
        action: RustRuntimeBuiltinToolCallAction,
        config: LocalRuntimeAdapterConfig,
    ) -> RustProtocolError | None:
        mode = config.tool_modes.get("process.start", "allow")
        if mode == "ask" and config.bypass_approval:
            mode = "allow"
        if mode == "deny":
            return process_error(
                "tool_denied",
                "Tool execution denied by approval policy",
                {"tool": "process.start"},
            )
        if mode == "allow":
            return None
        if self._request_process_approval is None:
            return process_error(
                "approval_required",
                "Approval callbacks are not implemented yet",
                {"tool": "process.start"},
            )
        try:
            approved = await self._request_process_approval(action)
        except Exception:
            return process_error(
                "tool_denied",
                "Tool execution denied because approval failed",
                {"tool": "process.start", "reason": "callback_failed"},
            )
        if approved:
            return None
        return process_error(
            "tool_denied",
            "Tool execution denied by approval callback",
            {"tool": "process.start"},
        )

    def _start_barrier(
        self, process_identifier: str
    ) -> asyncio.Future[Literal["accepted", "failed"]]:
        barrier = self._process_start_barriers.get(process_identifier)
        if barrier is None:
            barrier = asyncio.get_running_loop().create_future()
            self._process_start_barriers[process_identifier] = barrier
        return barrier

    async def _dispatch_process_operation(
        self,
        durable: RuntimeActionV1,
        action: RustRuntimeBuiltinToolCallAction,
        *,
        process_id: str,
        digest: str,
        prepared: PreparedProcessStartV1 | None,
        manager: SessionProcessManager,
    ) -> RuntimeActionV1:
        if durable.process_manager_instance_id is not None:
            if (
                durable.process_manager_instance_id != manager.manager_instance_id
                or durable.process_request_sha256 != digest
                or durable.prepared_process_start != prepared
            ):
                raise ProcessActionError(
                    process_error(
                        "process_identity_conflict",
                        "Process operation identity conflicts with an earlier request",
                        {"actionId": action.action_id, "processId": process_id},
                    )
                )
            return durable
        async with self._lock:
            self._guard_open()
            try:
                await asyncio.to_thread(
                    self._store.record_process_operation_dispatched,
                    action_id=action.action_id,
                    manager_instance_id=manager.manager_instance_id,
                    request_sha256=digest,
                    prepared_start=prepared,
                )
            except Exception:
                stored = await asyncio.to_thread(self._store.load)
                persisted = next(
                    (
                        item
                        for item in stored.runtime_state.actions
                        if item.action_id == action.action_id
                    ),
                    None,
                )
                if (
                    persisted is None
                    or persisted.process_manager_instance_id
                    != manager.manager_instance_id
                    or persisted.process_request_sha256 != digest
                    or persisted.prepared_process_start != prepared
                ):
                    raise
                return persisted
            return next(
                item
                for item in (
                    await asyncio.to_thread(self._store.load)
                ).runtime_state.actions
                if item.action_id == action.action_id
            )

    async def _record_started_process(
        self,
        prepared: PreparedProcessStartV1,
        snapshot: ProcessTerminalSnapshot,
        *,
        start_outcome: Literal["accepted", "failed"],
        failure_stage: Literal["post_spawn", "persistence", "recovery"] | None,
    ) -> ManagedProcessV1:
        process = ManagedProcessV1(
            process_id=prepared.process_id,
            start_action_id=prepared.start_action_id,
            start_call_id=prepared.start_call_id,
            manager_instance_id=prepared.manager_instance_id,
            command=prepared.command,
            cwd=prepared.cwd,
            command_environment=prepared.command_environment,
            pty_backend=snapshot.pty_backend,
            start_outcome=start_outcome,
            start_failure_stage=failure_stage,
            status=snapshot.status,
            exit_code=snapshot.exit_code,
            created_at=prepared.created_at,
            started_at=snapshot.started_at,
            finished_at=snapshot.finished_at,
        )
        async with self._lock:
            self._guard_open()
            with self._store.use_journal_reservation(process.process_id):
                await asyncio.to_thread(self._store.record_process_state, process)
        self._log_terminal_process(process)
        return process

    async def _require_process(self, process_id: str) -> ManagedProcessV1:
        process = await self._find_process(process_id)
        if process is None:
            raise ProcessActionError(
                process_error(
                    "unknown_process",
                    "Unknown background process",
                    {"processId": process_id},
                )
            )
        return process

    async def _recover_process_action(  # noqa: PLR0911 - one return per recovery outcome
        self,
        durable: RuntimeActionV1,
        action: RustRuntimeBuiltinToolCallAction,
        request: ValidatedProcessStart
        | ValidatedProcessOutput
        | ValidatedProcessWrite
        | ValidatedProcessList
        | ValidatedProcessStop,
    ) -> RustEvent | None:
        if isinstance(request, ValidatedProcessOutput | ValidatedProcessList):
            return None
        digest = process_request_sha256(cast(MutatingProcessAction, request))
        manager = self._process_manager
        if durable.process_request_sha256 is not None:
            if durable.process_request_sha256 != digest:
                return process_failed(
                    action, _process_identity_conflict(action, request)
                )
            if manager is not None and (
                durable.process_manager_instance_id == manager.manager_instance_id
            ):
                return None
        process = await self._find_process(request.process_id)
        if process is not None and process.status == "running":
            process = await self._orphan_process(process)
        if isinstance(request, ValidatedProcessStart):
            if durable.process_request_sha256 is None:
                return process_failed(
                    action,
                    _effect_recovery_error(
                        action,
                        request.process_id,
                        "start",
                        "not_started",
                        None,
                        "Background process start was interrupted before dispatch",
                    ),
                )
            if process is None:
                prepared = durable.prepared_process_start
                if prepared is None:
                    return process_failed(
                        action, _process_identity_conflict(action, request)
                    )
                process = await self._record_recovered_start(prepared)
            if process.start_outcome == "accepted":
                return process_succeeded(
                    action, {"processId": process.process_id, "status": "running"}
                )
            if process.start_failure_stage in {"post_spawn", "persistence"}:
                return process_failed(
                    action,
                    process_error(
                        "process_start_failed",
                        "Background process could not be started",
                        {
                            "processId": process.process_id,
                            "stage": process.start_failure_stage,
                        },
                    ),
                )
            return process_failed(
                action,
                _effect_recovery_error(
                    action,
                    request.process_id,
                    "start",
                    "orphaned",
                    None,
                    "Background process start cannot be recovered after restart",
                ),
            )
        if process is None:
            return process_failed(
                action,
                process_error(
                    "unknown_process",
                    "Unknown background process",
                    {"processId": request.process_id},
                ),
            )
        if durable.process_request_sha256 is None:
            if isinstance(request, ValidatedProcessWrite):
                return process_failed(action, _process_not_running(process))
            return process_succeeded(action, _stop_result(process))
        operation = "write" if isinstance(request, ValidatedProcessWrite) else "stop"
        if operation == "stop":
            return process_succeeded(action, _stop_result(process))
        return process_failed(
            action,
            _effect_recovery_error(
                action,
                process.process_id,
                operation,
                process.status,
                process.exit_code,
                "Background process write cannot be recovered after restart",
            ),
        )

    async def _find_process(self, process_id: str) -> ManagedProcessV1 | None:
        async with self._lock:
            self._guard_open()
            stored = await asyncio.to_thread(self._store.load)
        return next(
            (
                item
                for item in stored.runtime_state.processes
                if item.process_id == process_id
            ),
            None,
        )

    async def _orphan_process(self, process: ManagedProcessV1) -> ManagedProcessV1:
        await asyncio.to_thread(
            ProcessOutputStore.ensure_unavailable,
            self._store.session_root,
            process.process_id,
        )
        orphaned = process.model_copy(
            update={
                "status": "orphaned",
                "exit_code": None,
                "finished_at": _timestamp(),
            }
        )
        async with self._lock:
            self._guard_open()
            with self._store.use_journal_reservation(orphaned.process_id):
                await asyncio.to_thread(self._store.record_process_state, orphaned)
        self._log_terminal_process(orphaned)
        return orphaned

    async def _record_recovered_start(
        self, prepared: PreparedProcessStartV1
    ) -> ManagedProcessV1:
        await asyncio.to_thread(
            ProcessOutputStore.ensure_unavailable,
            self._store.session_root,
            prepared.process_id,
        )
        process = ManagedProcessV1(
            process_id=prepared.process_id,
            start_action_id=prepared.start_action_id,
            start_call_id=prepared.start_call_id,
            manager_instance_id=prepared.manager_instance_id,
            command=prepared.command,
            cwd=prepared.cwd,
            command_environment=prepared.command_environment,
            pty_backend=None,
            start_outcome="failed",
            start_failure_stage="recovery",
            status="orphaned",
            exit_code=None,
            created_at=prepared.created_at,
            started_at=None,
            finished_at=_timestamp(prepared.created_at),
        )
        async with self._lock:
            self._guard_open()
            with self._store.use_journal_reservation(process.process_id):
                await asyncio.to_thread(self._store.record_process_state, process)
        self._log_terminal_process(process)
        # Recording the process is not enough: until its terminal notification is
        # submitted, _record_process_notification_submitted never pops the lifetime
        # reservation, has_active_process_work stays true and the Session can never
        # be idle-evicted again. _recover_processes_after_restart submits for the
        # records it reconciles; this path has to do the same for the ones it writes.
        self.submit_terminal_snapshot(
            ProcessTerminalSnapshot(
                process_id=process.process_id,
                pty_backend=cast(PtyBackend, process.pty_backend),
                started_at=cast(str, process.started_at),
                finished_at=cast(str, process.finished_at),
                # Mirrors the record above; _commit_terminal_snapshot rejects a
                # snapshot whose terminal fields disagree with the stored process.
                status="orphaned",
                exit_code=process.exit_code,
                output_available=False,
            )
        )
        return process

    async def _commit_terminal_snapshot(
        self, snapshot: ProcessTerminalSnapshot
    ) -> None:
        await self._start_barrier(snapshot.process_id)
        if not snapshot.output_available:
            await asyncio.to_thread(
                ProcessOutputStore.ensure_unavailable,
                self._store.session_root,
                snapshot.process_id,
            )
        process = await self._find_process(snapshot.process_id)
        stored_terminal = False
        if process is None:
            async with self._lock:
                stored = await asyncio.to_thread(self._store.load)
                durable = next(
                    (
                        item
                        for item in stored.runtime_state.actions
                        if item.prepared_process_start is not None
                        and item.prepared_process_start.process_id
                        == snapshot.process_id
                    ),
                    None,
                )
            if durable is None or durable.prepared_process_start is None:
                raise RuntimeError("terminal snapshot has no durable process start")
            process = await self._record_started_process(
                durable.prepared_process_start,
                snapshot,
                start_outcome="accepted",
                failure_stage=None,
            )
            stored_terminal = True
        elif process.status == "running":
            process = process.model_copy(
                update={
                    "status": snapshot.status,
                    "exit_code": snapshot.exit_code,
                    "finished_at": snapshot.finished_at,
                }
            )
            async with self._lock:
                self._guard_open()
                with self._store.use_journal_reservation(process.process_id):
                    await asyncio.to_thread(self._store.record_process_state, process)
            stored_terminal = True
        elif (
            process.status != snapshot.status
            or process.exit_code != snapshot.exit_code
            or process.finished_at != snapshot.finished_at
        ):
            raise RuntimeError("conflicting terminal process snapshot")
        if stored_terminal:
            self._log_terminal_process(process)
        await self._submit_terminal_process(process)

    def _log_terminal_process(self, process: ManagedProcessV1) -> None:
        if process.process_id in self._logged_terminal_processes:
            return
        self._logged_terminal_processes.add(process.process_id)
        if process.status == "orphaned":
            process_logger.warning("background_process.orphaned")
        else:
            process_logger.info("background_process.terminal")

    async def _submit_terminal_process(self, process: ManagedProcessV1) -> None:
        await self._start_barrier(process.process_id)
        notification = build_terminal_notification(process)
        async with self._lock:
            self._guard_open()
            stored = await asyncio.to_thread(self._store.load)
            if any(
                item.process_id == process.process_id
                for item in stored.runtime_state.submitted_process_notifications
            ):
                return
            with self._store.use_journal_reservation(process.process_id):
                await self._apply(RustNotificationEvent(notification=notification))
                await self._record_process_notification_submitted(
                    process.process_id, notification.id
                )

    async def _record_process_notification_submitted(
        self, process_id: str, notification_id: str
    ) -> None:
        with self._store.use_journal_reservation(process_id):
            await asyncio.to_thread(
                self._store.record_process_notification_submitted,
                process_id,
                notification_id,
            )
        self._process_lifetime_reservations.pop(process_id, None)
        self._store.release_journal_capacity(process_id)
        pending = self._pending_process_core_input
        if (
            pending is not None
            and not pending.wait_for_completion
            and pending.reservation_owner == process_id
            and isinstance(pending.command, RustNotificationEvent)
            and pending.command.notification.id == notification_id
        ):
            self._pending_process_core_input = None

    async def _recover_processes_after_restart(self) -> None:
        if self._process_recovery_initialized:
            return
        self._process_recovery_initialized = True
        async with self._lock:
            self._guard_open()
            stored = await asyncio.to_thread(self._store.load)
            pending_action_ids = {
                action.action_id for action in self.inspection.pending_actions
            }
        acknowledged = {
            item.process_id
            for item in stored.runtime_state.submitted_process_notifications
        }
        manager = self._process_manager
        for process in stored.runtime_state.processes:
            barrier = self._start_barrier(process.process_id)
            if process.start_action_id not in pending_action_ids and not barrier.done():
                barrier.set_result("accepted")
            if process.status == "running" and (
                manager is None
                or process.manager_instance_id != manager.manager_instance_id
                or not manager.has_live_process(process.process_id)
            ):
                process = await self._orphan_process(process)
            if process.status == "running" or process.process_id in acknowledged:
                continue
            self.submit_terminal_snapshot(
                ProcessTerminalSnapshot(
                    process_id=process.process_id,
                    pty_backend=cast(PtyBackend, process.pty_backend),
                    started_at=cast(str, process.started_at),
                    finished_at=cast(str, process.finished_at),
                    status=process.status,
                    exit_code=process.exit_code,
                    output_available=True,
                )
            )

    async def _close_restarted_approval_callbacks(self) -> None:
        async with self._lock:
            self._guard_open()
            stored = await asyncio.to_thread(self._store.load)
            pending = [
                callback
                for callback in stored.runtime_state.callbacks
                if callback.kind == "approval" and callback.state == "pending"
            ]
            for callback in pending:
                await asyncio.to_thread(
                    self._store.resolve_callback,
                    callback.callback_id,
                    {
                        "code": "callback_closed",
                        "message": "Approval request closed after App Server restart",
                    },
                    failed=True,
                )

    def _terminal_task_done(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        if task.exception() is not None:
            logger.warning("background_process.operation_failed")
            self._ensure_terminal_retry(immediate=False)
        self._notify_work_state_changed()

    def _ensure_terminal_retry(self, *, immediate: bool) -> None:
        if (
            self._pending_process_core_input is None
            and not self._pending_terminal_snapshots
            and not self._pending_process_action_results
        ):
            return
        if self._terminal_retry_task is None or self._terminal_retry_task.done():
            self._terminal_retry_wakeup.clear()
            self._terminal_retry_task = asyncio.create_task(
                self._retry_process_durability(), name="process-durability-retry"
            )
        if immediate:
            self._terminal_retry_wakeup.set()

    async def _retry_process_action_result(
        self,
        action: RustAction,
        durable: RuntimeActionV1,
        event: RustEvent,
        *,
        failed: bool,
    ) -> None:
        pending = self._pending_process_action_results.get(action.action_id)
        if pending is None:
            pending = _PendingProcessActionResult(
                action=action,
                durable=durable,
                event=event,
                failed=failed,
                completed=asyncio.get_running_loop().create_future(),
            )
            self._pending_process_action_results[action.action_id] = pending
        self._ensure_terminal_retry(immediate=True)
        await asyncio.shield(pending.completed)

    async def _retry_process_durability(self) -> None:
        attempt = 0
        while (
            self._pending_process_core_input is not None
            or self._pending_terminal_snapshots
            or self._pending_process_action_results
        ):
            delay = _PROCESS_DURABILITY_RETRY_DELAYS_SECONDS[
                min(attempt, len(_PROCESS_DURABILITY_RETRY_DELAYS_SECONDS) - 1)
            ]
            try:
                await asyncio.wait_for(
                    self._terminal_retry_wakeup.wait(), timeout=delay
                )
            except TimeoutError:
                pass
            self._terminal_retry_wakeup.clear()
            failed = False
            pending_core_input = self._pending_process_core_input
            if (
                pending_core_input is not None
                and not pending_core_input.completed.done()
            ):
                try:
                    with self._store.use_journal_reservation(
                        pending_core_input.reservation_owner
                    ):
                        await self._persist_core_transition(
                            pending_core_input.payload,
                            pending_core_input.transition,
                            pending_core_input.update,
                        )
                except Exception:
                    failed = True
                    logger.warning("background_process.operation_failed")
                else:
                    self._complete_pending_process_core_input(pending_core_input)
                    if pending_core_input.wait_for_completion:
                        await asyncio.sleep(0)
                        continue
            if failed:
                attempt += 1
                continue
            for action_id, pending in tuple(
                self._pending_process_action_results.items()
            ):
                try:
                    await self._record_action_result(
                        pending.action,
                        pending.durable,
                        pending.event,
                        reconcile_started=None,
                        failed=pending.failed,
                    )
                except Exception:
                    failed = True
                    logger.warning("background_process.operation_failed")
                    continue
                self._pending_process_action_results.pop(action_id, None)
                if not pending.completed.done():
                    pending.completed.set_result(None)
                self._notify_work_state_changed()
            for process_identifier, snapshot in tuple(
                self._pending_terminal_snapshots.items()
            ):
                task = self._terminal_tasks.get(process_identifier)
                if task is not None and not task.done():
                    continue
                try:
                    await self._commit_terminal_snapshot(snapshot)
                except Exception:
                    failed = True
                    logger.warning("background_process.operation_failed")
                    continue
                self._pending_terminal_snapshots.pop(process_identifier, None)
                if self._terminal_tasks.get(process_identifier) is task:
                    self._terminal_tasks.pop(process_identifier, None)
                self._notify_work_state_changed()
            attempt = attempt + 1 if failed else 0

    def _notify_work_state_changed(self) -> None:
        callback = self._work_state_callback
        if callback is not None:
            result = callback()
            if isinstance(result, Awaitable):
                asyncio.ensure_future(result)

    async def _wait_for_terminal_updates(self, process_id: str | None = None) -> None:
        for identifier, task in tuple(self._terminal_tasks.items()):
            if (
                (process_id is None or identifier == process_id)
                and task.done()
                and not task.cancelled()
                and task.exception() is not None
            ):
                self._schedule_terminal_snapshot(
                    self._pending_terminal_snapshots[identifier]
                )
        tasks = [
            task
            for identifier, task in self._terminal_tasks.items()
            if process_id is None or identifier == process_id
        ]
        if not tasks:
            return
        await asyncio.gather(*tasks)
        for identifier, task in tuple(self._terminal_tasks.items()):
            if task in tasks and task.done() and task.exception() is None:
                del self._terminal_tasks[identifier]
                self._pending_terminal_snapshots.pop(identifier, None)
        self._terminal_retry_wakeup.set()

    async def _acknowledge_process_result_applied(
        self, action: RustAction, event: RustEvent
    ) -> None:
        if not isinstance(action, RustRuntimeBuiltinToolCallAction):
            return
        reservation = self._process_action_reservations.pop(action.action_id, None)
        if reservation is not None:
            _temporary, permanent, process_identifier = reservation
            self._store.release_journal_capacity(action.action_id)
            if action.call.name == "process.start" and process_identifier is not None:
                stored = await asyncio.to_thread(self._store.load)
                process_exists = any(
                    process.process_id == process_identifier
                    for process in stored.runtime_state.processes
                )
                notification_submitted = any(
                    notification.process_id == process_identifier
                    for notification in stored.runtime_state.submitted_process_notifications
                )
                if not process_exists or notification_submitted:
                    self._process_lifetime_reservations.pop(process_identifier, None)
                    self._store.release_journal_capacity(process_identifier)
        if action.call.name != "process.start":
            self._notify_work_state_changed()
            return
        identifier = process_id(
            self._store.session_id, action.action_id, action.call_id
        )
        barrier = self._process_start_barriers.get(identifier)
        if barrier is not None and not barrier.done():
            barrier.set_result(
                "accepted" if isinstance(event, RustToolSucceededEvent) else "failed"
            )
        self._notify_work_state_changed()

    async def _reserve_process_action_storage(
        self, action: RustRuntimeBuiltinToolCallAction, stored: StoredSession
    ) -> None:
        if action.action_id in self._process_action_reservations:
            return
        temporary, permanent, process_identifier = _process_storage_reservation(
            action, self._store.session_id, stored.runtime_state.processes
        )
        required = temporary + permanent
        if not await asyncio.to_thread(self._store.has_journal_capacity, required):
            await self._compact_store(allow_recoverable=True)
        if not await asyncio.to_thread(self._store.has_journal_capacity, required):
            raise HarnessStoreCapacityError(
                "insufficient space for process Action lifecycle"
            )
        reservations = {action.action_id: temporary}
        if process_identifier is not None:
            reservations[process_identifier] = permanent
        await asyncio.to_thread(self._store.reserve_journal_capacity, reservations)
        self._process_action_reservations[action.action_id] = (
            temporary,
            permanent,
            process_identifier,
        )
        if process_identifier is not None:
            self._process_lifetime_reservations[process_identifier] = permanent

    def _restore_process_storage_reservations(self, stored: StoredSession) -> None:
        pending_action_ids = {
            action.action_id for action in self.inspection.pending_actions
        }
        reserved_process_ids: set[str] = set()
        for durable in stored.runtime_state.actions:
            if durable.kind != "process" or durable.action_id not in pending_action_ids:
                continue
            action = _ACTION_ADAPTER.validate_python(durable.request)
            if not isinstance(action, RustRuntimeBuiltinToolCallAction):
                raise ValueError("stored process Action has the wrong request type")
            reservation = _process_storage_reservation(
                action, self._store.session_id, stored.runtime_state.processes
            )
            self._process_action_reservations[action.action_id] = reservation
            self._store.restore_journal_capacity(action.action_id, reservation[0])
            if reservation[2] is not None:
                reserved_process_ids.add(reservation[2])
                self._process_lifetime_reservations[reservation[2]] = reservation[1]
                self._store.restore_journal_capacity(reservation[2], reservation[1])
        notified = {
            notification.process_id
            for notification in stored.runtime_state.submitted_process_notifications
        }
        for process in stored.runtime_state.processes:
            if (
                process.process_id in notified
                or process.process_id in reserved_process_ids
            ):
                continue
            process_bytes = len(
                canonical_json(
                    cast(
                        JsonValue,
                        process.model_dump(
                            mode="json", by_alias=True, exclude_none=True
                        ),
                    )
                )
            )
            self._process_lifetime_reservations[process.process_id] = (
                _PROCESS_LIFETIME_OVERHEAD_BYTES + process_bytes * 2
            )
            self._store.restore_journal_capacity(
                process.process_id,
                self._process_lifetime_reservations[process.process_id],
            )
        pending_starts = {
            action.call_id: action.action_id
            for durable in stored.runtime_state.actions
            if durable.kind == "process" and durable.action_id in pending_action_ids
            for action in [_ACTION_ADAPTER.validate_python(durable.request)]
            if isinstance(action, RustRuntimeBuiltinToolCallAction)
            and action.call.name == "process.start"
        }
        for callback in stored.runtime_state.callbacks:
            if callback.kind != "approval" or callback.state != "pending":
                continue
            matching = [
                action_id
                for call_id, action_id in pending_starts.items()
                if callback.callback_id == f"approval-{call_id}"
                or callback.callback_id.startswith(f"approval-{call_id}-")
            ]
            if len(matching) == 1:
                self._process_callback_reservations[callback.callback_id] = matching[0]

    async def _resynchronize_completion_action(
        self, action: RustLLMCallAction
    ) -> RustLLMCallAction:
        transition = await self._apply(
            RustCompletionModelInputResyncRequestedEvent(action_id=action.action_id)
        )
        refreshed = (
            next(
                (
                    directive.action
                    for directive in transition.next.directives
                    if isinstance(directive, RustRefreshActionDirective)
                    and directive.action.action_id == action.action_id
                ),
                None,
            )
            if isinstance(transition.next, RustActionsNextAction)
            else None
        )
        if not isinstance(refreshed, RustLLMCallAction):
            raise RuntimeError(
                "Core model-input resync did not return a completion refresh"
            )
        return refreshed

    async def _ensure_action_started(
        self, action: RustToolCallAction | RustLLMCallAction
    ) -> None:
        if isinstance(action, RustLLMCallAction):
            if action.purpose != "compaction" or action.compaction_id is None:
                raise ValueError(
                    "only identified compaction actions have public start entries"
                )
            entry_id = f"checkpoint-compaction-{action.compaction_id}"
        else:
            entry_id = f"effect-{action.action_id}"
        if any(
            entry.get("id") == entry_id
            for entry in self._projector.projection.snapshot.history.entries
        ):
            return
        update = self._projector.apply_action_started(
            action, observed_at=time.time_ns() // 1_000_000
        )
        await self._record_projection_update(update)

    async def _recover(self, durable: RuntimeActionV1, action: RustAction) -> RustEvent:
        if self._recover_action is not None:
            return await self._recover_action(durable, action)
        if durable.recovery_mode in {"idempotent_retry", "reconcile", "redeliver"}:
            return await self._execute_action(action)
        return _failed_action_event(
            action, "The interrupted effect cannot be reconnected safely"
        )

    async def _apply(self, command: RustEvent) -> RustSessionTransition:
        await self._maybe_compact_journal()
        pending = self._pending_process_core_input
        if pending is not None:
            if pending.command == command:
                await self._retry_pending_process_core_input_once(pending)
                if (
                    pending.wait_for_completion
                    and self._pending_process_core_input is pending
                ):
                    self._pending_process_core_input = None
                return pending.transition
            await self._wait_for_pending_process_core_input(pending)
            if not pending.wait_for_completion:
                if not isinstance(pending.command, RustNotificationEvent):
                    raise RuntimeError(
                        "pending terminal Core input is not a notification"
                    )
                await self._record_process_notification_submitted(
                    pending.reservation_owner, pending.command.notification.id
                )
            elif self._pending_process_core_input is pending:
                self._pending_process_core_input = None
        inspection = self.inspection
        payload = RustHarnessInput(
            input_id=inspection.last_input_id + 1,
            determinism=RustDeterminismContext(
                time_unix_ms=time.time_ns() // 1_000_000,
                random_seed=secrets.randbits(32),
            ),
            command=command,
        )
        result = parse_apply_result(
            self._core.apply(
                canonical_json(
                    payload.model_dump(mode="json", by_alias=True, exclude_none=False)
                ).decode()
            )
        )
        if isinstance(result, RustRejectedApplyResult):
            rejection = result.rejection
            if (
                isinstance(rejection, RustInvalidStateRejection)
                and rejection.command_type == "user_message"
                and rejection.state == "idle"
            ):
                raise HarnessStaleTurnError(None)
        if not isinstance(result, RustAcceptedApplyResult):
            raise RuntimeError(f"Core rejected durable input: {result!r}")
        transition = result.transition
        previous_transition = self._replayed_transition
        update = self._projector.apply(
            transition, observed_at=payload.determinism.time_unix_ms
        )
        # Track the Core's just-applied transition before persisting. A last-resort
        # compaction inside _persist_core_transition (capacity error on the
        # projection append) folds a checkpoint that already includes this input,
        # so it must stamp this transition as pending_transition — not the previous
        # one. Setting it here keeps _replayed_transition matching the checkpoint.
        self._replayed_transition = transition
        if self._store.current_journal_reservation_owner is None:
            await self._persist_plain_core_transition(
                payload, transition, update, previous_transition
            )
        else:
            persistence = asyncio.create_task(
                self._persist_process_core_transition(
                    command, payload, transition, update
                ),
                name=f"process-core-durability-{payload.input_id}",
            )
            try:
                await asyncio.shield(persistence)
            except asyncio.CancelledError:
                await persistence
                raise
        return transition

    async def _persist_core_transition(
        self,
        payload: RustHarnessInput,
        transition: RustSessionTransition,
        update: ProjectionUpdate,
    ) -> None:
        await asyncio.to_thread(
            self._store.record_core_input,
            payload,
            transition,
            self._config.capabilities,
            self._config.settings,
            self._config.plugins,
        )
        await self._record_projection_update(update)

    async def _persist_plain_core_transition(
        self,
        payload: RustHarnessInput,
        transition: RustSessionTransition,
        update: ProjectionUpdate,
        previous_transition: RustSessionTransition | None,
    ) -> None:
        try:
            await asyncio.to_thread(
                self._store.record_core_input,
                payload,
                transition,
                self._config.capabilities,
                self._config.settings,
                self._config.plugins,
            )
        except Exception:
            # Unlike the process path this one has no retry owner, so a lost write
            # would leave the Core numbering ahead of the journal for good: every
            # later input lands under an ID replay rejects, stranding the session.
            # Only the write is compensated -- once the record lands the input is
            # durable, and a projection append that then fails is repaired by replay.
            await asyncio.to_thread(
                self._discard_undurable_core_input, previous_transition
            )
            raise
        await self._record_projection_update(update)

    def _discard_undurable_core_input(
        self, previous_transition: RustSessionTransition | None
    ) -> None:
        # Synchronous and off the loop, like _rewrite_plugins_sync: a cancelled awaiter
        # must not observe a Runtime holding a Core it has already retired.
        stored = self._store.load()
        replacement, _transitions = stored.restore_core_with_transitions(self._config)
        previous = self._core
        self._core = replacement
        previous.close()
        # _apply advanced the projector before attempting the write, and SessionProjector
        # advances in place, so the discarded input has to be rolled out of it too.
        # Not ``rebased``: this rolls an input back rather than resyncing around one, and
        # streamed content belonging to a turn that is being undone has nothing left to
        # settle it.
        self._projector = SessionProjector(stored.projection_state)
        self._replayed_transition = previous_transition
        self._model_input_resync_required = True
        logger.warning(
            "Unified session discarded a Core input that never reached the journal",
            extra={
                "harness_backend": "unified",
                "session_id": self._store.session_id,
                "store_format": "mistral.vibe.unified-session-store/v1",
                "generation": stored.manifest.generation,
                "core_last_input_id": self.inspection.last_input_id,
            },
        )

    async def _persist_process_core_transition(
        self,
        command: RustEvent,
        payload: RustHarnessInput,
        transition: RustSessionTransition,
        update: ProjectionUpdate,
    ) -> None:
        failure: Exception | None = None
        try:
            await self._persist_core_transition(payload, transition, update)
            return
        except Exception as error:
            failure = error
            logger.warning("background_process.operation_failed")
        owner = self._store.current_journal_reservation_owner
        if owner is None:
            raise RuntimeError("process Core input has no journal reservation")
        pending = _PendingProcessCoreInput(
            command=command,
            payload=payload,
            transition=transition,
            update=update,
            reservation_owner=owner,
            wait_for_completion=not isinstance(command, RustNotificationEvent),
            completed=asyncio.get_running_loop().create_future(),
        )
        if self._pending_process_core_input is not None:
            raise RuntimeError("another process Core input is awaiting durability")
        self._pending_process_core_input = pending
        self._ensure_terminal_retry(immediate=pending.wait_for_completion)
        if not pending.wait_for_completion:
            if failure is None:
                raise RuntimeError("missing process Core persistence failure")
            raise failure
        await self._wait_for_pending_process_core_input(pending)
        if self._pending_process_core_input is pending:
            self._pending_process_core_input = None

    async def _retry_pending_process_core_input_once(
        self, pending: _PendingProcessCoreInput
    ) -> None:
        if pending.completed.done():
            return
        try:
            with self._store.use_journal_reservation(pending.reservation_owner):
                await self._persist_core_transition(
                    pending.payload, pending.transition, pending.update
                )
        except Exception:
            logger.warning("background_process.operation_failed")
            self._ensure_terminal_retry(immediate=False)
            raise
        self._complete_pending_process_core_input(pending)

    async def _wait_for_pending_process_core_input(
        self, pending: _PendingProcessCoreInput
    ) -> None:
        self._ensure_terminal_retry(immediate=True)
        try:
            await asyncio.shield(pending.completed)
        except asyncio.CancelledError:
            await pending.completed
            raise

    def _complete_pending_process_core_input(
        self, pending: _PendingProcessCoreInput
    ) -> None:
        self._replayed_transition = pending.transition
        if not pending.completed.done():
            pending.completed.set_result(None)

    async def _record_projection_update(self, update: ProjectionUpdate) -> None:
        delta = update.delta
        if delta is None:
            # In-memory advance: no journal write, and no store read to decide
            # on one. Both are per-fragment costs on the live provider stream.
            if self._event_sink is not None:
                emitted = self._event_sink(update.event)
                if isinstance(emitted, Awaitable):
                    await emitted
            return
        effective = (await asyncio.to_thread(self._store.load)).projection_state
        if effective.watermark >= update.projection.watermark:
            if (
                effective.watermark == update.projection.watermark
                and effective.snapshot != update.journaled_snapshot
            ):
                raise RuntimeError("conflicting public projection persistence retry")
            self._projector = self._projector.rebased(effective)
            return
        await self._advance_projection(update.projection.watermark, delta)
        if self._event_sink is not None:
            emitted = self._event_sink(update.event)
            if isinstance(emitted, Awaitable):
                await emitted

    async def _advance_projection(self, watermark: int, delta: ProjectionDelta) -> None:
        try:
            await asyncio.to_thread(
                self._store.advance_projection_delta, watermark, delta
            )
        except HarnessStoreCapacityError:
            # Last-resort net: compaction folds the journal into a fresh
            # generation whose baseline equals the delta's prior snapshot, so the
            # retried delta reconstructs the same advance. Proactive compaction in
            # ``_apply`` should keep this from ever firing.
            await self._compact_store(allow_recoverable=True)
            await asyncio.to_thread(
                self._store.advance_projection_delta, watermark, delta
            )
        effective = (await asyncio.to_thread(self._store.load)).projection_state
        self._projector = self._projector.rebased(effective)

    async def _catch_up_projection(self) -> None:
        projected_sequence = self._projector.projection.snapshot_sequence
        pending = tuple(
            replayed
            for replayed in self._replayed_core_inputs
            if replayed.sequence > projected_sequence
        )
        for replayed in pending:
            update = self._projector.apply(
                replayed.transition, observed_at=replayed.observed_at
            )
            if update.delta is not None:
                await self._advance_projection(
                    update.projection.watermark, update.delta
                )
        self._replayed_core_inputs = ()

    async def _maybe_compact_journal(self) -> None:
        """Fold the journal into a fresh generation before it can reach the cap.

        Runs at the top of ``_apply``, the one boundary where the previous
        transition is fully persisted and no Core input is mid-flight, so folding
        cannot double-apply a journalled input. A process Core input awaiting
        durability or a live Action journal reservation means we are mid-flight;
        skip and retry on a later clean boundary. Once deltas keep per-advance
        records small this rarely fires; it is a safety valve, not routine cost.

        Compaction resets ``_replayed_core_inputs``, so it must only run once
        their transitions are projected. Public entry points drain them via
        ``_catch_up_projection`` before dispatching to ``_apply``; guarding here
        turns that caller contract into a skip rather than silent data loss if a
        future path reaches ``_apply`` with replayed inputs still pending.
        """
        if self._pending_process_core_input is not None:
            return
        if self._store.current_journal_reservation_owner is not None:
            return
        if self._replayed_core_inputs:
            return
        journal_bytes = await asyncio.to_thread(self._store.journal_bytes)
        if journal_bytes < _JOURNAL_COMPACTION_HIGH_WATER_BYTES:
            return
        await self._compact_store(allow_recoverable=True)

    async def _compact_store(self, allow_recoverable: bool = False) -> bool:
        """Fold the journal on a worker, without letting a canceller abandon it.

        The fold serialises the Core, and a worker thread does not stop when the
        task awaiting it is cancelled: abandoning the await leaves the worker
        holding the Core while ``close`` retires it, and the Core rejects the
        second borrow. The canceller waits for a fold already under way instead.
        """
        compaction = asyncio.create_task(
            asyncio.to_thread(self._compact_store_sync, allow_recoverable),
            name=f"journal-fold-{self._store.session_id}",
        )
        try:
            return await asyncio.shield(compaction)
        except asyncio.CancelledError:
            await compaction
            raise

    def _compact_store_sync(self, allow_recoverable: bool = False) -> bool:
        started = time.perf_counter()
        stored = self._store.load()
        runtime_state = stored.runtime_state
        # Mirror _commit_runtime_state_locked: a mid-turn (recoverable) fold must
        # record the in-flight transition as pending_transition so recover() can
        # re-drive its still-pending actions; a quiescent fold clears any stale
        # one. Without this, a crash after a mid-turn compaction leaves pending
        # actions with no transition to replay and recover() raises.
        pending_transition = (
            self._replayed_transition
            if self.inspection.status in {"running", "compacting"}
            else None
        )
        runtime_state = runtime_state.model_copy(
            update={"pending_transition": pending_transition}
        )
        if not allow_recoverable and not runtime_state.quiescent:
            return False
        if not stored.journal:
            return False
        journal_bytes = self._store.journal_bytes()
        sequence = stored.runtime_state.snapshot_sequence
        checkpoint, core_last_input_id = self._capture_core_generation()
        self._store.write_generation(
            checkpoint=checkpoint,
            runtime_state=runtime_state.model_copy(
                update={
                    "snapshot_sequence": sequence,
                    "core_last_input_id": core_last_input_id,
                    "core_capabilities": self._config.capabilities,
                    "core_settings": self._config.settings,
                    "core_plugins": list(self._config.plugins),
                }
            ),
            projection_state=stored.projection_state.model_copy(
                update={"snapshot_sequence": sequence}
            ),
            core_action_ids=self.pending_action_ids,
        )
        published = self._store.last_publication
        # Compaction rewrites the store, not the conversation: the generation above
        # carries the checkpoint just taken from the live Core, so restoring one from
        # it would land on the state already in hand and lose the delivery cursor with
        # it. Only the journal-derived state is reset.
        refreshed = self._store.load()
        self._projector = self._projector.rebased(refreshed.projection_state)
        # Keep the in-memory transition consistent with the pending_transition just
        # written: a recoverable fold retains the in-flight transition (mirroring
        # _commit_runtime_state_locked), a quiescent fold clears it. Setting None
        # here would disagree with the durable generation after a mid-turn fold.
        self._replayed_transition = pending_transition
        self._replayed_core_inputs = ()
        record_session_operation(
            time.perf_counter() - started,
            operation="compaction",
            outcome="success",
            source_backend="unified",
        )
        logger.info(
            "Unified session compaction completed",
            extra={
                "harness_backend": "unified",
                "session_id": self._store.session_id,
                "store_format": "mistral.vibe.unified-session-store/v1",
                "generation": refreshed.manifest.generation,
                "recovery_journal_sequence": sequence,
                "recovery_journal_bytes": journal_bytes,
                # Chunks written stays near zero as a session grows unless the
                # transcript is being rewritten rather than appended to, which is
                # the workload where the chunk pool stops paying for itself.
                "pooled_chunks_written": published.written,
                "pooled_chunks_reused": published.reused,
                "pooled_bytes_written": published.bytes_written,
            },
        )
        return True

    def _reject_running_core(self) -> None:
        if self.inspection.status == "running":
            raise RuntimeError("cannot re-pin the plugins of a running Core")

    def _load_quiescent(self) -> StoredSession:
        stored = self._store.load()
        if not stored.runtime_state.quiescent:
            raise RuntimeError("cannot re-pin the plugins of a non-quiescent session")
        return stored

    def _rewrite_plugins_sync(
        self, plugin_lock: PluginLockV1, config: RustHarnessConfig
    ) -> None:
        stored = self._load_quiescent()
        sequence = stored.runtime_state.snapshot_sequence
        checkpoint, core_last_input_id = self._capture_core_generation()
        # Building the replacement is the step that rejects a configuration the
        # new plugin set cannot satisfy, so it runs before anything durable is
        # written: a Core that will not build leaves the previous lock recorded.
        replacement = restore_core_from_checkpoint(
            config, checkpoint, resume_input_id=core_last_input_id
        )
        try:
            self._store.write_generation(
                checkpoint=checkpoint,
                runtime_state=stored.runtime_state.model_copy(
                    update={
                        "snapshot_sequence": sequence,
                        "plugin_lock": plugin_lock,
                        "core_last_input_id": core_last_input_id,
                        "core_capabilities": config.capabilities,
                        "core_settings": config.settings,
                        "core_plugins": list(config.plugins),
                    }
                ),
                projection_state=stored.projection_state.model_copy(
                    update={"snapshot_sequence": sequence}
                ),
                core_action_ids=self.pending_action_ids,
            )
        except BaseException:
            replacement.close()
            raise
        refreshed = self._store.load()
        previous = self._core
        self._core = replacement
        previous.close()
        self._config = config
        self._projector = SessionProjector(refreshed.projection_state)
        self._replayed_transition = None
        self._replayed_core_inputs = ()
        self._model_input_resync_required = False
        logger.info(
            "Unified session plugins re-pinned",
            extra={
                "harness_backend": "unified",
                "session_id": self._store.session_id,
                "store_format": "mistral.vibe.unified-session-store/v1",
                "generation": refreshed.manifest.generation,
                "plugin_count": len(plugin_lock.plugins),
            },
        )

    def _guard_open(self) -> None:
        if self._closed:
            raise RuntimeError("The durable session Runtime is closed")


def _process_storage_reservation(
    action: RustRuntimeBuiltinToolCallAction,
    session_id: str,
    processes: list[ManagedProcessV1],
) -> tuple[int, int, str | None]:
    action_bytes = len(
        canonical_json(
            cast(
                JsonValue,
                action.model_dump(mode="json", by_alias=True, exclude_none=True),
            )
        )
    )
    temporary = _PROCESS_ACTION_OVERHEAD_BYTES + action_bytes * 3
    permanent = 0
    process_identifier: str | None = None
    if action.call.name == "process.output":
        temporary += 3 * 64_000
    elif action.call.name == "process.list":
        temporary += 3 * len(
            canonical_json(
                cast(
                    JsonValue,
                    [
                        process.model_dump(mode="json", by_alias=True)
                        for process in processes
                    ],
                )
            )
        )
    elif action.call.name == "process.start":
        process_identifier = process_id(session_id, action.action_id, action.call_id)
        permanent = _PROCESS_LIFETIME_OVERHEAD_BYTES + action_bytes * 2
    return temporary, permanent, process_identifier


def _process_persistence_failure(
    action: RustRuntimeBuiltinToolCallAction, session_id: str
) -> RustEvent:
    if action.call.name == "process.start":
        return process_failed(
            action,
            process_error(
                "process_start_failed",
                "Background process could not be started",
                {
                    "processId": process_id(
                        session_id, action.action_id, action.call_id
                    ),
                    "stage": "persistence",
                },
            ),
        )
    details: JsonObject = {
        "operation": action.call.name.removeprefix("process."),
        "stage": "persistence",
    }
    process_identifier = action.call.arguments.get("processId")
    if isinstance(process_identifier, str):
        details["processId"] = process_identifier
    return process_failed(
        action,
        process_error("process_io_failed", "Background process I/O failed", details),
    )


def _rebuilt_core(core: HarnessSession, expected_last_input_id: int) -> HarnessSession:
    """Check that a Core rebuilt from a fresh generation resumed where the live one stood.

    Discarding the resident Core and rebuilding it from the checkpoint just written is
    what proves the generation is self-sufficient. That only holds if the rebuild is
    transparent: the Runtime keeps numbering inputs from the live Core's cursor, so a
    replacement that resumed anywhere else would reject the next input or silently reuse
    an ID the journal already records.
    """
    resumed = RustSessionInspection.model_validate_json(core.inspect()).last_input_id
    if resumed != expected_last_input_id:
        core.close()
        raise RuntimeError(
            f"rebuilt Core resumed at input {resumed}, expected {expected_last_input_id}"
        )
    return core


def _runtime_state_for_replaced_context(
    runtime_state: RuntimeStateV3,
) -> RuntimeStateV3:
    """Drop the ledgers that belong to the timeline a replaced context abandons.

    A Core rebuilt from history numbers its inputs from the beginning again, and
    the identities it derives from that numbering come back with it: Action IDs,
    the provider operations and approval callbacks named after them, and the
    processes named after the call that started them. Carried across, the first
    Action of the new timeline is answered by the durable record of whatever
    occupied its slot in the old one -- the Runtime replays that result and
    never asks the model -- so the ledgers are dropped with the turns they
    describe. Only a quiescent session can get here, so nothing being dropped is
    still in flight and every recorded process has already terminated.

    The accepted-input cursor restarts with them. Every other generation write
    carries it forward so its rebuild is transparent; this one is the single place
    the Runtime deliberately begins a new sequence.
    """
    return runtime_state.model_copy(
        update={
            "command_receipts": [],
            "actions": [],
            "callbacks": [],
            "provider_operations": [],
            "processes": [],
            "submitted_process_notifications": [],
            "core_last_input_id": 0,
        }
    )


def build_terminal_notification(process: ManagedProcessV1) -> RustHarnessNotification:
    if process.status == "running":
        raise ValueError("running process cannot produce a terminal notification")
    level, message = {
        "completed": ("info", f"Background process {process.process_id} completed."),
        "failed": ("error", f"Background process {process.process_id} failed."),
        "stopped": ("info", f"Background process {process.process_id} stopped."),
        "orphaned": (
            "warning",
            "The App Server can no longer manage background process "
            f"{process.process_id} or confirm whether it is running.",
        ),
    }[process.status]
    exit_code = (
        process.exit_code
        if process.status == "completed"
        and process.exit_code is not None
        and -(2**31) <= process.exit_code <= 2**31 - 1
        else None
    )
    return RustHarnessNotification(
        id=f"background-process:{process.process_id}:terminal",
        source=RustBackgroundProcessNotificationSource(
            process_id=process.process_id, status=process.status, exit_code=exit_code
        ),
        level=cast(Literal["info", "warning", "error"], level),
        message=message,
    )


def _process_not_running(process: ManagedProcessV1) -> RustProtocolError:
    return process_error(
        "process_not_running",
        "Background process is not running",
        {"processId": process.process_id, "status": process.status},
    )


def _process_identity_conflict(
    action: RustRuntimeBuiltinToolCallAction,
    request: ValidatedProcessStart | ValidatedProcessWrite | ValidatedProcessStop,
) -> RustProtocolError:
    return process_error(
        "process_identity_conflict",
        "Process operation identity conflicts with an earlier request",
        {"actionId": action.action_id, "processId": request.process_id},
    )


def _effect_recovery_error(
    action: RustRuntimeBuiltinToolCallAction,
    process_id: str,
    operation: Literal["start", "write", "stop"],
    status: str,
    exit_code: int | None,
    message: str,
) -> RustProtocolError:
    return process_error(
        "effect_recovery_failed",
        message,
        {
            "actionId": action.action_id,
            "processId": process_id,
            "operation": operation,
            "status": status,
            "exitCode": exit_code,
        },
    )


def _stop_result(process: ManagedProcessV1) -> JsonObject:
    return {
        "processId": process.process_id,
        "status": process.status,
        "exitCode": process.exit_code if process.status == "completed" else None,
    }


def _timestamp(minimum: str | None = None) -> str:
    now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return max(now, minimum) if minimum is not None else now


def _context_compaction_response(transition: RustSessionTransition) -> JsonValue:
    terminal = [
        observation
        for observation in transition.observations
        if isinstance(
            observation,
            RustContextCompactedObservation | RustContextCompactionFailedObservation,
        )
        and observation.trigger == "manual"
    ]
    if len(terminal) != 1:
        raise RuntimeError(
            "manual context compaction has no single terminal observation"
        )
    observation = terminal[0]
    if isinstance(observation, RustContextCompactedObservation):
        return {"type": "succeeded", "summary": observation.summary}
    return {
        "type": "failed",
        "error": cast(
            JsonValue,
            observation.error.model_dump(mode="json", by_alias=True, exclude_none=True),
        ),
    }


def _parse_context_compaction_response(response: JsonValue) -> ContextCompactionResult:
    if not isinstance(response, dict):
        raise RuntimeError("stored context-compaction response is not an object")
    result_type = response.get("type")
    if result_type == "succeeded":
        summary = response.get("summary")
        if not isinstance(summary, str) or not summary:
            raise RuntimeError("stored context-compaction response has no summary")
        return ContextCompactionSuccess(summary=summary)
    if result_type == "failed":
        return ContextCompactionFailure(
            error=RustProtocolError.model_validate(response.get("error"))
        )
    raise RuntimeError("stored context-compaction response has an unknown result type")


def _capability_reconfigure_params(
    capabilities: RustHarnessCapabilitySet, revision: str
) -> dict[str, JsonValue]:
    return {
        "revision": revision,
        "capabilities": cast(
            JsonValue,
            capabilities.model_dump(mode="json", by_alias=True, exclude_none=True),
        ),
    }


def _settings_reconfigure_params(settings: RustHarnessSettings) -> dict[str, JsonValue]:
    return {
        "settings": cast(
            JsonValue,
            settings.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
    }


def _settings_reconfigure_command(params: dict[str, JsonValue]) -> RustReconfigureEvent:
    settings = RustHarnessSettings.model_validate(params.get("settings"))
    return RustReconfigureEvent(changes=[RustSettingsChange(value=settings)])


def _capability_revision(capabilities: RustHarnessCapabilitySet) -> str:
    return sha256_json(
        cast(
            JsonValue,
            capabilities.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
    )


def _capability_reconfigure_command(
    params: dict[str, JsonValue],
) -> RustReconfigureEvent:
    capabilities = RustHarnessCapabilitySet.model_validate(params.get("capabilities"))
    return RustReconfigureEvent(changes=[RustCapabilitiesChange(value=capabilities)])


def _action_kind(
    action: RustAction,
) -> Literal[
    "completion", "tool", "hook", "process", "callback", "child", "filesystem"
]:
    if isinstance(action, RustLLMCallAction):
        return "completion"
    if isinstance(action, RustHookCallActionBase):
        return "hook"
    if isinstance(action, RustFilesystemAction):
        return "filesystem"
    if isinstance(action, RustRuntimeBuiltinToolCallAction):
        if action.call.name.startswith("process."):
            return "process"
        if action.call.name.startswith("subagent."):
            return "child"
    return "tool"


def _recovery_mode(action: RustAction) -> RecoveryMode:
    kind = _action_kind(action)
    if kind == "completion":
        return "reconnect_or_fail"
    if kind in {"process", "child"}:
        return "reconcile"
    if kind == "filesystem":
        return "idempotent_retry"
    return "fail"


async def _unavailable_action(action: RustAction) -> RustEvent:
    return _failed_action_event(
        action, "No Runtime adapter is configured for this action"
    )


async def _cancel_tasks(tasks: Sequence[asyncio.Task[RustEvent]]) -> None:
    if not tasks:
        return
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _failed_action_event(action: RustAction, message: str) -> RustEvent:
    error = RustProtocolError(
        code="effect_recovery_failed", message=message, retryable=False, details=None
    )
    if isinstance(action, RustLLMCallAction):
        return RustCompletionFailedEvent(action_id=action.action_id, error=error)
    if isinstance(action, RustHookCallActionBase):
        return RustHookFailedEvent(action_id=action.action_id, error=error)
    if isinstance(
        action, RustRuntimeBuiltinToolCallAction | RustProvidedToolCallAction
    ):
        return RustToolFailedEvent(
            action_id=action.action_id,
            call_id=action.call_id,
            result=RustToolFailureResult(error=error),
        )
    if isinstance(action, RustFilesystemAction):
        return RustFilesystemFailedEvent(action_id=action.action_id, error=error)
    raise TypeError(f"Unsupported action type: {type(action).__name__}")


def action_from_recovery_state(action: RuntimeActionV1) -> RustAction:
    # A request is only dropped for an Action that Core did not hold when the
    # generation was published, and Core can only keep Actions it already held, so
    # reaching here with a pruned one is a storage bug rather than a digest
    # mismatch. Say so plainly instead of blaming the digest.
    if action.request_pruned:
        raise ValueError("Durable action request was pruned and cannot be recovered")
    if action.request_sha256 != _sha256_request(action.request):
        raise ValueError("Durable action request digest does not match its content")
    return _ACTION_ADAPTER.validate_python(action.request)


def _sha256_request(value: JsonValue) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(value)).hexdigest()


__all__ = [
    "ActionExecutor",
    "ActionRecoverer",
    "DurableCommandResult",
    "DurableSessionRuntime",
    "EventSink",
    "action_from_recovery_state",
    "build_terminal_notification",
]
