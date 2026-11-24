"""What the per-Session adapter uses from its runtime's Session object.

The adapter depends on this port, not on the local runtime's class, so the
target runtime can implement it too. Most members are direct runtime calls:
gaps to remove as the Session API covers them. Members read through
``getattr`` are optional and stay out.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.protocol import RustHarnessConfig
    from mistralai_vibe_local_harness.session_protocol import (
        HostEffectRecord,
        JsonObject,
        PluginInfo,
        SessionReadParams,
        SessionReadResult,
        SessionSnapshot,
        TurnEnqueueParams,
        TurnQueue,
        TurnQueueEditAbandonParams,
        TurnQueueEditCancelParams,
        TurnQueueEditSaveParams,
        TurnQueueEditStartParams,
        TurnQueueReadParams,
        TurnQueueRemoveParams,
        TurnQueueReplaceParams,
        TurnQueueResumeParams,
    )
    from mistralai_vibe_local_harness.vibe import (
        ConnectorRouteSnapshot,
        HarnessSessionSubscription,
        LocalRuntimeAdapterConfig,
        MCPRouteSnapshot,
        ResolvedConnectorCatalog,
        ResolvedConnectorSelection,
        ResolvedMCPCatalog,
        SessionConfig,
        SessionWorkspace,
        UnifiedHarnessSessionBackend,
    )
    from mistralai_vibe_local_harness.vibe._storage import PluginLockV1, SessionPin


class RuntimeSessionPort(Protocol):  # noqa: PLR0904 - mirrors what the adapter calls today
    """The runtime Session one per-Session adapter drives."""

    # Reads and Turn state

    @property
    def session_id(self) -> str: ...

    @property
    def parent_session_id(self) -> str | None: ...

    @property
    def started_by_session_id(self) -> str | None: ...

    @property
    def ephemeral(self) -> bool: ...

    @property
    def active_turn_id(self) -> str | None: ...

    @property
    def turn_waits(self) -> bool: ...

    @property
    def has_turn_work(self) -> bool: ...

    @property
    def has_active_work(self) -> bool: ...

    async def read(self, params: SessionReadParams) -> SessionReadResult: ...

    async def subscribe(
        self, params: SessionReadParams
    ) -> HarnessSessionSubscription: ...

    def observe_events(
        self,
        observer_id: str,
        observer: Callable[[JsonObject], None],
        *,
        places_notices: bool = True,
    ) -> SessionSnapshot: ...

    def stop_observing_events(self, observer_id: str) -> None: ...

    def guard_request(self) -> None: ...

    def turn_start_boundary(self) -> AbstractAsyncContextManager[None]: ...

    async def wait_for_recovered_turn(self) -> None: ...

    def configure_turn_settlement(
        self, settle: Callable[[], Awaitable[None]]
    ) -> None: ...

    def configure_waiting_turn_steer(self, steered: Callable[[], None]) -> None: ...

    async def start_turn(self, params: object) -> object: ...

    async def start_scheduled_turn(self, params: object, loop_id: str) -> object: ...

    async def steer_turn(self, params: object) -> object: ...

    async def interrupt_turn(self, params: object) -> object: ...

    async def respond_to_callback(self, params: object) -> object: ...

    async def compact(self, params: object) -> object: ...

    async def shutdown(self) -> None: ...

    # Prompt queue

    @property
    def turn_queue(self) -> TurnQueue: ...

    async def enqueue_turn(self, params: TurnEnqueueParams) -> object: ...

    async def read_turn_queue(self, params: TurnQueueReadParams) -> object: ...

    async def remove_queued_turn(self, params: TurnQueueRemoveParams) -> object: ...

    async def replace_queued_turn(self, params: TurnQueueReplaceParams) -> object: ...

    async def resume_turn_queue(self, params: TurnQueueResumeParams) -> object: ...

    async def start_queued_turn_edit(
        self, params: TurnQueueEditStartParams
    ) -> object: ...

    async def save_queued_turn_edit(
        self, params: TurnQueueEditSaveParams
    ) -> object: ...

    async def cancel_queued_turn_edit(
        self, params: TurnQueueEditCancelParams
    ) -> object: ...

    async def abandon_queued_turn_edit(
        self, params: TurnQueueEditAbandonParams
    ) -> object: ...

    async def abandon_active_queued_turn_edit(self) -> None: ...

    # Workspace, fork and pins

    @property
    def cwd(self) -> str: ...

    @property
    def workspace(self) -> SessionWorkspace: ...

    @property
    def next_workspace(self) -> SessionWorkspace: ...

    def move_workspace(
        self,
        workspace: SessionWorkspace,
        *,
        on_moved: Callable[[SessionWorkspace], Awaitable[None]] | None = None,
        on_dropped: Callable[[], Awaitable[None]] | None = None,
    ) -> None: ...

    async def drop_workspace_move(self) -> None: ...

    def pin(self, pin: SessionPin) -> str | None: ...

    async def persist_pin(self, pin: SessionPin, value: str) -> bool: ...

    async def promote(self) -> bool: ...

    # Gaps to remove: private members of the local runtime.
    def _fork_boundary(self) -> AbstractAsyncContextManager[None]: ...

    async def _guard_relocation(self) -> PluginLockV1: ...

    # MCP, connectors and plugins

    @property
    def mcp_discovery_due(self) -> bool: ...

    async def read_mcp(self) -> MCPRouteSnapshot: ...

    async def reconfigure_mcp(
        self,
        configuration: ResolvedMCPCatalog,
        *,
        force_remote_discovery: bool,
        push_to_core: bool = True,
        cached_only: bool = False,
    ) -> MCPRouteSnapshot: ...

    async def discover_pending_mcp(
        self, *, refresh: bool = True
    ) -> MCPRouteSnapshot: ...

    async def push_mcp_capabilities(self) -> None: ...

    async def authorization_changed(
        self, *, name: str, descriptor_revision: str
    ) -> MCPRouteSnapshot: ...

    async def suspend_mcp(
        self, *, name: str, tool_name: str | None
    ) -> MCPRouteSnapshot: ...

    async def read_connectors(self) -> ConnectorRouteSnapshot: ...

    async def reconfigure_connectors(
        self,
        catalog: ResolvedConnectorCatalog,
        selection: ResolvedConnectorSelection,
        *,
        push_to_core: bool = True,
    ) -> ConnectorRouteSnapshot: ...

    async def push_connector_capabilities(self) -> None: ...

    def hold_connector_calls(
        self, *, timeout_s: float
    ) -> AbstractAsyncContextManager[None]: ...

    async def suspend_connectors(
        self, *, alias: str, tool_name: str | None
    ) -> ConnectorRouteSnapshot: ...

    async def read_plugin_info(self) -> PluginInfo: ...

    async def read_plugin_lock(self) -> PluginLockV1: ...

    # Config and instructions

    @property
    def core_config(self) -> RustHarnessConfig | None: ...

    @property
    def adapter_config(self) -> LocalRuntimeAdapterConfig | None: ...

    async def apply_config(
        self, config: SessionConfig, *, allow_reserved_turn: bool = False
    ) -> None: ...

    def apply_adapter_config(
        self, adapter_config: LocalRuntimeAdapterConfig
    ) -> None: ...

    async def check_config_fits(self, config: SessionConfig) -> None: ...

    async def reconfigure_subagents(
        self, adapter_config: LocalRuntimeAdapterConfig
    ) -> None: ...

    async def reconfigure_system_instructions(self, instructions: str) -> None: ...

    async def record_live_agent_change(
        self, adapter_config: LocalRuntimeAdapterConfig
    ) -> None: ...

    # Context, notices and host effects

    async def inject_context(self, params: object) -> object: ...

    async def holds_images(self) -> bool: ...

    def publish_notice(
        self, message: str, *, level: str = "warning", code: str | None = None
    ) -> None: ...

    async def record_host_effect(self, record: HostEffectRecord) -> object: ...


if TYPE_CHECKING:

    def _local_session_satisfies_the_port(
        session: UnifiedHarnessSessionBackend,
    ) -> RuntimeSessionPort:
        return session  # Fails type checking when the local class leaves the port.
