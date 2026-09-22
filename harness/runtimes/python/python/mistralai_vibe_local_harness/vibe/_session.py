"""One Harness Session lifecycle and Session Protocol operations."""

import asyncio
import base64
import json
import logging
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Never, cast
from urllib.parse import urlparse

from pydantic import JsonValue

from mistralai_vibe_local_harness.protocol import (
    RustBlobResourceContents,
    RustContentBlock,
    RustContextMessageEvent,
    RustEmbeddedResourceContentBlock,
    RustFailTurnEvent,
    RustHarnessCapabilitySet,
    RustHarnessConfig,
    RustHarnessSettings,
    RustImageContentBlock,
    RustPluginContextDefinition,
    RustProtocolError,
    RustProvidedToolCallAction,
    RustResourceLinkContentBlock,
    RustRuntimeBuiltinToolCallAction,
    RustTextContentBlock,
    RustTextResourceContents,
    RustToolSucceededEvent,
    RustToolSuccessResult,
    RustUserMessageEvent,
)
from mistralai_vibe_local_harness.session_protocol import (
    BlockedSessionStatus,
    FailedPublicTurn,
    FailedSessionStatus,
    IdleSessionStatus,
    InProgressPublicTurn,
    InterruptedPublicTurn,
    JsonObject,
    LatestPublicHistoryPage,
    PluginInfo,
    PublicError,
    PublicRetryState,
    PublicSession,
    PublicSessionState,
    RunningSessionStatus,
    SessionReadParams,
    SessionReadResult,
    SessionSnapshot,
    TurnEnqueueParams,
    TurnEnqueueResponse,
    TurnInputEntry,
    TurnQueue,
    TurnQueueReadParams,
    TurnQueueReadResponse,
    TurnQueueRemoveParams,
    TurnQueueRemoveResponse,
    TurnQueueReplaceParams,
    TurnQueueReplaceResponse,
    TurnQueueResumeParams,
    TurnQueueResumeResponse,
    TurnQueueSteerParams,
    TurnQueueSteerResponse,
    TurnQueueUpdatedEvent,
)
from mistralai_vibe_local_harness.session_protocol import (
    ContentBlock as SessionContentBlock,
)
from mistralai_vibe_local_harness.session_protocol import (
    EmbeddedResourceContentBlock as SessionEmbeddedResourceContentBlock,
)
from mistralai_vibe_local_harness.session_protocol import (
    Event as SessionEvent,
)
from mistralai_vibe_local_harness.session_protocol import (
    ImageContentBlock as SessionImageContentBlock,
)
from mistralai_vibe_local_harness.session_protocol import (
    ResourceLinkContentBlock as SessionResourceLinkContentBlock,
)
from mistralai_vibe_local_harness.session_protocol import (
    TextContentBlock as SessionTextContentBlock,
)
from mistralai_vibe_local_harness.vibe._connector_models import (
    ConnectorRouteSnapshot,
    ResolvedConnectorCatalog,
    ResolvedConnectorSelection,
)
from mistralai_vibe_local_harness.vibe._connector_runtime import ConnectorRuntime
from mistralai_vibe_local_harness.vibe._deferred_turn import (
    DeferredTurnPreparation,
    DeferredTurnPreparationContext,
    DeferredTurnPreparationError,
    DeferredTurnStartParams,
    DeferredTurnStartResult,
    PublicHistoryEntry,
    TurnStartRequest,
)
from mistralai_vibe_local_harness.vibe._errors import (
    HarnessCallbackClosedError,
    HarnessCallbackConflictError,
    HarnessCallbackNotFoundError,
    HarnessCommandConflictError,
    HarnessContextCompactionError,
    HarnessNotImplementedError,
    HarnessStaleTurnError,
    HarnessTurnConflictError,
    HarnessTurnQueuePendingError,
)
from mistralai_vibe_local_harness.vibe._file_image_fallback import (
    build_file_image_content_block,
)
from mistralai_vibe_local_harness.vibe._local_actions import (
    ApprovalGrant,
    HookHandlers,
    _LocalActionState,
    gated_tool_name,
)
from mistralai_vibe_local_harness.vibe._mcp_models import (
    MCPRouteSnapshot,
    ResolvedMCPCatalog,
)
from mistralai_vibe_local_harness.vibe._mcp_runtime import MCPRuntime
from mistralai_vibe_local_harness.vibe._paths import file_uri_to_path
from mistralai_vibe_local_harness.vibe._projection import (
    content_is_injected,
    first_user_message_preview,
    public_message_entry,
    renamed_session_state,
)
from mistralai_vibe_local_harness.vibe._runtime import (
    ContextCompactionFailure,
    DurableSessionRuntime,
)
from mistralai_vibe_local_harness.vibe._runtime_config import (
    LocalRuntimeAdapterConfig,
    ProviderRetry,
)
from mistralai_vibe_local_harness.vibe._session_events import SessionEventSubscriptions
from mistralai_vibe_local_harness.vibe._storage import (
    PluginLockV1,
    RuntimeStateV3,
    SessionLease,
    SessionMetadataV1,
    SessionPin,
)
from mistralai_vibe_local_harness.vibe._subagents._configuration import (
    ResolvedSubagentConfiguration,
)
from mistralai_vibe_local_harness.vibe._subagents._models import (
    ChildCommandResult,
    ChildGenerationRef,
    CloseRunningTarget,
    InterruptTarget,
    ParentOriginatedChildCommandReceipt,
    SendStartTarget,
    SendSteerTarget,
    SpawnTarget,
)
from mistralai_vibe_local_harness.vibe._title import (
    TitleCadence,
    count_model_steps,
    generate_session_title,
    latest_compaction_id,
)
from mistralai_vibe_local_harness.vibe._turn_queue import (
    PreparedTurnEntry,
    QueuedTurnRecord,
    SessionTurnQueue,
)
from mistralai_vibe_local_harness.vibe.plugins import SessionPluginBinding, empty_plugin_binding

logger = logging.getLogger(__name__)

_MAX_IMAGE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _EncodedImageFile:
    data: str
    size: int


@dataclass(frozen=True, slots=True)
class HarnessSessionSubscription:
    """A complete snapshot plus the events committed after its watermark."""

    snapshot: SessionSnapshot
    events: AsyncIterator[JsonObject]


class UnifiedHarnessSessionBackend:
    def __init__(
        self,
        session_id: str,
        created_at: int,
        *,
        cwd: str | None = None,
        on_reconfigure_subagents: Callable[[LocalRuntimeAdapterConfig], Awaitable[None]]
        | None = None,
        state: PublicSessionState | None = None,
        watermark: int = 0,
        lease: SessionLease | None = None,
        runtime: DurableSessionRuntime | None = None,
        mcp_runtime: MCPRuntime | None = None,
        connector_runtime: ConnectorRuntime | None = None,
        on_shutdown: Callable[[str], None] | None = None,
        on_work_state_changed: Callable[[str], Awaitable[None] | None] | None = None,
        image_source_roots: tuple[Path, ...] | None = None,
        attachments_root: Path | None = None,
        discard_on_shutdown: Callable[[str], None] | None = None,
        promote_on_start: Callable[[SessionPluginBinding], DurableSessionRuntime] | None = None,
        initialize_subagents_on_promote: (
            Callable[[DurableSessionRuntime, SessionPluginBinding], Awaitable[None]] | None
        ) = None,
        pending_plugin_binding: SessionPluginBinding | None = None,
        session_metadata: SessionMetadataV1 | None = None,
        action_adapter: _LocalActionState | None = None,
        adapter_config: LocalRuntimeAdapterConfig | None = None,
        foreign_hook_handlers: HookHandlers | None = None,
        release_plugins: Callable[[], Awaitable[None]] | None = None,
        read_plugin_info: Callable[[], Awaitable[PluginInfo]] | None = None,
    ) -> None:
        self._session_id = session_id
        self._created_at = created_at
        self._cwd = cwd
        self._on_reconfigure_subagents = on_reconfigure_subagents
        self._action_adapter = action_adapter
        # Kept apart from the merged set inside ``action_adapter``: a child Session
        # inherits this Session's ``hook_bindings``, so it has to re-merge the same
        # foreign handlers against the Host's own builtins (see
        # ``UnifiedHarnessSessionBackendHost.create_or_restore_child``).
        self._foreign_hook_handlers = foreign_hook_handlers or HookHandlers()
        # Session-owned and config-independent: the attachments directory has to
        # survive every ``apply_adapter_config``, which only carries the
        # workspace roots.
        self._attachments_root = (
            attachments_root.expanduser().resolve() if attachments_root is not None else None
        )
        self._image_source_roots = self._resolve_image_source_roots(image_source_roots)
        self._turn_queue = SessionTurnQueue()
        self._state = state or PublicSessionState(
            session=PublicSession(
                id=session_id,
                status=IdleSessionStatus(),
                created_at=created_at,
                updated_at=created_at,
            ),
            turn_queue=self._turn_queue.state,
        )
        self._watermark = watermark
        self._local_terminal_state_watermark: int | None = None
        self._event_id = watermark
        self._retrying: PublicRetryState | None = None
        self._lease = lease
        self._runtime = runtime
        self._mcp_runtime = mcp_runtime
        self._connector_runtime = connector_runtime
        self._pending_capabilities: RustHarnessCapabilitySet | None = None
        self._pending_settings: RustHarnessSettings | None = None
        self._pending_system_instructions: str | None = None
        self._pending_adapter_config: LocalRuntimeAdapterConfig | None = None
        self._on_shutdown = on_shutdown
        self._on_work_state_changed = on_work_state_changed
        self._discard_on_shutdown = discard_on_shutdown
        self._promote_on_start = promote_on_start
        self._initialize_subagents_on_promote = initialize_subagents_on_promote
        self._pending_plugin_binding = pending_plugin_binding
        self._session_metadata = session_metadata
        self._release_plugins = release_plugins
        self._read_plugin_info = read_plugin_info
        self._closed = False
        # App Server requests run concurrently. Keep direct-turn admission,
        # queue mutation, promotion, and lazy Runtime creation in one sequence.
        self._lifecycle_lock = asyncio.Lock()
        self._promotion_conflict_id: str | None = None
        # The turn this session reserved, held until Core accepts it or it ends.
        self._reserved_turn_id: str | None = None
        self._reserved_turn_started_at: int | None = None
        self._reserved_turn_task: asyncio.Task[None] | None = None
        self._active_queue_item_id: str | None = None
        self._active_turn_task: asyncio.Task[None] | None = None
        self._turn_tasks: set[asyncio.Task[None]] = set()
        self._turn_task_ids: dict[asyncio.Task[None], str] = {}
        self._settling_turn_tasks: set[asyncio.Task[None]] = set()
        self._queue_drain_task: asyncio.Task[None] | None = None
        self._settle_configuration: Callable[[], Awaitable[None]] | None = None
        self._last_queue_terminal_turn_id: str | None = None
        self._turn_terminal_events: dict[str, asyncio.Event] = {}
        self._title_task: asyncio.Task[None] | None = None
        self._title_config = adapter_config
        # What the transcript reflects. A resumed session reopens on the model it
        # was left on, which must not read as a change.
        self._active_model = None if adapter_config is None else adapter_config.active_model.model
        self._title_cadence = TitleCadence()
        self._title_cadence_seeded = False
        self._approval_waiters: dict[str, asyncio.Future[ApprovalGrant]] = {}
        self._user_input_waiters: dict[str, asyncio.Future[tuple[bool, JsonValue]]] = {}
        self._open_callbacks: dict[str, JsonObject] = {}
        self._callback_results: dict[str, JsonValue] = {}
        self._closed_callback_ids: set[str] = set()
        self._event_subscriptions = SessionEventSubscriptions()
        self._event_observers: dict[str, Callable[[JsonObject], None]] = {}
        if runtime is not None and on_work_state_changed is not None:
            runtime.configure_work_state_callback(self._notify_work_state_changed)
        self._buffered_events: list[JsonObject] | None = None

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def parent_session_id(self) -> str | None:
        return self._state.session.parent_session_id

    @property
    def cwd(self) -> str | None:
        return self._cwd

    @property
    def foreign_hook_handlers(self) -> HookHandlers:
        return self._foreign_hook_handlers

    def pin(self, pin: SessionPin) -> str | None:
        """The value stored for a session pin (see ``SessionPin``)."""
        if self._session_metadata is not None:
            return self._session_metadata.pin(pin)
        if self._runtime is not None:
            return self._runtime.runtime_state.session_metadata.pin(pin)
        return None

    async def persist_pin(self, pin: SessionPin, value: str) -> bool:
        """Store a changed session pin value and report whether storage was updated."""
        async with self._lifecycle_lock:
            if self.pin(pin) == value:
                return False
            if self._session_metadata is not None:
                # Promotion closes over this object, so mutate it in place.
                setattr(self._session_metadata, pin.value, value)
            runtime = self._runtime
            if runtime is None:
                return True

            def update(state: RuntimeStateV3) -> RuntimeStateV3:
                metadata = state.session_metadata.model_copy(update={pin.value: value})
                return state.model_copy(update={"session_metadata": metadata})

            await runtime.update_runtime_state(update)
            return True

    async def persist_cwd(self, cwd: str) -> bool:
        async with self._lifecycle_lock:
            if self._cwd == cwd:
                return False
            self._cwd = cwd
            if self._session_metadata is not None:
                self._session_metadata.cwd = cwd
            runtime = self._runtime
            if runtime is None:
                return True

            def update(state: RuntimeStateV3) -> RuntimeStateV3:
                metadata = state.session_metadata.model_copy(update={"cwd": cwd})
                return state.model_copy(update={"session_metadata": metadata})

            await runtime.update_runtime_state(update)
            return True

    @property
    def turn_queue(self) -> TurnQueue:
        return self._turn_queue.state

    @property
    def ephemeral(self) -> bool:
        return self._discard_on_shutdown is not None

    def _unsaved_fork_identity(self) -> tuple[SessionMetadataV1, PluginLockV1] | None:
        """In-memory identity for a live session that has not been persisted yet."""
        if not self.ephemeral or self._session_metadata is None:
            return None
        binding = self._pending_plugin_binding or empty_plugin_binding()
        return self._session_metadata, binding.lock

    @property
    def active_turn_id(self) -> str | None:
        """The turn Core is running, else one reserved for execution.

        Core owns the turn lifecycle and a turn outlives the task that started it,
        so a finished task never means a finished turn.
        """
        active = self._running_or_scheduled_turn_id()
        if active is not None:
            return active
        if self._active_turn_task is not None:
            return None
        return self._reserved_turn_id

    def _running_or_scheduled_turn_id(self) -> str | None:
        runtime = self._runtime
        accepted = runtime.inspection.active_turn_id if runtime is not None else None
        if accepted is not None:
            return accepted
        scheduled = self._active_turn_task
        if scheduled is None or scheduled.done():
            return None
        return self._reserved_turn_id

    @property
    def _has_active_work(self) -> bool:
        if self._reserved_turn_id is not None or self._turn_queue:
            return True
        if self._queue_drain_task is not None and not self._queue_drain_task.done():
            return True
        if self._title_task is not None and not self._title_task.done():
            return True
        if any(not task.done() for task in self._turn_tasks):
            return True
        return self._runtime is not None and self._runtime.has_active_process_work

    def configure_turn_settlement(self, settle: Callable[[], Awaitable[None]]) -> None:
        """Register what the Host must apply before any turn opens."""
        self._settle_configuration = settle

    def _configure_work_state_callback(
        self, callback: Callable[[str], Awaitable[None] | None]
    ) -> None:
        self._on_work_state_changed = callback
        if self._runtime is not None:
            self._runtime.configure_work_state_callback(self._notify_work_state_changed)

    @property
    def adapter_config(self) -> LocalRuntimeAdapterConfig | None:
        """The last configuration applied, which promotion resolves against."""
        return self._title_config

    def configuration_over(self, config: RustHarnessConfig) -> RustHarnessConfig:
        """`config`, carrying whatever this session has been told since.

        A session that has not promoted holds its settings and capabilities
        rather than pushing them, so a caller resolving anything against the
        configuration it was created with would be a move behind.
        """
        update: dict[str, object] = {}
        if self._pending_settings is not None:
            update["settings"] = self._pending_settings
        if self._pending_capabilities is not None:
            update["capabilities"] = self._pending_capabilities
        if self._pending_system_instructions is not None:
            update["system_instructions"] = self._pending_system_instructions
        if not update:
            return config
        return config.model_copy(update=update, deep=True)

    def apply_adapter_config(self, adapter_config: LocalRuntimeAdapterConfig) -> None:
        if self._action_adapter is None:
            self._raise_not_implemented("apply_adapter_config")
        self._action_adapter.configure(adapter_config)
        self._title_config = adapter_config
        if self._runtime is None:
            self._pending_adapter_config = adapter_config
        else:
            self._runtime.configure_process_config(adapter_config)
        self._image_source_roots = self._resolve_image_source_roots(
            adapter_config.workspace_roots or None
        )

    async def reconfigure_subagents(self, adapter_config: LocalRuntimeAdapterConfig) -> None:
        """Propagate an adapter config change to subagent bindings and children.

        Safe to call mid-turn: this only touches subagent state, not the
        parent's running turn.
        """
        if self._on_reconfigure_subagents is not None:
            try:
                await self._on_reconfigure_subagents(adapter_config)
            except Exception:
                logger.warning(
                    "Failed to reconfigure subagents after adapter update",
                    exc_info=True,
                )

    async def apply_capabilities(
        self,
        capabilities: RustHarnessCapabilitySet,
        *,
        plugins: tuple[RustPluginContextDefinition, ...] | None = None,
    ) -> None:
        runtime = self._runtime
        if runtime is None:
            self._pending_capabilities = capabilities
            return
        await runtime.reconfigure_skills(capabilities.skills)
        if plugins is not None:
            await runtime.reconfigure_plugins(plugins)

    async def apply_runtime_configuration(
        self,
        settings: RustHarnessSettings,
        adapter_config: LocalRuntimeAdapterConfig,
        capabilities: RustHarnessCapabilitySet,
        *,
        system_instructions: str | None = None,
        plugins: tuple[RustPluginContextDefinition, ...] | None = None,
        allow_reserved_turn: bool = False,
    ) -> None:
        async with self._lifecycle_lock:
            self._reject_compaction()
            if not allow_reserved_turn or self._running_or_scheduled_turn_id() is not None:
                self._reject_active_turn()
            runtime = self._runtime
            if runtime is None:
                self._pending_settings = settings
                self._pending_capabilities = capabilities
                # Core instructions cannot be replaced after creation, so an
                # ephemeral session retains them for its first promotion.
                if system_instructions is not None:
                    self._pending_system_instructions = system_instructions
                self.apply_adapter_config(adapter_config)
                # Nothing to record into yet, but this is the model the session
                # now runs: leaving the tracker behind would report the move
                # again once a runtime exists.
                self._active_model = adapter_config.active_model.model
                if self._on_reconfigure_subagents is not None:
                    try:
                        await self._on_reconfigure_subagents(adapter_config)
                    except Exception:
                        logger.warning(
                            "Failed to reconfigure subagents after runtime update",
                            exc_info=True,
                        )
                return
            await runtime.reconfigure_settings(settings)
            self.apply_adapter_config(adapter_config)
            # Both guards above mean this only runs between turns, which is when
            # the new model actually takes over, so the transcript entry lands in
            # the right place without anyone having to time it. A session opened
            # without a configuration has no model to have moved from.
            model = adapter_config.active_model.model
            if self._active_model is not None and model != self._active_model:
                await runtime.record_model_change(model)
            self._active_model = model
            await runtime.reconfigure_skills(capabilities.skills)
            if plugins is not None:
                await runtime.reconfigure_plugins(plugins)
            if self._on_reconfigure_subagents is not None:
                try:
                    await self._on_reconfigure_subagents(adapter_config)
                except Exception:
                    logger.warning(
                        "Failed to reconfigure subagents after runtime update",
                        exc_info=True,
                    )

    def _resolve_image_source_roots(self, roots: tuple[Path, ...] | None) -> tuple[Path, ...]:
        workspace = _image_source_roots(self._cwd, roots)
        if self._attachments_root is None or self._attachments_root in workspace:
            return workspace
        return workspace + (self._attachments_root,)

    async def read(self, params: SessionReadParams) -> SessionReadResult:
        return SessionReadResult(snapshot=self._snapshot(params.history_limit))

    async def subscribe(self, params: SessionReadParams) -> HarnessSessionSubscription:
        # Snapshot creation and subscriber registration contain no await, so a
        # Runtime event cannot land between them on the Session event loop.
        snapshot = self._snapshot(params.history_limit)
        return HarnessSessionSubscription(
            snapshot=snapshot,
            events=self._event_subscriptions.subscribe(),
        )

    async def rename(self, title: str) -> SessionSnapshot:
        async with self._lifecycle_lock:
            # Stop any background title already generating so its write cannot
            # land after this manual rename and clobber it.
            await self._cancel_title_task()
            observed_at = _now_milliseconds()
            if self._runtime is None:
                self._state = renamed_session_state(self._state, title, observed_at=observed_at)
                self._publish_session_state_update()
            else:
                await self._runtime.rename_session(title, observed_at=observed_at)
            return self._snapshot(0)

    def observe_events(
        self, observer_id: str, observer: Callable[[JsonObject], None]
    ) -> SessionSnapshot:
        """Atomically attach a non-consuming event observer and return its watermark."""
        self._event_observers[observer_id] = observer
        return self._snapshot(500)

    def stop_observing_events(self, observer_id: str) -> None:
        self._event_observers.pop(observer_id, None)

    def publish_child_event(self, child_session_id: str, event: JsonObject) -> None:
        """Publish a child event on the already-bound parent subscription."""
        self._publish_event(
            {
                "type": "child_session_event",
                "sessionId": child_session_id,
                "event": event,
            }
        )

    def publish_child_registration(self, child_session_id: str, snapshot: SessionSnapshot) -> None:
        self._publish_event(
            {
                "type": "child_session_registered",
                "sessionId": child_session_id,
                "snapshot": snapshot.model_dump(mode="json", by_alias=True),
            }
        )

    def guard_request(self) -> None:
        # Requests perform their own operation-specific conflict checks. In
        # particular, shutdown must remain available while deferred events are
        # waiting for a response callback.
        return None

    def _require_session(self, session_id: str) -> None:
        if session_id != self._session_id:
            raise ValueError(f"Session not found: {session_id}")

    async def _wait_for_pending_turns(self) -> None:
        reserved = self._reserved_turn_task
        tasks = tuple(task for task in self._turn_tasks if not task.done()) + (
            (reserved,) if reserved is not None and not reserved.done() else ()
        )
        if tasks:
            await asyncio.gather(*tasks)

    def open_callbacks(self) -> tuple[JsonObject, ...]:
        return tuple(self._open_callbacks.values())

    async def recover(self) -> None:
        if self._runtime is not None:
            await self._runtime.recover()
            self._watermark = max(self._watermark, self._runtime.watermark)
            self._event_id = max(self._event_id, self._watermark)

    async def read_plugin_info(self) -> PluginInfo:
        """Ask the plugin port what this session has bound.

        Routed through the session rather than read off the provider directly
        so that the catalogue a client is told about is the one this session
        bound, not the Host-wide resolve the request was authored from. A
        Runtime with no provider configured has no plugins, and an empty
        catalogue is the honest answer rather than an error.
        """
        if self._read_plugin_info is None:
            return PluginInfo()
        return await self._read_plugin_info()

    async def read_mcp(self) -> MCPRouteSnapshot:
        runtime = self._require_mcp_runtime()
        return runtime.snapshot

    async def reconfigure_mcp(
        self,
        configuration: ResolvedMCPCatalog,
        *,
        force_remote_discovery: bool,
        push_to_core: bool = True,
    ) -> MCPRouteSnapshot:
        self._reject_active_turn()
        return await self._require_mcp_runtime().reconfigure(
            configuration,
            force_remote_discovery=force_remote_discovery,
            push_to_core=push_to_core,
        )

    async def push_mcp_capabilities(self) -> None:
        await self._require_mcp_runtime().push_snapshot_to_core()

    async def authorization_changed(
        self, *, name: str, descriptor_revision: str
    ) -> MCPRouteSnapshot:
        self._reject_active_turn()
        return await self._require_mcp_runtime().authorization_changed(
            name=name, descriptor_revision=descriptor_revision
        )

    async def suspend_mcp(self, *, name: str, tool_name: str | None) -> MCPRouteSnapshot:
        self._reject_active_turn()
        return await self._require_mcp_runtime().suspend(name=name, tool_name=tool_name)

    async def read_connectors(self) -> ConnectorRouteSnapshot:
        return self._require_connector_runtime().snapshot

    async def reconfigure_connectors(
        self,
        catalog: ResolvedConnectorCatalog,
        selection: ResolvedConnectorSelection,
        *,
        push_to_core: bool = True,
    ) -> ConnectorRouteSnapshot:
        self._reject_active_turn()
        return await self._require_connector_runtime().reconfigure(
            catalog, selection, push_to_core=push_to_core
        )

    async def push_connector_capabilities(self) -> None:
        await self._require_connector_runtime().push_snapshot_to_core()

    async def suspend_connectors(
        self, *, alias: str, tool_name: str | None
    ) -> ConnectorRouteSnapshot:
        self._reject_active_turn()
        return await self._require_connector_runtime().suspend(alias=alias, tool_name=tool_name)

    async def switch_agent(self, params: object) -> Never:
        self._raise_not_implemented("switch_agent")

    async def update_settings(self, params: object) -> Never:
        self._raise_not_implemented("update_settings")

    async def write_config(self, params: object) -> Never:
        self._raise_not_implemented("write_config")

    async def reload_config(self, params: object) -> Never:
        self._raise_not_implemented("reload_config")

    async def start_turn(self, params: object) -> object:
        await self._settle_turn_configuration()
        async with self._lifecycle_lock:
            return await self._start_turn(params)

    async def start_deferred_turn(
        self,
        params: DeferredTurnStartParams,
    ) -> DeferredTurnStartResult:
        """Accept a turn before slow workspace preparation finishes.

        The response reserves the turn exactly like ``start_turn``. Its
        after-response hook publishes the user message and preparation steps,
        then prepares and promotes the Runtime in the background. This keeps a
        newly created session visible while guaranteeing that no model or tool
        work starts against the pre-preparation cwd.
        """
        async with self._lifecycle_lock:
            self._require_session(params.session_id)
            if params.turn.session_id != params.session_id:
                raise ValueError("Deferred Turn session IDs must match")
            self._reject_compaction()
            self._reject_active_turn()
            if self._turn_queue:
                raise HarnessTurnQueuePendingError()
            if self._runtime is not None:
                raise RuntimeError("Deferred turn preparation requires an ephemeral session")
            payload = _deferred_turn_start_payload(
                params.turn, image_source_roots=self._image_source_roots
            )
            started_at = _now_milliseconds()
            preparation_context = DeferredTurnPreparationContext(
                session_id=self._session_id,
                turn_id=payload.turn_id,
                started_at=started_at,
            )
            self._reserved_turn_id = payload.turn_id
            self._reserved_turn_started_at = started_at

            def after_response() -> None:
                if self._reserved_turn_id != payload.turn_id:
                    return
                accepted_entries = self._publish_reserved_turn_started(
                    payload,
                    started_at=started_at,
                    pending_history_entries=params.prepare.pending_history_entries,
                )
                task = asyncio.create_task(
                    self._finish_deferred_turn(
                        payload,
                        preparation=params.prepare,
                        preparation_context=preparation_context,
                        accepted_history_entries=accepted_entries,
                    )
                )
                self._reserved_turn_task = task
                task.add_done_callback(self._clear_reserved_turn_task)

            def on_response_abandoned() -> None:
                if self._reserved_turn_id != payload.turn_id:
                    return
                self._reserved_turn_id = None
                self._reserved_turn_started_at = None
                self._schedule_turn_queue_drain()

            return DeferredTurnStartResult(
                turn_id=payload.turn_id,
                session_id=self._session_id,
                started_at=started_at,
                last_event_id=self._current_event_id(),
                after_response=after_response,
                on_response_abandoned=on_response_abandoned,
            )

    async def start_scheduled_turn(self, params: object, loop_id: str) -> object:
        await self._settle_turn_configuration()
        async with self._lifecycle_lock:
            return await self._start_turn(params, scheduled_loop_id=loop_id)

    async def _start_turn(self, params: object, *, scheduled_loop_id: str | None = None) -> object:
        self._reject_compaction()
        self._reject_active_turn()
        if self._turn_queue:
            raise HarnessTurnQueuePendingError()
        payload = _turn_start_payload(params, image_source_roots=self._image_source_roots)
        runtime = await self._runtime_for_turn_locked(promotion_conflict_id=payload.turn_id)
        started_at = _now_milliseconds()
        self._reserved_turn_id = payload.turn_id
        self._reserved_turn_started_at = started_at
        scheduled_notice = (
            _scheduled_loop_notice(self._session_id, payload.turn_id, scheduled_loop_id)
            if scheduled_loop_id is not None
            else None
        )

        def after_response() -> None:
            if self._reserved_turn_id != payload.turn_id:
                return
            self._reserved_turn_started_at = None
            self._schedule_turn(
                runtime,
                payload,
                accepted_public_history_entries=(
                    (scheduled_notice,) if scheduled_notice is not None else ()
                ),
            )

        def on_response_abandoned() -> None:
            if self._reserved_turn_id != payload.turn_id:
                return
            self._reserved_turn_id = None
            self._reserved_turn_started_at = None
            self._schedule_turn_queue_drain()

        return _SessionBackendResult(
            response=_TurnStartResponse(
                turn=_PublicTurn(
                    id=payload.turn_id,
                    session_id=self._session_id,
                    status="in_progress",
                    started_at=started_at,
                ),
                last_event_id=self._current_event_id(),
            ),
            after_response=after_response,
            on_response_abandoned=on_response_abandoned,
        )

    def _publish_reserved_turn_started(
        self,
        payload: "_TurnStartPayload",
        *,
        started_at: int,
        pending_history_entries: tuple[PublicHistoryEntry, ...],
    ) -> tuple[JsonObject, ...]:
        pending = self._bind_turn_history_entries(
            pending_history_entries,
            turn_id=payload.turn_id,
            observed_at=started_at,
        )
        # A host-injected Turn stays model-visible but never public. Its user
        # entry is dropped here rather than only in the projection, because
        # accepted entries are replayed into the projection after promotion and
        # would carry the injected prompt back into public history.
        if content_is_injected(payload.content):
            accepted_entries = pending
        else:
            user_entry = public_message_entry(
                self._session_id,
                payload.turn_id,
                f"user-{payload.turn_id}-{payload.client_command_id}",
                "user",
                payload.content,
                started_at,
                "turn_start",
            )
            accepted_entries = (user_entry, *pending)
        history = self._state.history.model_copy(
            update={"entries": [*self._state.history.entries, *accepted_entries]}
        )
        preview = self._state.session.preview or first_user_message_preview(history.entries)
        self._state = self._state.model_copy(
            update={
                "session": self._state.session.model_copy(
                    update={
                        "status": RunningSessionStatus(active_turn_id=payload.turn_id),
                        "updated_at": started_at,
                        "preview": preview,
                    }
                ),
                "history": history,
                "latest_turn": InProgressPublicTurn(
                    id=payload.turn_id,
                    session_id=self._session_id,
                    started_at=started_at,
                ),
            }
        )
        self._publish_session_state_update()
        return accepted_entries

    async def _finish_deferred_turn(
        self,
        reserved_payload: "_TurnStartPayload",
        *,
        preparation: DeferredTurnPreparation,
        preparation_context: DeferredTurnPreparationContext,
        accepted_history_entries: tuple[JsonObject, ...],
    ) -> None:
        try:
            result = await preparation.run(preparation_context)
        except asyncio.CancelledError:
            return
        except DeferredTurnPreparationError as exc:
            async with self._lifecycle_lock:
                if self._reserved_turn_id != reserved_payload.turn_id:
                    return
                self._replace_turn_history_entries(
                    exc.history_entries,
                    turn_id=reserved_payload.turn_id,
                )
                self._reserved_turn_id = None
                self._reserved_turn_started_at = None
                self._publish_failed_turn(reserved_payload.turn_id, exc.error)
            return
        except Exception as exc:
            async with self._lifecycle_lock:
                if self._reserved_turn_id != reserved_payload.turn_id:
                    return
                self._reserved_turn_id = None
                self._reserved_turn_started_at = None
                self._publish_failed_turn(reserved_payload.turn_id, exc)
            return

        async with self._lifecycle_lock:
            if self._reserved_turn_id != reserved_payload.turn_id:
                return
            try:
                payload = _deferred_turn_start_payload(
                    result.runtime_input.turn,
                    image_source_roots=self._image_source_roots,
                )
                payload = _TurnStartPayload(
                    turn_id=reserved_payload.turn_id,
                    content=payload.content,
                    client_command_id=reserved_payload.client_command_id,
                    params=payload.params,
                )
                if result.history_entries:
                    replacements = self._replace_turn_history_entries(
                        result.history_entries,
                        turn_id=payload.turn_id,
                    )
                    replacement_by_id = {
                        cast(str, entry.get("id")): entry for entry in replacements
                    }
                    if replacement_by_id:
                        accepted_history_entries = tuple(
                            replacement_by_id.get(cast(str, entry.get("id")), entry)
                            for entry in accepted_history_entries
                        )
                runtime = await self._runtime_for_turn_locked(promotion_conflict_id=payload.turn_id)
                if result.after_promotion is not None:
                    await result.after_promotion()
            except Exception as exc:
                self._reserved_turn_id = None
                self._reserved_turn_started_at = None
                self._publish_failed_turn(reserved_payload.turn_id, exc)
                return
            self._reserved_turn_id = None
            self._reserved_turn_started_at = None
            self._schedule_turn(
                runtime,
                payload,
                accepted_public_history_entries=accepted_history_entries,
            )

    def _bind_turn_history_entries(
        self,
        entries: tuple[PublicHistoryEntry, ...],
        *,
        turn_id: str,
        observed_at: int,
    ) -> tuple[JsonObject, ...]:
        return tuple(
            cast(
                JsonObject,
                entry.model_copy(
                    update={
                        "session_id": self._session_id,
                        "turn_id": turn_id,
                        "created_at": observed_at,
                        "updated_at": observed_at,
                    }
                ).model_dump(mode="json", by_alias=True),
            )
            for entry in entries
        )

    def _replace_turn_history_entries(
        self, entries: tuple[PublicHistoryEntry, ...], *, turn_id: str
    ) -> tuple[JsonObject, ...]:
        if not entries:
            return ()
        now = _now_milliseconds()
        replacements = self._bind_turn_history_entries(entries, turn_id=turn_id, observed_at=now)
        by_id = {
            cast(str, entry.get("id")): entry
            for entry in replacements
            if isinstance(entry.get("id"), str)
        }
        history_entries = [
            (
                {
                    **by_id[entry_id],
                    "createdAt": entry.get("createdAt", now),
                }
                if isinstance(entry_id := entry.get("id"), str) and entry_id in by_id
                else entry
            )
            for entry in self._state.history.entries
        ]
        self._state = self._state.model_copy(
            update={
                "session": self._state.session.model_copy(update={"updated_at": now}),
                "history": self._state.history.model_copy(update={"entries": history_entries}),
            }
        )
        self._publish_session_state_update()
        return replacements

    def _clear_reserved_turn_task(self, task: asyncio.Task[None]) -> None:
        if task is self._reserved_turn_task:
            self._reserved_turn_task = None
        if not task.cancelled() and (exception := task.exception()) is not None:
            logger.exception("Deferred turn task failed", exc_info=exception)

    async def enqueue_turn(self, params: TurnEnqueueParams) -> object:
        self._require_session(params.session_id)
        prepared_entries = _prepare_turn_entries(
            params.entries, image_source_roots=self._image_source_roots
        )
        async with self._lifecycle_lock:
            self._reject_compaction()
            if self._reserved_turn_id is None:
                await self._runtime_for_turn_locked()
            result = self._turn_queue.enqueue(
                params,
                prepared_entries,
                created_at=_now_milliseconds(),
            )

        def after_response() -> None:
            self._publish_turn_queue_updated()
            self._schedule_turn_queue_drain()

        return _SessionBackendResult(
            response=TurnEnqueueResponse(queue_item_id=result.record.queued_turn.id),
            after_response=None if result.duplicate else after_response,
        )

    async def replace_queued_turn(self, params: TurnQueueReplaceParams) -> object:
        self._require_session(params.session_id)
        prepared_entries = _prepare_turn_entries(
            params.entries, image_source_roots=self._image_source_roots
        )
        enqueue_params = TurnEnqueueParams(
            idempotency_key=params.idempotency_key,
            session_id=params.session_id,
            entries=params.entries,
        )
        async with self._lifecycle_lock:
            self._reject_compaction()
            if self._reserved_turn_id is None:
                await self._runtime_for_turn_locked()
            result = self._turn_queue.replace(
                params.queue_item_id, enqueue_params, prepared_entries
            )

        def after_response() -> None:
            self._publish_turn_queue_updated()
            self._schedule_turn_queue_drain()

        return _SessionBackendResult(
            response=TurnQueueReplaceResponse(queue_item_id=result.record.queued_turn.id),
            after_response=None if result.duplicate else after_response,
        )

    async def read_turn_queue(self, params: TurnQueueReadParams) -> object:
        self._require_session(params.session_id)
        async with self._lifecycle_lock:
            return _SessionBackendResult(response=TurnQueueReadResponse(queue=self.turn_queue))

    async def steer_queued_turn(self, params: TurnQueueSteerParams) -> object:
        self._require_session(params.session_id)
        command_id = f"queue-steer:{params.queue_item_id}"
        async with self._lifecycle_lock:
            receipt = self._turn_queue.steer_receipt(params.queue_item_id)
            if receipt is not None:
                if receipt.turn_id != params.expected_turn_id:
                    raise HarnessCommandConflictError(command_id)
                return _SessionBackendResult(
                    response=TurnQueueSteerResponse(
                        queue_item_id=receipt.queue_item_id,
                        turn_id=receipt.turn_id,
                    )
                )

            self._reject_compaction()
            active_turn_id = self.active_turn_id
            if active_turn_id != params.expected_turn_id:
                raise HarnessStaleTurnError(active_turn_id)
            runtime = self._runtime
            if runtime is None:
                self._raise_not_implemented("steer_queued_turn")
            record = self._turn_queue.require_record(params.queue_item_id)
            content = _queued_steer_content(record)
            command = RustUserMessageEvent(
                turn_id=params.expected_turn_id,
                content=content,
                mode="steer",
            )
            dumped = params.model_dump(mode="json", by_alias=True)
            # Once submission starts, finish its durable outcome even if the RPC
            # caller disconnects. Retirement and publication below contain no await.
            submission = asyncio.create_task(
                runtime.command_without_driving_actions(
                    client_command_id=command_id,
                    method="turn/queue/steer",
                    params=cast(dict[str, JsonValue], dumped),
                    command=command,
                    response_factory=lambda _transition: {
                        "queue_item_id": params.queue_item_id,
                        "turn_id": params.expected_turn_id,
                    },
                ),
                name=f"harness-queued-steer:{params.queue_item_id}",
            )
            cancellation: asyncio.CancelledError | None = None
            while not submission.done():
                try:
                    await asyncio.shield(submission)
                except asyncio.CancelledError as error:
                    cancellation = error
            submission.result()

            receipt = self._turn_queue.retire_steered(
                record,
                turn_id=params.expected_turn_id,
            )
            self._publish_turn_queue_updated()
            response = _SessionBackendResult(
                response=TurnQueueSteerResponse(
                    queue_item_id=receipt.queue_item_id,
                    turn_id=receipt.turn_id,
                )
            )
            if cancellation is not None:
                raise cancellation
            return response

    async def remove_queued_turn(self, params: TurnQueueRemoveParams) -> object:
        self._require_session(params.session_id)
        async with self._lifecycle_lock:
            self._reject_compaction()
            removed = self._turn_queue.remove(params.queue_item_id)

        def after_response() -> None:
            self._publish_turn_queue_updated()
            self._schedule_turn_queue_drain()

        return _SessionBackendResult(
            response=TurnQueueRemoveResponse(),
            after_response=after_response if removed else None,
        )

    async def resume_turn_queue(self, params: TurnQueueResumeParams) -> object:
        self._require_session(params.session_id)
        async with self._lifecycle_lock:
            self._reject_compaction()
            resumed = self._turn_queue.resume()

        def after_response() -> None:
            self._publish_turn_queue_updated()
            self._schedule_turn_queue_drain()

        return _SessionBackendResult(
            response=TurnQueueResumeResponse(),
            after_response=after_response if resumed else None,
        )

    async def _runtime_for_turn_locked(
        self,
        *,
        promotion_conflict_id: str | None = None,
        operation: str = "start_turn",
    ) -> DurableSessionRuntime:
        runtime = self._runtime
        if runtime is None and self._promote_on_start is not None:
            self._promotion_conflict_id = promotion_conflict_id
            binding = self._pending_plugin_binding or empty_plugin_binding()
            try:
                runtime = await asyncio.to_thread(self._promote_on_start, binding)
                self._runtime = runtime
                if self._on_work_state_changed is not None:
                    runtime.configure_work_state_callback(self._notify_work_state_changed)
                self._promote_on_start = None
                self._pending_plugin_binding = None
                pending, self._pending_capabilities = self._pending_capabilities, None
                if pending is not None:
                    await runtime.reconfigure_skills(pending.skills)
                pending_settings, self._pending_settings = self._pending_settings, None
                if pending_settings is not None:
                    await runtime.reconfigure_settings(pending_settings)
                self._pending_system_instructions = None
                pending_process = self._pending_adapter_config
                self._pending_adapter_config = None
                if pending_process is not None:
                    runtime.configure_process_config(pending_process)
                initialize_subagents = self._initialize_subagents_on_promote
                if initialize_subagents is not None:
                    await initialize_subagents(runtime, binding)
                    self._initialize_subagents_on_promote = None
            finally:
                self._promotion_conflict_id = None
        if runtime is None:
            self._raise_not_implemented(operation)
        self._discard_on_shutdown = None
        return runtime

    async def steer_turn(self, params: object) -> object:
        raw = cast(Any, params)
        _required_str(raw, "session_id")
        expected_turn_id = _required_str(raw, "expected_turn_id")
        active_turn_id = self.active_turn_id
        if active_turn_id != expected_turn_id:
            raise HarnessStaleTurnError(active_turn_id)
        runtime = self._runtime
        if runtime is None:
            self._raise_not_implemented("steer_turn")
        content = _rust_content_blocks(
            raw.message,
            getattr(raw, "client_user_message_id", None),
            image_source_roots=self._image_source_roots,
        )
        user_display_content = getattr(raw, "user_display_content", None)
        if user_display_content is not None:
            content = _with_content_meta(
                content,
                {
                    "vibe.userDisplayContent": user_display_content.model_dump(
                        mode="json", by_alias=True
                    )
                },
            )
        if not content:
            raise ValueError("Unified Harness turn/steer currently supports text input only")
        command = RustUserMessageEvent(
            turn_id=expected_turn_id,
            content=content,
            mode="steer",
        )
        dumped = raw.model_dump(mode="json", by_alias=True, exclude_none=True)
        result = await runtime.command_without_driving_actions(
            client_command_id=getattr(raw, "idempotency_key", None)
            or f"steer-{secrets.token_hex(16)}",
            method="turn/steer",
            params=cast(dict[str, JsonValue], dumped),
            command=command,
            response_factory=lambda _transition: {
                "accepted": True,
                "last_event_id": self._current_event_id(),
            },
        )
        return _SessionBackendResult(response=result.response)

    async def interrupt_turn(self, params: object) -> object:
        raw = cast(Any, params)
        _required_str(raw, "session_id")
        expected_turn_id = _required_str(raw, "expected_turn_id")
        async with self._lifecycle_lock:
            active_turn_id = self.active_turn_id or self._reserved_turn_id
            if active_turn_id != expected_turn_id:
                raise HarnessStaleTurnError(active_turn_id)
            if self._running_or_scheduled_turn_id() is None:
                started_at = self._reserved_turn_started_at
                if started_at is None:
                    raise RuntimeError("Reserved turn has no start time")
                task = self._reserved_turn_task
                self._reserved_turn_task = None
                self._reserved_turn_id = None
                self._reserved_turn_started_at = None
                if task is not None and not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

                response_settled = False

                def settle_response() -> None:
                    nonlocal response_settled
                    if response_settled:
                        return
                    response_settled = True
                    self._publish_reserved_turn_interrupted(
                        expected_turn_id,
                        started_at=started_at,
                    )
                    if self._turn_queue.resume():
                        self._publish_turn_queue_updated()
                    self._schedule_turn_queue_drain()

                return _SessionBackendResult(
                    response={"accepted": True, "last_event_id": self._current_event_id()},
                    after_response=settle_response,
                    on_response_abandoned=settle_response,
                )
            runtime = self._runtime
            if runtime is None:
                self._raise_not_implemented("interrupt_turn")
            await self._cancel_active_work()
            await runtime.interrupt(expected_turn_id=expected_turn_id, reason="client interrupt")
        return _SessionBackendResult(
            response={"accepted": True, "last_event_id": self._current_event_id()}
        )

    async def inject_context(self, params: object) -> object:
        async with self._lifecycle_lock:
            return await self._inject_context(params)

    async def _inject_context(self, params: object) -> object:
        self._reject_compaction()
        self._reject_active_turn()
        raw = cast(Any, params)
        _required_str(raw, "session_id")
        content = _rust_content_blocks(
            raw.input,
            getattr(raw, "client_user_message_id", None),
            image_source_roots=self._image_source_roots,
        )
        if not content:
            raise ValueError("Unified Harness context injection requires content")
        runtime = await self._runtime_for_turn_locked(
            promotion_conflict_id="context-injection",
            operation="inject_context",
        )
        dumped = raw.model_dump(mode="json", by_alias=True, exclude_none=True)
        await runtime.command_without_driving_actions(
            client_command_id=f"context-{secrets.token_hex(16)}",
            method="session/context/inject",
            params=cast(dict[str, JsonValue], dumped),
            command=RustContextMessageEvent(content=content),
            response_factory=lambda _transition: {},
        )
        entries: list[JsonObject] = []
        if getattr(raw, "as_message", False):
            entry = public_message_entry(
                self._session_id,
                None,
                f"context-{secrets.token_hex(16)}",
                "user",
                content,
                _now_milliseconds(),
                "harness",
            )
            await runtime.append_public_history_entries([entry])
            entries.append(entry)
        return _SessionBackendResult(response={"entries": entries})

    async def respond_to_callback(self, params: object) -> object:
        raw = cast(Any, params)
        result = _field(raw, "result")
        callback_id = _required_str(result, "callback_id")
        serialized_result = _json_value(result)
        accepted_result = self._callback_results.get(callback_id)
        if accepted_result is not None:
            if accepted_result != serialized_result:
                raise HarnessCallbackConflictError(callback_id)
            return _SessionBackendResult(
                response={"accepted": True, "last_event_id": self._current_event_id()}
            )
        if callback_id in self._closed_callback_ids:
            raise HarnessCallbackClosedError(callback_id)
        approval_waiter = self._approval_waiters.get(callback_id)
        user_input_waiter = self._user_input_waiters.get(callback_id)
        decision_type = _approval_result_decision_type(result)
        grant = _approval_grant(decision_type)
        callback = self._open_callbacks.get(callback_id)
        if callback is None and approval_waiter is None and user_input_waiter is None:
            if callback_id in self._closed_callback_ids:
                raise HarnessCallbackClosedError(callback_id)
            raise HarnessCallbackNotFoundError(callback_id)
        runtime = self._runtime
        resolve_callback = getattr(runtime, "resolve_callback_durable", None)
        if resolve_callback is not None:
            raw_result = cast(Any, result)
            dumped_result = (
                raw_result.model_dump(mode="json", by_alias=True, exclude_none=True)
                if hasattr(raw_result, "model_dump")
                else result
            )
            await resolve_callback(
                callback_id,
                cast(JsonValue, dumped_result),
                failed=_field(result, "error", default=None) is not None,
            )
        self._approval_waiters.pop(callback_id, None)
        self._user_input_waiters.pop(callback_id, None)
        self._open_callbacks.pop(callback_id, None)
        self._callback_results[callback_id] = serialized_result
        if callback is not None:
            if _field(result, "error", default=None) is not None:
                self._closed_callback_ids.add(callback_id)
            self._publish_event(_callback_resolved_event(callback, result))
            self._publish_session_state_update()
        if approval_waiter is not None and not approval_waiter.done():
            approval_waiter.set_result(grant)
        if user_input_waiter is not None and not user_input_waiter.done():
            error = _field(result, "error", default=None)
            if error is not None:
                user_input_waiter.set_result((False, _json_value(error)))
            else:
                output = _field(result, "output")
                user_input_waiter.set_result((True, _json_value(_field(output, "result"))))
        if decision_type == "cancel_turn":
            await self._interrupt_active_turn_from_callback(callback)
        last_event_id = self._current_event_id()
        return _SessionBackendResult(response={"accepted": True, "last_event_id": last_event_id})

    async def compact(self, params: object) -> object:
        async with self._lifecycle_lock:
            return await self._compact(params)

    async def _compact(self, params: object) -> object:
        self._reject_active_turn()
        runtime = self._runtime
        if runtime is None:
            self._raise_not_implemented("compact")
        raw = cast(Any, params)
        _required_str(raw, "session_id")
        instructions = getattr(raw, "extra_instructions", "")
        if not isinstance(instructions, str):
            raise ValueError("compact extra instructions must be a string")
        if self._buffered_events is not None:
            raise HarnessTurnConflictError("context-compaction")
        self._buffered_events = []
        try:
            outcome = await runtime.compact_context(
                client_command_id=f"compact-{secrets.token_hex(16)}",
                instructions=instructions,
            )
        except BaseException:
            self._buffered_events = None
            raise
        buffered_events = tuple(self._buffered_events)

        def after_response() -> None:
            if self._buffered_events is None:
                return
            self._buffered_events = None
            for event in buffered_events:
                self._emit_event(event)

        def on_response_abandoned() -> None:
            if self._buffered_events is not None:
                self._buffered_events = None

        if isinstance(outcome, ContextCompactionFailure):
            raise HarnessContextCompactionError(
                outcome.error.code,
                f"Context compaction failed: {outcome.error.code}",
                details=outcome.error.details,
                after_response=after_response,
                on_response_abandoned=on_response_abandoned,
            )
        response = {
            "summary": outcome.summary,
            "state": runtime.projection.model_dump(mode="json", by_alias=True),
            "last_event_id": self._current_event_id(),
        }

        return _SessionBackendResult(
            response=response,
            after_response=after_response,
            on_response_abandoned=on_response_abandoned,
            buffered_events=buffered_events,
        )

    def _reject_compaction(self) -> None:
        if self._buffered_events is not None:
            raise HarnessTurnConflictError("context-compaction")

    def _runtime_for_host(self) -> DurableSessionRuntime:
        runtime = self._runtime
        if runtime is None:
            self._raise_not_implemented("child Runtime access")
        return runtime

    def _detach_lease_for_tree_deletion(self) -> SessionLease:
        lease = self._lease
        if lease is None:
            raise RuntimeError("Session has no attached lease for tree deletion")
        self._lease = None
        return lease

    def _state_for_host(self) -> PublicSessionState:
        state, _watermark = self._current_state_and_watermark()
        return state.model_copy(deep=True)

    async def _restore_child_execution(
        self,
        *,
        start_new_actions: bool,
        once_idle: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        # ``once_idle`` runs where Core has settled on the configuration this bind
        # restored: anything published before that is replaced by the deferred set
        # the settle adopts.
        runtime = self._runtime_for_host()
        inspection = runtime.inspection
        if inspection.status != "running" or inspection.active_turn_id is None:
            await runtime.adopt_restored_configuration()
            if once_idle is not None:
                await once_idle()
            return
        if self._active_turn_task is not None and not self._active_turn_task.done():
            return
        event = self._turn_terminal_events.setdefault(inspection.active_turn_id, asyncio.Event())
        if not start_new_actions:
            return

        async def run() -> None:
            await runtime.recover()
            if once_idle is not None:
                await once_idle()

        self._track_turn_task(inspection.active_turn_id, run(), event)

    async def _start_parent_turn(
        self,
        *,
        parent_session_id: str,
        message: str,
        target: SpawnTarget,
        operation_key: str,
    ) -> None:
        runtime = self._runtime_for_host()
        inspection = runtime.inspection
        if inspection.status == "running":
            if inspection.active_turn_id != target.command.turn_id:
                raise HarnessTurnConflictError(inspection.active_turn_id or "unknown")
            return
        command = RustUserMessageEvent(
            turn_id=target.command.turn_id,
            content=[RustTextContentBlock(text=message)],
            mode="queue",
        )
        result = await runtime.command_without_driving_actions(
            client_command_id=target.child_command_id,
            method="parent/turn/start",
            params={"message": message, "turn_id": target.command.turn_id},
            command=command,
            response_factory=lambda _transition: {},
        )
        await self._record_parent_command(parent_session_id, operation_key, target)
        if result.transition is not None and result.transition.actions:
            event = self._turn_terminal_events.setdefault(target.command.turn_id, asyncio.Event())
            self._track_turn_task(
                target.command.turn_id,
                runtime.drive_transition(result.transition),
                event,
            )

    async def _send_parent_message(
        self,
        *,
        parent_session_id: str,
        message: str,
        known_generation: ChildGenerationRef,
        operation_key: str,
    ) -> SendStartTarget | SendSteerTarget:
        runtime = self._runtime_for_host()
        inspection = runtime.inspection
        if inspection.status == "running":
            if inspection.active_turn_id != known_generation.turn_id:
                raise HarnessStaleTurnError(inspection.active_turn_id)
            target: SendStartTarget | SendSteerTarget = SendSteerTarget(
                command=known_generation,
                child_command_id=f"{operation_key}:steer",
            )
            command = RustUserMessageEvent(
                turn_id=known_generation.turn_id,
                content=[RustTextContentBlock(text=message)],
                mode="steer",
            )
            method = "parent/turn/steer"
        else:
            generation = known_generation.generation + 1
            command_ref = ChildGenerationRef(
                generation=generation,
                turn_id=f"{self._session_id}:turn:{generation}",
            )
            target = SendStartTarget(
                command=command_ref,
                child_command_id=f"{operation_key}:start",
            )
            command = RustUserMessageEvent(
                turn_id=command_ref.turn_id,
                content=[RustTextContentBlock(text=message)],
                mode="queue",
            )
            method = "parent/turn/start"
        result = await runtime.command_without_driving_actions(
            client_command_id=target.child_command_id,
            method=method,
            params={"message": message, "turn_id": target.command.turn_id},
            command=command,
            response_factory=lambda _transition: {},
        )
        await self._record_parent_command(parent_session_id, operation_key, target)
        if result.transition is not None and result.transition.actions:
            event = self._turn_terminal_events.setdefault(target.command.turn_id, asyncio.Event())
            self._track_turn_task(
                target.command.turn_id,
                runtime.drive_transition(result.transition),
                event,
            )
        return target

    async def _interrupt_parent_turn(
        self,
        *,
        parent_session_id: str,
        target: InterruptTarget | CloseRunningTarget,
        operation_key: str,
    ) -> None:
        runtime = self._runtime_for_host()
        inspection = runtime.inspection
        if inspection.status == "running":
            if inspection.active_turn_id != target.command.turn_id:
                raise HarnessStaleTurnError(inspection.active_turn_id)
            await self._cancel_active_work()
            await runtime.interrupt(
                expected_turn_id=target.command.turn_id,
                reason="interrupted by parent",
            )
        elif inspection.last_turn_id != target.command.turn_id:
            raise HarnessStaleTurnError(inspection.active_turn_id)
        await self._record_parent_command(parent_session_id, operation_key, target)
        self._turn_terminal_events.setdefault(target.command.turn_id, asyncio.Event()).set()

    async def _acknowledge_parent_command(self, operation_key: str) -> None:
        runtime = self._runtime_for_host()

        def acknowledge(state: RuntimeStateV3) -> RuntimeStateV3:
            receipts = dict(state.parent_command_receipts)
            if receipts.pop(operation_key, None) is None:
                return state
            return state.model_copy(
                update={"parent_command_receipts": dict(sorted(receipts.items()))}
            )

        await runtime.update_runtime_state(acknowledge)

    async def _wait_for_parent_turn(self, turn_id: str) -> PublicSessionState:
        state, _watermark = self._current_state_and_watermark()
        if (
            state.latest_turn is not None
            and state.latest_turn.id == turn_id
            and state.latest_turn.status != "in_progress"
        ):
            return state
        event = self._turn_terminal_events.setdefault(turn_id, asyncio.Event())
        await event.wait()
        state, _watermark = self._current_state_and_watermark()
        if state.latest_turn is None or state.latest_turn.id != turn_id:
            raise RuntimeError("child Turn terminated without a public outcome")
        return state

    async def _record_parent_command(
        self,
        parent_session_id: str,
        operation_key: str,
        target: SpawnTarget
        | SendStartTarget
        | SendSteerTarget
        | InterruptTarget
        | CloseRunningTarget,
    ) -> None:
        runtime = self._runtime_for_host()
        receipt = ParentOriginatedChildCommandReceipt(
            operation_key=operation_key,
            parent_session_id=parent_session_id,
            target=target,
            result=ChildCommandResult(turn_id=target.command.turn_id),
        )

        def record(state: RuntimeStateV3) -> RuntimeStateV3:
            existing = state.parent_command_receipts.get(operation_key)
            if existing is not None:
                if existing != receipt:
                    raise ValueError("parent command receipt conflicts with its replay")
                return state
            receipts = {**state.parent_command_receipts, operation_key: receipt}
            return state.model_copy(
                update={"parent_command_receipts": dict(sorted(receipts.items()))}
            )

        await runtime.update_runtime_state(record)

    def _track_turn_task(
        self,
        turn_id: str,
        awaitable: Awaitable[object],
        terminal_event: asyncio.Event | None = None,
    ) -> None:
        async def run() -> None:
            await awaitable

        task = asyncio.create_task(run())
        self._reserved_turn_id = turn_id
        self._active_turn_task = task
        self._turn_tasks.add(task)
        self._turn_task_ids[task] = turn_id
        if terminal_event is not None:
            self._turn_terminal_events[turn_id] = terminal_event
        task.add_done_callback(self._clear_finished_turn_task)

    async def _guard_plugin_rewrite(self) -> PluginLockV1:
        """Refuse a re-pin before one is prepared, and report the lock in force.

        Every rejection ``_rewrite_plugins`` makes, made early. Preparing the
        replacement set binds it live, so a request this session was always
        going to refuse must be refused before the caller prepares anything.
        """
        self._reject_active_turn()
        if self._runtime is None:
            if self._promote_on_start is None:
                self._raise_not_implemented("plugin re-pin")
            binding = self._pending_plugin_binding or empty_plugin_binding()
            return binding.lock
        return await self._runtime.guard_plugin_rewrite()

    async def _rewrite_plugins(
        self,
        *,
        plugins: SessionPluginBinding,
        config: RustHarnessConfig,
        subagents: ResolvedSubagentConfiguration | None = None,
    ) -> None:
        """Swap an idle session onto an already-bound plugin set.

        Private on purpose. The Vibe port is the four-method seam; a re-pin
        reaches the session through the Host that owns the store, the same way
        approvals and event publishing already do.

        Idle-only, and it says so with the same typed conflict a second
        ``turn/start`` gets. Swapping the Core's tool catalogue under a running
        Turn would change the tools mid-decision, and the Runtime's own
        rejection for that is an untyped ``RuntimeError`` a caller cannot act
        on, so the check belongs here where the active turn is known. The check
        repeats ``_guard_plugin_rewrite`` because a turn can start in between.
        """
        self._reject_active_turn()
        if self._runtime is None:
            if self._promote_on_start is None:
                self._raise_not_implemented("plugin re-pin")
            # The whole binding, not a reconstruction from ``config.plugins``: the
            # agent profiles are not in the Core config, and promote needs them.
            self._pending_plugin_binding = plugins
            return
        await self._runtime.rewrite_plugins(
            plugin_lock=plugins.lock, config=config, subagents=subagents
        )

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        reserved_task = self._reserved_turn_task
        self._reserved_turn_task = None
        self._reserved_turn_id = None
        self._reserved_turn_started_at = None
        self._buffered_events = None
        errors: list[BaseException] = []
        try:
            if reserved_task is not None and not reserved_task.done():
                reserved_task.cancel()
                with suppress(asyncio.CancelledError):
                    await reserved_task
            await self._cancel_queue_drain_task()
            await self._cancel_active_work()
            await self._cancel_title_task()
            await self._settle_inactive_turn_tasks()
        except BaseException as exc:
            errors.append(exc)
        runtime = self._runtime
        self._runtime = None
        if runtime is not None:
            try:
                await runtime.close()
            except BaseException as exc:
                errors.append(exc)
        mcp_runtime = self._mcp_runtime
        self._mcp_runtime = None
        if mcp_runtime is not None:
            try:
                await mcp_runtime.aclose()
            except BaseException as exc:
                errors.append(exc)
        connector_runtime = self._connector_runtime
        self._connector_runtime = None
        if connector_runtime is not None:
            try:
                await connector_runtime.aclose()
            except BaseException as exc:
                errors.append(exc)
        release_plugins = self._release_plugins
        self._release_plugins = None
        if release_plugins is not None:
            # After the runtime so nothing is mid tool call, before the lease
            # so the next opener never races a half-closed MCP child.
            try:
                await release_plugins()
            except BaseException as exc:
                errors.append(exc)
        discard_on_shutdown = self._discard_on_shutdown
        if discard_on_shutdown is not None:
            try:
                await asyncio.to_thread(discard_on_shutdown, self._session_id)
                self._discard_on_shutdown = None
            except BaseException as exc:
                errors.append(exc)
        lease = self._lease
        self._lease = None
        if lease is not None:
            try:
                await asyncio.to_thread(lease.release)
            except BaseException as exc:
                errors.append(exc)
        on_shutdown = self._on_shutdown
        self._on_shutdown = None
        if on_shutdown is not None:
            try:
                on_shutdown(self._session_id)
            except BaseException as exc:
                errors.append(exc)
        self._event_subscriptions.close()
        self._event_observers.clear()
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Failed to shut down Unified Harness session", errors)

    def _publish_event(self, event: JsonObject, *, settles_turn_task: bool = True) -> None:
        terminal = _terminal_turn(event)
        if terminal is not None and settles_turn_task:
            turn_id, _status = terminal
            self._settling_turn_tasks.update(
                task
                for task, task_turn_id in self._turn_task_ids.items()
                if task_turn_id == turn_id and not task.done()
            )
        published = self._with_next_event_id(event)
        self._reconcile_active_turn_from_event(published)
        # Stamped after reconciliation: a terminal status retires the retry, and
        # the event carrying that status is the one that must report it gone.
        self._emit_event(self._with_harness_owned_state(published))
        if terminal is not None:
            self._handle_terminal_turn(*terminal)

    def _publish_sequenced_event(self, event: JsonObject) -> None:
        self._emit_event(self._with_harness_owned_state(self._with_next_event_id(event)))

    def _schedule_turn(
        self,
        runtime: DurableSessionRuntime,
        payload: "_TurnStartPayload",
        *,
        queue_item_id: str | None = None,
        context_entries: tuple[PreparedTurnEntry, ...] = (),
        accepted_public_history_entries: tuple[JsonObject, ...] = (),
    ) -> None:
        async def run() -> None:
            for index, entry in enumerate(context_entries):
                await runtime.command_without_driving_actions(
                    client_command_id=f"{payload.client_command_id}:context:{index}",
                    method="turn/context",
                    params=payload.params,
                    command=RustContextMessageEvent(content=list(entry.content)),
                    response_factory=lambda _transition: {},
                )
            if not payload.content:
                return
            await runtime.command(
                client_command_id=payload.client_command_id,
                method="turn/start",
                params=payload.params,
                command=RustUserMessageEvent(
                    turn_id=payload.turn_id,
                    content=payload.content,
                    mode="queue",
                ),
                response_factory=lambda _transition: {},
                accepted_public_history_entries=accepted_public_history_entries,
            )

        task = asyncio.create_task(run())
        self._reserved_turn_id = payload.turn_id
        self._active_queue_item_id = queue_item_id
        self._active_turn_task = task
        self._turn_tasks.add(task)
        self._turn_task_ids[task] = payload.turn_id
        task.add_done_callback(self._clear_finished_turn_task)

    def _schedule_turn_queue_drain(self) -> None:
        if self._closed or self._reserved_turn_id is not None or self.active_turn_id is not None:
            return
        existing = self._queue_drain_task
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._drain_turn_queue())
        self._queue_drain_task = task
        task.add_done_callback(self._turn_queue_drain_finished)

    async def _settle_turn_configuration(self) -> None:
        """Let the Host land configuration it parked, before a turn opens on it.

        Every turn passes here: a queued turn is promoted by this Session and
        never reaches ``start_turn``, so asking in one place is what keeps the
        two admissions honest. Awaited outside the lifecycle lock, which
        settling takes itself.
        """
        settle = self._settle_configuration
        if settle is not None:
            await settle()

    async def _drain_turn_queue(self) -> None:
        await self._settle_turn_configuration()
        async with self._lifecycle_lock:
            if (
                self._closed
                or self._reserved_turn_id is not None
                or self.active_turn_id is not None
            ):
                return
            record = self._turn_queue.peek_next()
            if record is None:
                return
            runtime = self._runtime
            if runtime is None:
                runtime = await self._runtime_for_turn_locked(operation="enqueue_turn")
            record = self._turn_queue.pop_next()
            if record is None:
                return
            self._publish_turn_queue_updated()
            self._start_queued_turn(runtime, record)

    def _start_queued_turn(self, runtime: DurableSessionRuntime, record: QueuedTurnRecord) -> None:
        queue_item_id = record.queued_turn.id
        turn_id = f"turn-{secrets.token_hex(16)}"
        context_entries = tuple(
            entry for entry in record.prepared_entries if entry.role == "context"
        )
        user_entry = next(
            (entry for entry in record.prepared_entries if entry.role == "user"),
            None,
        )
        content = list(user_entry.content) if user_entry is not None else []
        if content:
            content = _with_content_meta(content, {"vibe_queue_item_id": queue_item_id})
        dumped = record.params.model_dump(mode="json", by_alias=True, exclude_none=True)
        payload = _TurnStartPayload(
            turn_id=turn_id,
            content=content,
            client_command_id=f"queue-{queue_item_id}",
            params=cast(dict[str, JsonValue], dumped),
        )
        self._schedule_turn(
            runtime,
            payload,
            queue_item_id=queue_item_id,
            context_entries=context_entries,
        )

    def _turn_queue_drain_finished(self, task: asyncio.Task[None]) -> None:
        if self._queue_drain_task is task:
            self._queue_drain_task = None
        if task.cancelled():
            return
        if error := task.exception():
            asyncio.get_running_loop().call_exception_handler(
                {
                    "message": "Unified Harness turn queue drain failed",
                    "exception": error,
                    "task": task,
                }
            )

    def _publish_turn_queue_updated(self) -> None:
        state, _watermark = self._current_state_and_watermark()
        event = SessionEvent(
            event_id=str(self._next_event_id()),
            emitted_at=_now_milliseconds(),
            session_id=self._session_id,
            root_session_id=state.session.root_session_id,
            payload=TurnQueueUpdatedEvent(queue=self.turn_queue),
        )
        self._emit_event(cast(JsonObject, event.model_dump(mode="json", by_alias=True)))

    def _handle_terminal_turn(self, turn_id: str, status: str) -> None:
        if turn_id == self._last_queue_terminal_turn_id:
            return
        self._last_queue_terminal_turn_id = turn_id
        if status in {"failed", "interrupted"}:
            if self._turn_queue.pause():
                self._publish_turn_queue_updated()
            return
        self._schedule_turn_queue_drain()

    def _emit_event(self, event: JsonObject) -> None:
        if self._buffered_events is not None:
            self._buffered_events.append(event)
            return
        self._event_subscriptions.publish(event)
        for observer in tuple(self._event_observers.values()):
            observer(event)

    async def _request_approval(
        self,
        action: RustRuntimeBuiltinToolCallAction | RustProvidedToolCallAction,
        reason: str | None = None,
        required_permissions: tuple[JsonObject, ...] = (),
    ) -> ApprovalGrant:
        callback_id = f"approval-{action.call_id}"
        if callback_id in self._approval_waiters:
            callback_id = f"{callback_id}-{secrets.token_hex(4)}"
        future = asyncio.get_running_loop().create_future()
        callback = _approval_callback(
            session_id=self._session_id,
            callback_id=callback_id,
            action=action,
            created_at=_now_milliseconds(),
            reason=reason,
            required_permissions=required_permissions,
        )
        self._approval_waiters[callback_id] = future
        self._open_callbacks[callback_id] = callback
        try:
            runtime = self._runtime
            register_callback = getattr(runtime, "register_callback_durable", None)
            if register_callback is not None:
                await register_callback(callback_id, "approval", callback)
            self._publish_event({"type": "callback_requested", "callback": callback})
            self._publish_session_state_update()
            return await future
        finally:
            self._approval_waiters.pop(callback_id, None)
            if self._open_callbacks.pop(callback_id, None) is not None:
                self._publish_session_state_update()

    async def _request_process_approval(self, action: RustRuntimeBuiltinToolCallAction) -> bool:
        """Adapt the grant-returning requester to the process runtime's boolean port.

        Smart approve's ``_request_approval`` returns an ``ApprovalGrant`` (once /
        session / always / deny); the process runtime only needs allow-vs-deny.
        """
        grant = await self._request_approval(action)
        return grant.approved

    async def _request_user_input(
        self, action: RustProvidedToolCallAction
    ) -> RustToolSucceededEvent | RustFailTurnEvent:
        callback_id = f"user-input-{action.call_id}"
        if callback_id in self._user_input_waiters:
            callback_id = f"{callback_id}-{secrets.token_hex(4)}"
        future: asyncio.Future[tuple[bool, JsonValue]] = asyncio.get_running_loop().create_future()
        callback = _user_input_callback(
            session_id=self._session_id,
            callback_id=callback_id,
            action=action,
            created_at=_now_milliseconds(),
        )
        self._user_input_waiters[callback_id] = future
        self._open_callbacks[callback_id] = callback
        try:
            runtime = self._runtime
            register_callback = getattr(runtime, "register_callback_durable", None)
            if register_callback is not None:
                await register_callback(callback_id, "user_input", callback)
            self._publish_event({"type": "callback_requested", "callback": callback})
            self._publish_session_state_update()
            accepted, value = await future
            if accepted:
                return RustToolSucceededEvent(
                    action_id=action.action_id,
                    call_id=action.call_id,
                    result=RustToolSuccessResult(
                        content=[RustTextContentBlock(text=json.dumps(value))],
                        structured_content=value,
                    ),
                )
            return RustFailTurnEvent(
                action_id=action.action_id,
                expected_turn_id=action.turn_id,
                error=RustProtocolError(
                    code="callback_delivery_failed",
                    message=_callback_failure_message(value),
                    retryable=False,
                ),
            )
        finally:
            self._user_input_waiters.pop(callback_id, None)
            if self._open_callbacks.pop(callback_id, None) is not None:
                self._publish_session_state_update()

    async def _interrupt_active_turn_from_callback(self, callback: JsonObject | None) -> None:
        async with self._lifecycle_lock:
            runtime = self._runtime
            if runtime is None:
                return
            expected_turn_id = _str_field(callback, "turnId") or self.active_turn_id
            if expected_turn_id is None:
                return
            await self._cancel_active_work()
            await runtime.interrupt(
                expected_turn_id=expected_turn_id, reason="approval cancel_turn"
            )

    async def _cancel_active_work(self) -> None:
        await self._close_open_callbacks("Turn interrupted")
        task = self._active_turn_task
        if task is not None and not task.done() and self._task_has_terminal_projection(task):
            self._settling_turn_tasks.add(task)
            self._active_turn_task = None
            self._reserved_turn_id = None
            self._active_queue_item_id = None
            return
        self._active_turn_task = None
        self._reserved_turn_id = None
        self._active_queue_item_id = None
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    def _task_has_terminal_projection(self, task: asyncio.Task[None]) -> bool:
        runtime = self._runtime
        turn_id = self._turn_task_ids.get(task)
        if runtime is None or turn_id is None:
            return False
        latest_turn = runtime.projection.latest_turn
        return (
            latest_turn is not None
            and latest_turn.id == turn_id
            and latest_turn.status != "in_progress"
        )

    async def _close_open_callbacks(self, reason: str) -> None:
        callbacks = dict(self._open_callbacks)
        for callback in callbacks.values():
            runtime = self._runtime
            resolve_callback = getattr(runtime, "resolve_callback_durable", None)
            if resolve_callback is not None:
                await resolve_callback(
                    _required_str(callback, "callbackId"),
                    {"code": "callback_closed", "message": reason},
                    failed=True,
                )
        self._open_callbacks = {}
        self._closed_callback_ids.update(callbacks)
        for callback in callbacks.values():
            self._publish_event(_callback_cancelled_event(callback, reason))
        for waiter in self._approval_waiters.values():
            if not waiter.done():
                waiter.set_result(ApprovalGrant.DENY)
        self._approval_waiters.clear()
        for waiter in self._user_input_waiters.values():
            if not waiter.done():
                waiter.set_result((False, {"message": reason}))
        self._user_input_waiters.clear()
        if callbacks:
            self._publish_session_state_update()

    async def _settle_inactive_turn_tasks(self) -> None:
        """Finish terminal durability work and cancel other orphaned Turns.

        A terminal projection clears ``_active_turn_task`` before
        ``DurableSessionRuntime.command`` writes its receipt and compacts the
        store. Those tasks must finish before the Runtime closes. Other tasks
        lost active ownership without a matching terminal Turn and are cancelled.
        """
        tasks = [task for task in self._turn_tasks if not task.done()]
        for task in tasks:
            if task not in self._settling_turn_tasks:
                task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def _cancel_queue_drain_task(self) -> None:
        task = self._queue_drain_task
        self._queue_drain_task = None
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    def _reject_active_turn(self) -> None:
        if self._promotion_conflict_id is not None:
            raise HarnessTurnConflictError(self._promotion_conflict_id)
        for task in tuple(self._turn_tasks):
            if task.done():
                self._clear_finished_turn_task(task)
        active_task = self._active_turn_task
        if active_task is not None and active_task.done():
            self._clear_finished_turn_task(active_task)
        # Local setup only, so deliberately not Core's turn: resuming a session
        # prepares connectors and MCP while Core still owns the turn it restores.
        active_turn_id = self._reserved_turn_id
        if active_turn_id is not None:
            raise HarnessTurnConflictError(active_turn_id)

    def _require_mcp_runtime(self) -> MCPRuntime:
        if self._mcp_runtime is None:
            raise HarnessNotImplementedError("MCP is not configured on this Runtime")
        return self._mcp_runtime

    def _require_connector_runtime(self) -> ConnectorRuntime:
        if self._connector_runtime is None:
            self._raise_not_implemented("connector runtime")
        return self._connector_runtime

    def _reconcile_active_turn_from_event(self, event: JsonObject) -> None:
        turn_completing = False
        if event.get("type") == "session_state_updated":
            state = event.get("state")
            session = state.get("session") if isinstance(state, dict) else None
            status = session.get("status") if isinstance(session, dict) else None
            if isinstance(status, dict) and status.get("type") in {"idle", "failed", "archived"}:
                # A provider retry only exists while a completion is in flight, so
                # any terminal status retires it, even one that does not end a turn
                # this backend is tracking.
                self._retrying = None
                # A turn accepted but not yet handed to a task cannot be the one
                # this event ends, and a late event must not drop it.
                if self._active_turn_task is not None:
                    self._reserved_turn_id = None
                    self._active_queue_item_id = None
                    self._active_turn_task = None
            turn_completing = isinstance(status, dict) and status.get("type") == "idle"
        # Tick the title cadence on every projected event, not only at turn
        # boundaries. A single long turn accrues steps with no status change, so a
        # boundary-only check would defer the periodic refresh until the turn ends
        # (or is interrupted). Reading the runtime projection keeps the source of
        # truth authoritative; the cadence gates due-ness and only one title task
        # runs at a time, so ticking per event is cheap.
        #
        # Fully isolated: this runs inside the runtime event sink (before the
        # event is emitted), so a failure here must never drop the event or fail
        # the turn. The title is a background nicety; it can only be skipped.
        try:
            self._maybe_schedule_title_generation(turn_completing=turn_completing)
        except Exception:
            logger.warning(
                "Scheduling background session title failed",
                extra={"harness_backend": "unified", "session_id": self._session_id},
                exc_info=True,
            )

    def _clear_finished_turn_task(self, task: asyncio.Task[None]) -> None:
        self._reap_finished_turn_task(task)
        # Every finished turn is a boundary, including one whose task the event
        # sink already unhooked -- reaping returns early for those, and
        # ``active_turn_id`` is clear either way by the time we are here.
        self._schedule_turn_settlement()

    def _reap_finished_turn_task(self, task: asyncio.Task[None]) -> None:
        self._turn_tasks.discard(task)
        self._settling_turn_tasks.discard(task)
        turn_id = self._turn_task_ids.pop(task, None)
        cancelled = task.cancelled()
        if turn_id is not None:
            self._turn_terminal_events.setdefault(turn_id, asyncio.Event()).set()
        exception = None if cancelled else task.exception()
        if task is not self._active_turn_task:
            if exception is not None:
                self._publish_failed_turn(turn_id, exception, handle_queue=False)
            return
        queue_item_id = self._active_queue_item_id
        self._active_turn_task = None
        self._reserved_turn_id = None
        self._active_queue_item_id = None
        if exception is not None:
            self._publish_failed_turn(
                turn_id,
                exception,
                queue_item_id=queue_item_id,
            )
        elif not cancelled:
            self._schedule_turn_queue_drain()
        self._notify_work_state_changed()

    def _schedule_turn_settlement(self) -> None:
        """Let the Host land parked configuration now the turn is really over.

        ``active_turn_id`` reports the reserved turn until this reaping runs, so
        a Host that settles on the turn's own last event still sees a turn in
        flight and holds its change back. Between turns is here.
        """
        settle = self._settle_configuration
        if settle is None:
            return
        _ = asyncio.ensure_future(settle())

    def _notify_work_state_changed(self) -> None:
        callback = self._on_work_state_changed
        if callback is None:
            return
        result = callback(self._session_id)
        if isinstance(result, Awaitable):
            asyncio.ensure_future(result)

    def _maybe_schedule_title_generation(self, *, turn_completing: bool) -> None:
        if self._closed:
            return
        config = self._title_config
        runtime = self._runtime
        if config is None or config.title_model is None or runtime is None:
            return
        if self._title_task is not None and not self._title_task.done():
            return
        # Read the authoritative projection rather than an event payload: mid-turn
        # events do not carry the public session state, and the projection already
        # reflects the entry that triggered this tick.
        projection = runtime.projection
        entries = list(projection.history.entries)
        current_title = projection.session.title
        current_title = current_title if isinstance(current_title, str) else None
        step = count_model_steps(entries)
        compaction_id = latest_compaction_id(entries)
        self._seed_title_cadence_once(runtime, step=step, compaction_id=compaction_id)
        if not self._title_cadence.begin_if_due(
            step=step,
            current_title=current_title,
            compaction_id=compaction_id,
            turn_completing=turn_completing,
            periodic=config.title_model_is_fast,
        ):
            return
        self._title_task = asyncio.create_task(
            self._generate_title(runtime),
            name=f"harness-title:{self._session_id}",
        )
        self._title_task.add_done_callback(self._title_generation_finished)

    def _title_generation_finished(self, task: asyncio.Task[None]) -> None:
        if self._title_task is task:
            self._title_task = None
        self._notify_work_state_changed()

    def _seed_title_cadence_once(
        self, runtime: DurableSessionRuntime, *, step: int, compaction_id: str | None
    ) -> None:
        # On resume the cadence starts empty and would treat the persisted title
        # as a manual rename, freezing auto refresh. Adopt a persisted auto title
        # so it keeps refreshing; leave a manual one for is_manual to block.
        if self._title_cadence_seeded:
            return
        self._title_cadence_seeded = True
        session = runtime.projection.session
        if session.title is not None and session.title_source == "auto":
            self._title_cadence.restore(title=session.title, step=step, compaction_id=compaction_id)

    async def _cancel_title_task(self) -> None:
        task = self._title_task
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _generate_title(self, runtime: DurableSessionRuntime) -> None:
        config = self._title_config
        if config is None:
            return
        session_id = self._session_id
        try:
            entries = list(runtime.projection.history.entries)
            previous_title = runtime.projection.session.title
            title = await generate_session_title(
                entries, config=config, previous_title=previous_title
            )
            if title is None or runtime.session_id != session_id:
                return
            if self._title_cadence.is_manual(runtime.projection.session.title):
                return
            await runtime.rename_session(title, source="auto")
            self._title_cadence.record(title=title)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Background session title generation failed",
                extra={"harness_backend": "unified", "session_id": session_id},
                exc_info=True,
            )

    def _publish_failed_turn(
        self,
        turn_id: str | None,
        exception: BaseException,
        *,
        queue_item_id: str | None = None,
        handle_queue: bool = True,
    ) -> None:
        logger.exception(
            "Unified turn command failed",
            extra={"harness_backend": "unified", "session_id": self._session_id},
            exc_info=exception,
        )
        if turn_id is None:
            return
        # The turn is over, so nothing is retrying. Cleared before the state is
        # read so the failure is the event that reports the retry gone.
        self._retrying = None
        now = _now_milliseconds()
        state = (
            getattr(self._runtime, "projection", self._state)
            if self._runtime is not None
            else self._state
        )
        started_at = getattr(state.latest_turn, "started_at", now)
        message = str(exception) or type(exception).__name__
        failed = state.model_copy(
            update={
                "session": state.session.model_copy(
                    update={
                        "status": FailedSessionStatus(message=message),
                        "updated_at": now,
                    }
                ),
                "latest_turn": FailedPublicTurn(
                    id=turn_id,
                    session_id=self._session_id,
                    queue_item_id=queue_item_id or _turn_queue_item_id(state.latest_turn, turn_id),
                    started_at=started_at,
                    completed_at=now,
                    error=PublicError(code=type(exception).__name__, message=message),
                ),
            }
        )
        self._state = failed
        self._local_terminal_state_watermark = self._current_projection_watermark()
        event = cast(
            JsonObject,
            {
                "type": "session_state_updated",
                "sessionId": self._session_id,
                "state": failed.model_dump(mode="json", by_alias=True),
            },
        )
        if handle_queue:
            self._publish_event(event, settles_turn_task=False)
        else:
            self._publish_sequenced_event(event)

    def _publish_reserved_turn_interrupted(self, turn_id: str, *, started_at: int) -> None:
        # See _publish_failed_turn: an interrupt ends the turn, so the retry it
        # was waiting on is retired before the terminal state is composed.
        self._retrying = None
        now = _now_milliseconds()
        state, _watermark = self._current_state_and_watermark()
        history_entries = [
            (
                {
                    **entry,
                    "updatedAt": now,
                    "generationStatus": "completed",
                    "state": {
                        "status": "cancelled",
                        "reason": "client interrupt",
                        "outputText": "",
                        "display": {
                            "success": False,
                            "message": "Cancelled",
                        },
                    },
                }
                if entry.get("type") == "effect"
                and entry.get("turnId") == turn_id
                and entry.get("generationStatus") != "completed"
                else entry
            )
            for entry in state.history.entries
        ]
        interrupted = state.model_copy(
            update={
                "session": state.session.model_copy(
                    update={"status": IdleSessionStatus(), "updated_at": now}
                ),
                "latest_turn": InterruptedPublicTurn(
                    id=turn_id,
                    session_id=self._session_id,
                    started_at=started_at,
                    completed_at=now,
                    reason="client interrupt",
                ),
                "history": state.history.model_copy(update={"entries": history_entries}),
            }
        )
        self._state = interrupted
        self._local_terminal_state_watermark = self._current_projection_watermark()
        self._publish_event(
            cast(
                JsonObject,
                {
                    "type": "session_state_updated",
                    "sessionId": self._session_id,
                    "state": interrupted.model_dump(mode="json", by_alias=True),
                },
            )
        )

    def _snapshot(self, history_limit: int) -> SessionSnapshot:
        state, watermark = self._current_state_and_watermark()
        state = self._with_open_callbacks(state)
        history = state.history
        entries = history.entries[-history_limit:] if history_limit else []
        return SessionSnapshot(
            state=state.model_copy(
                update={
                    "history": LatestPublicHistoryPage(entries=entries, cursor=history.cursor),
                    "turn_queue": self.turn_queue,
                }
            ),
            history_limit=history_limit,
            watermark=watermark,
        )

    def _current_state_and_watermark(self) -> tuple[PublicSessionState, int]:
        state = self._state
        if self._local_terminal_state_watermark is not None:
            if self._runtime is not None and hasattr(self._runtime, "projection"):
                runtime_state = self._runtime.projection
                local_turn = self._state.latest_turn
                local_turn_id = (
                    local_turn.id
                    if isinstance(local_turn, FailedPublicTurn | InterruptedPublicTurn)
                    else None
                )
                runtime_status = runtime_state.session.status
                runtime_latest_turn = runtime_state.latest_turn
                runtime_still_runs_local_turn = local_turn_id is not None and (
                    (
                        isinstance(runtime_status, RunningSessionStatus)
                        and runtime_status.active_turn_id == local_turn_id
                    )
                    or (
                        getattr(runtime_latest_turn, "status", None) == "in_progress"
                        and getattr(runtime_latest_turn, "id", None) == local_turn_id
                    )
                )
                if (
                    self._runtime.watermark > self._local_terminal_state_watermark
                    and not runtime_still_runs_local_turn
                ):
                    state = runtime_state
        elif self._runtime is not None and hasattr(self._runtime, "projection"):
            state = self._runtime.projection
        if state.retrying == self._retrying:
            # Every read and every published event lands here, and nothing is
            # retrying almost always, so the common case must not pay for a copy
            # of a state that grows with the session.
            return state, self._current_event_id()
        return state.model_copy(update={"retrying": self._retrying}), self._current_event_id()

    async def _set_provider_retry(self, turn_id: str, retry: ProviderRetry | None) -> None:
        # The live turn is a property over Core's inspection and the scheduled
        # task, not a field. Reading a private one raised AttributeError, which
        # the completion's retry observer swallowed, so nothing ever retried.
        if retry is not None and turn_id != self.active_turn_id:
            return
        if retry is None:
            if self._retrying is None or self._retrying.turn_id != turn_id:
                return
            updated = None
        else:
            updated = PublicRetryState(
                turn_id=turn_id, category=retry.category, detail=retry.detail
            )
        if updated == self._retrying:
            return
        self._retrying = updated
        self._publish_session_state_update()

    def _with_open_callbacks(self, state: PublicSessionState) -> PublicSessionState:
        callbacks = list(self._open_callbacks.values())
        if not callbacks:
            return state.model_copy(update={"active_callbacks": []})
        callback = callbacks[-1]
        active_turn_id = _str_field(callback, "turnId") or self.active_turn_id
        session = state.session
        if active_turn_id is not None:
            detail = _field(callback, "detail", default={})
            callback_kind = (detail.get("kind") if isinstance(detail, dict) else None) or "approval"
            session = session.model_copy(
                update={
                    "status": BlockedSessionStatus(
                        active_turn_id=active_turn_id,
                        callback_id=_required_str(callback, "callbackId"),
                        callback_kind=callback_kind,
                    )
                },
            )
        return state.model_copy(update={"active_callbacks": callbacks, "session": session})

    def publish_notice(self, message: str, *, level: str = "warning") -> None:
        """Emit an out-of-band remark that changes nothing about the session.

        A plugin reload that found a source still unreachable has something to
        say and nothing to report: the command succeeded, the session did not
        change, and there is no procedure result to carry the remark. It rides
        the same queue the MCP and connector runtimes already publish signals
        on, so a subscriber sees it in order with everything else.
        """
        self._publish_event({"type": "notice", "level": level, "message": message})

    def _publish_session_state_update(self) -> None:
        state, _watermark = self._current_state_and_watermark()
        self._publish_sequenced_event(
            cast(
                JsonObject,
                {
                    "type": "session_state_updated",
                    "sessionId": self._session_id,
                    "state": self._with_open_callbacks(state)
                    .model_copy(update={"turn_queue": self.turn_queue})
                    .model_dump(mode="json", by_alias=True),
                },
            )
        )

    def _with_harness_owned_state(self, event: JsonObject) -> JsonObject:
        """Stamp the state this backend owns onto a published state event.

        The turn queue and the provider-retry indicator are the Harness's, not
        the projection's, so an event minted by the projector carries neither.
        Emitting one without them reads downstream as "empty queue, not
        retrying", which silently retires a retry that is still in flight.
        """
        if event.get("type") != "session_state_updated":
            return event
        raw_state = event.get("state")
        if not isinstance(raw_state, dict):
            return event
        state = {
            **raw_state,
            "turnQueue": self.turn_queue.model_dump(mode="json", by_alias=True),
        }
        # Absent means "not retrying": the field is omitted when unset so a
        # reader that predates it keeps parsing.
        if self._retrying is None:
            state.pop("retrying", None)
        else:
            state["retrying"] = self._retrying.model_dump(mode="json", by_alias=True)
        return {**event, "state": state}

    def _with_next_event_id(self, event: JsonObject) -> JsonObject:
        event_id = self._next_event_id(event.get("eventId"))
        return {**event, "eventId": event_id}

    def _next_event_id(self, candidate: object = None) -> int:
        if isinstance(candidate, int) and candidate > self._event_id:
            self._event_id = candidate
        else:
            self._event_id = self._current_event_id() + 1
        return self._event_id

    def _current_event_id(self) -> int:
        runtime_watermark = self._current_projection_watermark()
        return max(self._event_id, self._watermark, runtime_watermark)

    def _current_projection_watermark(self) -> int:
        return self._runtime.watermark if self._runtime is not None else self._watermark

    @staticmethod
    def _raise_not_implemented(method: str) -> Never:
        raise HarnessNotImplementedError(f"The {method} method is not implemented yet.")


@dataclass(frozen=True, slots=True)
class _SessionBackendResult:
    response: object
    after_response: Callable[[], None] | None = None
    on_response_abandoned: Callable[[], None] | None = None
    buffered_events: tuple[JsonObject, ...] = ()


@dataclass(frozen=True, slots=True)
class _PublicTurn:
    id: str
    session_id: str
    status: str
    started_at: int


@dataclass(frozen=True, slots=True)
class _TurnStartResponse:
    turn: _PublicTurn
    last_event_id: int


@dataclass(frozen=True, slots=True)
class _TurnStartPayload:
    turn_id: str
    content: list[RustContentBlock]
    client_command_id: str
    params: dict[str, JsonValue]


def _deferred_turn_start_payload(
    request: TurnStartRequest, *, image_source_roots: tuple[Path, ...]
) -> _TurnStartPayload:
    content = _rust_session_content_blocks(
        list(request.message), image_source_roots=image_source_roots
    )
    if request.client_user_message_id is not None:
        content = _with_content_meta(
            content,
            {"vibe_client_message_id": request.client_user_message_id},
        )
    if request.user_display_content is not None:
        content = _with_content_meta(
            content,
            {
                "vibe.userDisplayContent": request.user_display_content.model_dump(
                    mode="json", by_alias=True
                )
            },
        )
    if request.injected:
        # Host-injected turns stay model-visible but must not surface as
        # public user history, so the projection can recognize them.
        content = _with_content_meta(content, {"vibe.injected": True})
    if not content:
        raise ValueError("Unified Harness turn/start currently requires user content")
    turn_id = f"turn-{secrets.token_hex(16)}"
    return _TurnStartPayload(
        turn_id=turn_id,
        content=content,
        client_command_id=request.idempotency_key or turn_id,
        params=cast(
            dict[str, JsonValue],
            request.model_dump(mode="json", by_alias=True, exclude_none=True),
        ),
    )


def _turn_start_payload(
    params: object, *, image_source_roots: tuple[Path, ...]
) -> _TurnStartPayload:
    raw = cast(Any, params)
    _required_str(raw, "session_id")
    content = _rust_content_blocks(
        raw.message,
        getattr(raw, "client_user_message_id", None),
        image_source_roots=image_source_roots,
    )
    user_display_content = getattr(raw, "user_display_content", None)
    if user_display_content is not None:
        content = _with_content_meta(
            content,
            {
                "vibe.userDisplayContent": user_display_content.model_dump(
                    mode="json", by_alias=True
                )
            },
        )
    if getattr(raw, "injected", False):
        content = _with_content_meta(content, {"vibe.injected": True})
    if not content:
        raise ValueError("Unified Harness turn/start currently supports text input only")
    turn_id = f"turn-{secrets.token_hex(16)}"
    dumped = raw.model_dump(mode="json", by_alias=True, exclude_none=True)
    return _TurnStartPayload(
        turn_id=turn_id,
        content=content,
        client_command_id=getattr(raw, "idempotency_key", None) or turn_id,
        params=cast(dict[str, JsonValue], dumped),
    )


def _prepare_turn_entries(
    entries: list[TurnInputEntry], *, image_source_roots: tuple[Path, ...]
) -> tuple[PreparedTurnEntry, ...]:
    prepared: list[PreparedTurnEntry] = []
    for entry in entries:
        content = _rust_session_content_blocks(entry.content, image_source_roots=image_source_roots)
        user_display_content = entry.annotations.vibe_user_display_content
        if user_display_content is not None:
            content = _with_content_meta(
                content,
                {
                    "vibe.userDisplayContent": user_display_content.model_dump(
                        mode="json", by_alias=True
                    )
                },
            )
        if entry.entry_id is not None:
            content = _with_content_meta(content, {"vibe_client_message_id": entry.entry_id})
        prepared.append(PreparedTurnEntry(role=entry.role, content=tuple(content)))
    return tuple(prepared)


def _queued_steer_content(record: QueuedTurnRecord) -> list[RustContentBlock]:
    if any(entry.role == "context" for entry in record.prepared_entries):
        raise ValueError("Unified Harness queued steering does not support context entries")
    user_entry = next(
        (entry for entry in record.prepared_entries if entry.role == "user"),
        None,
    )
    if user_entry is None or not user_entry.content:
        raise ValueError("Unified Harness queued steering requires user content")
    return list(user_entry.content)


def _rust_session_content_blocks(
    content: list[SessionContentBlock], *, image_source_roots: tuple[Path, ...]
) -> list[RustContentBlock]:
    blocks: list[RustContentBlock] = []
    for block in content:
        if isinstance(block, SessionTextContentBlock):
            blocks.append(RustTextContentBlock(text=block.text))
            continue
        if isinstance(block, SessionImageContentBlock):
            blocks.append(
                _session_image_content_block(block, image_source_roots=image_source_roots)
            )
            continue
        if isinstance(block, SessionResourceLinkContentBlock):
            blocks.append(
                RustResourceLinkContentBlock(
                    uri=block.uri,
                    name=block.name or block.uri,
                    title=block.title,
                    description=block.description,
                    mime_type=block.media_type,
                    size=block.size,
                )
            )
            continue
        if isinstance(block, SessionEmbeddedResourceContentBlock):
            resource: RustTextResourceContents | RustBlobResourceContents
            if block.text is not None:
                resource = RustTextResourceContents(
                    uri=block.uri,
                    mime_type=block.media_type,
                    text=block.text,
                )
            else:
                if block.blob is None:
                    raise RuntimeError("validated embedded resource has no content")
                resource = RustBlobResourceContents(
                    uri=block.uri,
                    mime_type=block.media_type,
                    blob=block.blob,
                )
            blocks.append(RustEmbeddedResourceContentBlock(resource=resource))
            continue
        raise TypeError(f"Unsupported queued Turn content block: {block!r}")
    return blocks


def _session_image_content_block(
    block: SessionImageContentBlock, *, image_source_roots: tuple[Path, ...]
) -> RustImageContentBlock:
    if block.uri.startswith("data:"):
        header, separator, data = block.uri.partition(",")
        if not separator or not header.endswith(";base64"):
            raise ValueError("Unified Harness image URI is not base64 data")
        media_type = block.media_type or header[5:-7]
        if not media_type:
            raise ValueError("Unified Harness image URI has no media type")
        return RustImageContentBlock(data=_validated_inline_image_data(data), mime_type=media_type)

    parsed = urlparse(block.uri)
    if parsed.scheme not in {"", "file"}:
        raise ValueError(f"Unified Harness queued image URI is not local: {block.uri!r}")
    path = Path(file_uri_to_path(block.uri)) if parsed.scheme == "file" else Path(block.uri)
    media_type = block.media_type
    if media_type is None:
        raise ValueError("Unified Harness queued image has no media type")
    encoded = _read_image_file_source(path, image_source_roots=image_source_roots)
    uri = block.uri if parsed.scheme == "file" else path.expanduser().resolve().as_uri()
    return build_file_image_content_block(
        data=encoded.data,
        uri=uri,
        name=block.alt_text or path.name or uri,
        mime_type=media_type,
        size=encoded.size,
    )


def _with_content_meta(
    content: list[RustContentBlock], additions: JsonObject
) -> list[RustContentBlock]:
    if not content:
        return content
    updated = list(content)
    first = updated[0]
    meta = dict(first.meta or {})
    meta.update(additions)
    updated[0] = first.model_copy(update={"meta": meta})
    return updated


def _scheduled_loop_notice(session_id: str, turn_id: str, loop_id: str) -> JsonObject:
    observed_at = _now_milliseconds()
    return {
        "type": "notice",
        "id": f"scheduled-loop-{turn_id}",
        "sessionId": session_id,
        "turnId": turn_id,
        "createdAt": observed_at,
        "updatedAt": observed_at,
        "generationStatus": "completed",
        "level": "info",
        "message": f"Loop `{loop_id}` fired",
        "details": {"kind": "scheduled_loop_fired", "loopId": loop_id},
    }


def _rust_content_blocks(
    message: object,
    client_message_id: str | None,
    *,
    image_source_roots: tuple[Path, ...],
) -> list[RustContentBlock]:
    blocks: list[RustContentBlock] = []
    for block in cast(Any, message):
        block_type = getattr(block, "type", None)
        match block_type:
            case "text":
                meta = {"vibe_client_message_id": client_message_id} if client_message_id else None
                blocks.append(
                    RustTextContentBlock.model_validate(
                        {
                            "type": "text",
                            "text": block.text,
                            **({"_meta": meta} if meta is not None else {}),
                        }
                    )
                )
            case "image":
                attachment = block.attachment
                source = attachment.source
                source_kind = getattr(source, "kind", None)
                match source_kind:
                    case "inline":
                        image = RustImageContentBlock(
                            data=_validated_inline_image_data(source.data),
                            mime_type=attachment.mime_type,
                        )
                    case "file":
                        path = Path(source.path)
                        encoded = _read_image_file_source(
                            path, image_source_roots=image_source_roots
                        )
                        uri = path.expanduser().resolve().as_uri()
                        image = build_file_image_content_block(
                            data=encoded.data,
                            uri=uri,
                            name=attachment.alias or path.name or uri,
                            mime_type=attachment.mime_type,
                            size=encoded.size,
                        )
                    case _:
                        raise ValueError(
                            "Unified Harness message input does not support "
                            f"image source kind: {source_kind!r}"
                        )
                blocks.append(image)
            case "resource":
                resource = block.resource
                resource_kind = getattr(resource, "kind", None)
                match resource_kind:
                    case "link":
                        blocks.append(
                            RustResourceLinkContentBlock(
                                uri=resource.uri,
                                name=resource.name or resource.uri,
                                title=resource.title,
                                description=resource.description,
                                mime_type=resource.media_type,
                                size=resource.size,
                            )
                        )
                    case "text":
                        blocks.append(
                            RustEmbeddedResourceContentBlock(
                                resource=RustTextResourceContents(
                                    uri=resource.uri,
                                    mime_type=resource.media_type,
                                    text=resource.text,
                                )
                            )
                        )
                    case "blob":
                        blocks.append(
                            RustEmbeddedResourceContentBlock(
                                resource=RustBlobResourceContents(
                                    uri=resource.uri,
                                    mime_type=resource.media_type,
                                    blob=resource.blob,
                                )
                            )
                        )
                    case _:
                        raise ValueError(
                            "Unified Harness message input does not support "
                            f"resource kind: {resource_kind!r}"
                        )
            case _:
                raise ValueError(
                    "Unified Harness message input does not support "
                    f"content block type: {block_type!r}"
                )
    return blocks


def _validated_inline_image_data(data: str) -> str:
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise ValueError("Unified Harness image data is not valid base64") from exc
    _check_image_size(len(raw))
    return data


def _image_source_roots(
    cwd: str | None, image_source_roots: tuple[Path, ...] | None
) -> tuple[Path, ...]:
    if image_source_roots is not None:
        roots = image_source_roots
    else:
        roots = (Path(cwd or Path.cwd()),)
    return tuple(root.expanduser().resolve() for root in roots)


def _read_image_file_source(
    path: Path, *, image_source_roots: tuple[Path, ...]
) -> _EncodedImageFile:
    source = path.expanduser().resolve()
    if not any(source.is_relative_to(root) for root in image_source_roots):
        raise ValueError(f"Image file is outside the workspace or session attachments: {source}")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise ValueError(f"Failed to read image file {source}: {exc}") from exc
    _check_image_size(size)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise ValueError(f"Failed to read image file {source}: {exc}") from exc
    _check_image_size(len(raw))
    return _EncodedImageFile(
        data=base64.b64encode(raw).decode("ascii"),
        size=len(raw),
    )


def _check_image_size(size: int) -> None:
    if size > _MAX_IMAGE_BYTES:
        raise ValueError(f"Image is too large: {size} > {_MAX_IMAGE_BYTES}")


def _approval_callback(
    *,
    session_id: str,
    callback_id: str,
    action: RustRuntimeBuiltinToolCallAction | RustProvidedToolCallAction,
    created_at: int,
    reason: str | None = None,
    required_permissions: tuple[JsonObject, ...] = (),
) -> JsonObject:
    tool_name = gated_tool_name(action)
    related_entry_id = f"effect-{action.action_id}"
    message = tool_name
    status_text = f"Waiting for approval to run {tool_name}"
    if tool_name == "process.start":
        message = (
            "Start an interactive process. Vibe may send it further input until it exits "
            "or is stopped."
        )
        status_text = "Waiting for approval to start an interactive process"
    return cast(
        JsonObject,
        {
            "type": "callback",
            "id": callback_id,
            "sessionId": session_id,
            "turnId": action.turn_id,
            "createdAt": created_at,
            "updatedAt": created_at,
            "generationStatus": "in_progress",
            "relatedEntryId": related_entry_id,
            "callbackId": callback_id,
            "title": f"Approve {tool_name}",
            "detail": {
                "kind": "approval",
                "effect": {
                    "kind": "tool",
                    "toolName": tool_name,
                    "input": action.call.arguments,
                    "display": {
                        "summary": tool_name,
                        "content": None,
                        "suffix": "",
                        "verb": "Run",
                        "message": message,
                        "settledVerb": "Ran",
                        "settledMessage": tool_name,
                        "statusText": status_text,
                    },
                },
                "requiredPermissions": list(required_permissions),
                "choices": [
                    "approve",
                    "approve_for_session",
                    "approve_permanently",
                    "deny",
                    "cancel_turn",
                ],
                "relatedEntryId": related_entry_id,
                "reason": reason,
            },
            "state": {"status": "open"},
        },
    )


def _user_input_callback(
    *,
    session_id: str,
    callback_id: str,
    action: RustProvidedToolCallAction,
    created_at: int,
) -> JsonObject:
    return cast(
        JsonObject,
        {
            "type": "callback",
            "id": callback_id,
            "sessionId": session_id,
            "turnId": action.turn_id,
            "createdAt": created_at,
            "updatedAt": created_at,
            "generationStatus": "in_progress",
            "relatedEntryId": action.call_id,
            "callbackId": callback_id,
            "title": "User input required",
            "detail": {
                "kind": "user_input",
                "request": action.call.arguments,
                "relatedEntryId": action.call_id,
            },
            "state": {"status": "open"},
        },
    )


def _callback_failure_message(value: JsonValue) -> str:
    if isinstance(value, dict):
        message = value.get("message")
        if isinstance(message, str) and message:
            return message
    return "Callback delivery failed"


def _callback_resolved_event(callback: JsonObject, result: object) -> JsonObject:
    error = _field(result, "error", default=None)
    if error is not None:
        return _callback_cancelled_event(callback, _required_error_message(error))
    output = _field(result, "output")
    resolved = dict(callback)
    resolved["updatedAt"] = _now_milliseconds()
    resolved["generationStatus"] = "completed"
    resolved["state"] = {"status": "answered", "output": _json_value(output)}
    return cast(JsonObject, {"type": "callback_resolved", "callback": resolved})


def _callback_cancelled_event(callback: JsonObject, reason: str) -> JsonObject:
    resolved = dict(callback)
    resolved["updatedAt"] = _now_milliseconds()
    resolved["generationStatus"] = "completed"
    resolved["state"] = {"status": "cancelled", "reason": reason}
    return cast(JsonObject, {"type": "callback_resolved", "callback": resolved})


def _required_error_message(error: object) -> str:
    message = _field(error, "message", default=None)
    return message if isinstance(message, str) and message else "Callback closed"


def _approval_grant(decision_type: str | None) -> ApprovalGrant:
    """Map an app-server decision type onto an approval grant (unknown -> deny)."""
    try:
        return ApprovalGrant(decision_type)
    except ValueError:
        return ApprovalGrant.DENY


def _approval_result_decision_type(result: object) -> str | None:
    if _field(result, "error", default=None) is not None:
        return None
    output = _field(result, "output", default=None)
    if output is None:
        return None
    decision = _field(output, "decision", default=None)
    decision_type = _field(decision, "type", default=None)
    return decision_type if isinstance(decision_type, str) else None


def _str_field(value: object, name: str) -> str | None:
    field = _field(value, name, default=None)
    return field if isinstance(field, str) else None


def _field(value: object, name: str, default: object = ...) -> object:
    if isinstance(value, dict):
        if name in value:
            return value[name]
        parts = name.split("_")
        camel = parts[0] + "".join(part.title() for part in parts[1:])
        if camel in value:
            return value[camel]
    if hasattr(value, name):
        return getattr(value, name)
    if default is not ...:
        return default
    raise ValueError(f"Missing required callback field: {name}")


def _json_value(value: object) -> JsonValue:
    if hasattr(value, "model_dump"):
        dumped = cast(Any, value).model_dump(mode="json", by_alias=True)
        return cast(JsonValue, dumped)
    return cast(JsonValue, value)


def _required_str(params: object, field: str) -> str:
    value = _field(params, field, default=None)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} is required")
    return value


def _terminal_turn(event: JsonObject) -> tuple[str, str] | None:
    if event.get("type") != "session_state_updated":
        return None
    state = event.get("state")
    if not isinstance(state, dict):
        return None
    turn = state.get("latestTurn")
    if not isinstance(turn, dict):
        return None
    turn_id = turn.get("id")
    status = turn.get("status")
    if not isinstance(turn_id, str) or status not in {
        "completed",
        "failed",
        "interrupted",
    }:
        return None
    return turn_id, cast(str, status)


def _turn_queue_item_id(turn: object, turn_id: str) -> str | None:
    if getattr(turn, "id", None) != turn_id:
        return None
    queue_item_id = getattr(turn, "queue_item_id", None)
    return queue_item_id if isinstance(queue_item_id, str) else None


def _now_milliseconds() -> int:
    import time

    return int(time.time() * 1000)


__all__ = ["HarnessSessionSubscription", "UnifiedHarnessSessionBackend"]
