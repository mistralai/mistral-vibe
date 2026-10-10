from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
import contextlib
from dataclasses import replace
from datetime import UTC, datetime
import json
import logging
import os
from pathlib import Path
import re
import shutil
import time
import tomllib
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, cast
from unittest.mock import AsyncMock, Mock

from git import Repo
import pytest
import tomli_w

import mistralai_vibe_local_harness.session_protocol as harness_session_protocol
from mistralai_vibe_local_harness.vibe import (
    DeferredTurnPreparationContext as HarnessDeferredTurnPreparationContext,
    DeferredTurnPreparationError as HarnessDeferredTurnPreparationError,
    DeferredTurnStartCapability,
    DeferredTurnStartParams as HarnessDeferredTurnStartParams,
    DeferredTurnStartResult as HarnessDeferredTurnStartResult,
)
from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.app_server import FakeSessionBackendServices, build_test_app_server
from tests.stubs.fake_account_gateway import FakeAccountGateway
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from tests.stubs.fake_connector_catalog import FakeConnectorCatalogService
from tests.stubs.fake_connector_registry import FakeConnectorRegistry
from vibe.app_server import _runtime as runtime_module
from vibe.app_server._account import WhoAmIResult
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._mcp_auth import MCPAuthenticationService
import vibe.app_server._narration as narration_module
from vibe.app_server._plugin_mcp import PluginMCPCatalog
from vibe.app_server._runtime import (
    AgentRuntimeFactory,
    build_runtime_snapshot,
    build_unified_runtime_snapshot,
)
from vibe.app_server._session_backend_port import (
    ResolvedMCPCatalog,
    SessionBackendError,
    SessionBackendHistoryClearHost,
    SessionBackendHost,
    SessionBackendHostArchive,
    SessionBackendHostPin,
    SessionBackendRuntimeView,
    SessionConnectorSourceState,
    SessionConnectorState,
)
from vibe.app_server._unified_harness_snapshots import RewindFileSnapshots
from vibe.app_server._unified_scheduled_loops import (
    ScheduledLoopStoreError,
    ScheduledPrompt,
)
from vibe.app_server._unified_scratchpad import SCRATCHPAD_TOOL_NAME
from vibe.app_server._worktree_effects import WorktreeProgress, WorktreeProgressFeed
from vibe.app_server._worktree_session import WorktreeResolution
from vibe.app_server.client import AppServerClient
from vibe.app_server.events import (
    ChildSessionUpdated,
    HistoryEntryAdded,
    HistoryEntryUpdated,
    SessionSnapshot,
    TurnQueueUpdated,
    TurnRetrying,
)
from vibe.app_server.models import (
    AccountPlanKind,
    CompletedEffectState,
    ConnectorCounts,
    FailedEffectState,
    FileImageSource,
    GenericEffectDetail,
    IdleSessionStatus,
    InlineImageSource,
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
    PublicBackgroundProcess,
    PublicCallbackEntry,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicMessageEntry,
    PublicQueuedTurn,
    PublicRetryCategory,
    PublicRetryState,
    PublicSession,
    PublicSessionState,
    PublicTurn,
    PublicTurnQueue,
    PublicTurnStatus,
    ResourceContentBlock,
    RunningEffectState,
    ShellEffectDetail,
    ShellEffectOutput,
    SkillEffectDetail,
    SkillEffectInput,
    TextContentBlock,
    TokenUsage,
    TurnErrorCode,
    WorktreeEffectDetail,
    validate_history_entry,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    AutoWorktreeInput,
    CallbackResultError,
    ClientCapabilities,
    ClientInfo,
    ContextInjectParams,
    ContextInjectResponse,
    EmptyResponse,
    FeedbackShouldShowParams,
    FeedbackShouldShowResponse,
    MessageAnnotations,
    NarrationSummarizeParams,
    NarrationSummarizeResponse,
    NewWorktreeInput,
    Notification,
    PageRequest,
    PluginInfoParams,
    PluginInfoResponse,
    ProtocolErrorCode,
    RuntimeReadParams,
    RuntimeReadResponse,
    RuntimeSnapshot,
    ServerRequest,
    ServerWarningParams,
    SessionArchiveParams,
    SessionArchiveResponse,
    SessionContinueParams,
    SessionDeleteParams,
    SessionForkParams,
    SessionHistoryClearParams,
    SessionHistoryClearResponse,
    SessionImageContentBlock,
    SessionKind,
    SessionLeaseHolder,
    SessionListParams,
    SessionListResponse,
    SessionOptions,
    SessionPinParams,
    SessionPinResponse,
    SessionReadParams,
    SessionReadResponse,
    SessionResumeParams,
    SessionRewindParams,
    SessionSettingsUpdateParams,
    SessionStartParams,
    SessionTextContentBlock,
    SessionTitleUpdateParams,
    SkillsInstalledParams,
    SkillsInstalledResponse,
    SkillsListParams,
    SkillsListResponse,
    TurnContextInputEntry,
    TurnEnqueueParams,
    TurnEnqueueResponse,
    TurnInterruptParams,
    TurnQueueReadParams,
    TurnQueueReplaceParams,
    TurnQueueReplaceResponse,
    TurnQueueResumeParams,
    TurnQueueSteerParams,
    TurnQueueSteerResponse,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerParams,
    TurnUserInputEntry,
    WorkspacePromptPrepareParams,
    WorkspacePromptPrepareResponse,
)
from vibe.app_server.server import AppServer
from vibe.app_server.session import AppServerSession
from vibe.app_server.transport import memory_transport_pair
from vibe.core.agents.manager import AgentManager
from vibe.core.config import (
    DEFAULT_ACTIVE_MODEL_CONFIG,
    MCPStdio,
    ModelConfig,
    SessionLoggingConfig,
    VibeConfigSchema,
)
from vibe.core.config.admin_config import AdminConfigApplyResult, AdminConfigOutcome
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.experiments.active import ExperimentSurface
from vibe.core.git.worktree import (
    ManagedWorktree,
    PreparedWorktree,
    WorktreeReleaseOutcome,
)
from vibe.core.git.worktree.record import WorktreeClaim, WorktreeRecoveryRecord
from vibe.core.paths import PLANS_DIR, WORKTREES_DIR
from vibe.core.session.session_interop import (
    InvalidLegacyInteropSourceError,
    export_legacy_committed_history,
    resolve_legacy_session_reference,
)
from vibe.core.session.session_lease import SessionBusyError, SessionLease
from vibe.core.skills.manager import SkillManager
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import LaunchContext
from vibe.core.tools.builtins.skill import already_loaded_message
from vibe.core.tools.connectors.connector_registry import RemoteTool
from vibe.core.tools.mcp.registry import MCPRegistry
from vibe.core.tools.models import ToolPermission
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.core.types import LLMMessage, Role, ScheduledLoop
from vibe.permissions import PathGrantScope
from vibe.user_content import UserDisplayContent, UserTextResource

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.session_protocol import JsonObject
    from mistralai_vibe_local_harness.vibe._storage import SessionPin

    # Imported for typing only: the module pulls in the optional Harness
    # extra, and these tests skip rather than fail when it is absent.
    from vibe.app_server._unified_harness_backend_adapter import SessionContextBuilder

_SESSION_CREATED = re.compile(
    r"^Session created: harness=(?P<harness>\w+) session_id=(?P<session_id>\S+)$"
)


class _RecordingSession:
    """Stands in for the Harness session, recording every pushed configuration."""

    session_id = "session-1"
    cwd: str | None = None
    parent_session_id: str | None = None
    ephemeral = True
    deferred_turn_params: HarnessDeferredTurnStartParams

    def __init__(self) -> None:
        self._pins: dict[SessionPin, str | None] = {}
        self._active_turn_id: str | None = None
        self.reserved_turn_id: str | None = None
        self.allow_reserved_flags: list[bool] = []
        self.applied: list[object] = []
        self.core_configs: list[object] = []
        self.settings: list[object] = []
        self.system_instructions: list[str | None] = []
        self.capabilities: list[object] = []
        self.plugins: list[tuple[object, ...] | None] = []
        self.sent: list[Any] = []
        self.preview = ""
        self.settle_configuration: Any | None = None

    def configure_turn_settlement(self, settle: Any) -> None:
        self.settle_configuration = settle

    async def _settle_configuration(self) -> None:
        """Ask the Host to land parked configuration, as the real Session does."""
        if self.settle_configuration is not None:
            await self.settle_configuration()

    @property
    def active_turn_id(self) -> str | None:
        return self._active_turn_id or self.reserved_turn_id

    @active_turn_id.setter
    def active_turn_id(self, value: str | None) -> None:
        self._active_turn_id = value

    def pin(self, pin: SessionPin) -> str | None:
        return self._pins.get(pin)

    async def persist_pin(self, pin: SessionPin, value: str) -> bool:
        if self._pins.get(pin) == value:
            return False
        self._pins[pin] = value
        return True

    def apply_adapter_config(self, adapter_config: object) -> None:
        self.applied.append(adapter_config)

    async def record_live_agent_change(self, adapter_config: object) -> None:
        pass

    async def apply_config(
        self, configuration: Any, *, allow_reserved_turn: bool = False
    ) -> None:
        # The real Harness rejects this mid-turn: Core reads its settings when
        # the turn starts and reconfigures them through its own command queue.
        # A double that accepted it anyway would let a caller that has to stay
        # off this path mid-turn pass here and fail in front of a user.
        from mistralai_vibe_local_harness.vibe import HarnessTurnConflictError

        self.allow_reserved_flags.append(allow_reserved_turn)
        if self._active_turn_id is not None:
            raise HarnessTurnConflictError(self._active_turn_id)
        if self.reserved_turn_id is not None and not allow_reserved_turn:
            raise HarnessTurnConflictError(self.reserved_turn_id)
        self.settings.append(configuration.core.settings)
        self.system_instructions.append(configuration.core.system_instructions)
        self.applied.append(configuration.local)
        self.capabilities.append(configuration.core.capabilities)
        self.plugins.append(None)
        self.core_configs.append(configuration.core)
        self.cwd = str(configuration.local.workspace.cwd)

    async def reconfigure_subagents(self, adapter_config: object) -> None:
        self.applied.append(adapter_config)

    async def apply_capabilities(
        self, capabilities: object, *, plugins: tuple[object, ...] | None = None
    ) -> None:
        self.capabilities.append(capabilities)
        self.plugins.append(plugins)

    async def start_turn(self, params: Any) -> Any:
        await self._settle_configuration()
        self.sent.append(params)
        turn = SimpleNamespace(id="turn-1", session_id=self.session_id, started_at=0)
        return SimpleNamespace(response=SimpleNamespace(turn=turn), after_response=None)

    async def start_deferred_turn(
        self, params: HarnessDeferredTurnStartParams
    ) -> HarnessDeferredTurnStartResult:
        self.sent.append(params)
        self.deferred_turn_params = params
        return HarnessDeferredTurnStartResult(
            turn_id="turn-1",
            session_id=self.session_id,
            started_at=0,
            last_event_id=0,
            after_response=lambda: None,
            on_response_abandoned=lambda: None,
        )

    async def enqueue_turn(self, params: Any) -> Any:
        from mistralai_vibe_local_harness.session_protocol import (
            TurnEnqueueResponse as HarnessTurnEnqueueResponse,
        )

        self.sent.append(params)
        return SimpleNamespace(
            response=HarnessTurnEnqueueResponse(queue_item_id="queue-1"),
            after_response=None,
        )

    async def replace_queued_turn(self, params: Any) -> Any:
        from mistralai_vibe_local_harness.session_protocol import (
            TurnQueueReplaceResponse as HarnessTurnQueueReplaceResponse,
        )

        self.sent.append(params)
        return SimpleNamespace(
            response=HarnessTurnQueueReplaceResponse(
                queue_item_id=params.queue_item_id
            ),
            after_response=None,
        )

    async def read_turn_queue(self, _params: Any) -> Any:
        from mistralai_vibe_local_harness.session_protocol import (
            QueuedTurn as HarnessQueuedTurn,
            TurnQueue as HarnessTurnQueue,
            TurnQueueReadResponse as HarnessTurnQueueReadResponse,
        )

        entries = next(
            params.entries
            for params in reversed(self.sent)
            if getattr(params, "entries", None) is not None
        )
        return SimpleNamespace(
            response=HarnessTurnQueueReadResponse(
                queue=HarnessTurnQueue(
                    items=[
                        HarnessQueuedTurn(id="queue-1", created_at=1, entries=entries)
                    ],
                    paused=False,
                    max_items=32,
                )
            ),
            after_response=None,
        )

    async def steer_queued_turn(self, params: Any) -> Any:
        self.sent.append(params)
        return SimpleNamespace(
            response=SimpleNamespace(
                queue_item_id=params.queue_item_id, turn_id=params.expected_turn_id
            ),
            after_response=None,
        )

    async def steer_turn(self, params: Any) -> Any:
        self.sent.append(params)
        return SimpleNamespace(
            response={"accepted": True, "last_event_id": 0}, after_response=None
        )

    async def inject_context(self, params: Any) -> Any:
        self.sent.append(params)
        return SimpleNamespace(response={"entries": []}, after_response=None)

    async def read(self, _params: Any) -> Any:
        """Replay everything sent so far as the session's public history.

        Core owns the model-visible history, so the adapter reads it back
        rather than tracking what it injected. The double keeps that loop
        closed: what goes out through a turn comes back as a user message.
        """
        from mistralai_vibe_local_harness.session_protocol import (
            IdleSessionStatus,
            LatestPublicHistoryPage,
            PublicSession as HarnessPublicSession,
            PublicSessionState as HarnessPublicSessionState,
            SessionSnapshot as HarnessSessionSnapshot,
            TurnQueue as HarnessTurnQueue,
        )

        entries = [
            {
                "type": "message",
                "id": f"entry-{index}",
                "sessionId": self.session_id,
                "createdAt": 1,
                "updatedAt": 1,
                "generationStatus": "completed",
                "role": "user",
                "content": [
                    {"type": "text", "text": block.text}
                    for block in blocks
                    if isinstance(block, TextContentBlock)
                ],
                "userDisplayContent": (
                    display.model_dump(mode="json") if display is not None else None
                ),
            }
            for index, (blocks, display) in enumerate(self._sent_messages())
        ]
        history_limit = getattr(_params, "history_limit", len(entries))
        entries = entries[-history_limit:] if history_limit else []
        return SimpleNamespace(
            snapshot=HarnessSessionSnapshot(
                state=HarnessPublicSessionState(
                    session=HarnessPublicSession(
                        id=self.session_id,
                        preview=self.preview,
                        status=IdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                    ),
                    history=LatestPublicHistoryPage(entries=entries),
                    turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
                ),
                history_limit=history_limit,
                watermark=0,
            )
        )

    def _sent_messages(self) -> list[tuple[list[Any], UserDisplayContent | None]]:
        sent = []
        for params in self.sent:
            blocks = getattr(params, "message", None) or getattr(params, "input", None)
            if blocks:
                sent.append((blocks, getattr(params, "user_display_content", None)))
        return sent


def _admin_result(
    outcome: AdminConfigOutcome, *, error: str | None = None
) -> AdminConfigApplyResult:
    return AdminConfigApplyResult(outcome, error=error)


def _stub_core_config() -> Any:
    """A real Core config for a stub derivation.

    ``_apply_derivation`` reads ``core_config.capabilities`` to push the skill
    catalogue, so a ``None`` here would only ever prove the stub is a stub.
    """
    from mistralai_vibe_local_harness.vibe._host import _core_config

    return _core_config("session-1")


def _stub_adapter_config() -> Any:
    """A real adapter config for a stub derivation.

    The Harness reads ``adapter_config.skills`` for model-issued skill calls, so
    a placeholder here fails on attribute access rather than on anything the
    test is about.
    """
    from mistralai_vibe_local_harness.vibe import LocalRuntimeAdapterConfig

    return LocalRuntimeAdapterConfig()


class _RecordingPermissions:
    """Stands in for the resolver so the assertion is about what the adapter records."""

    def __init__(self) -> None:
        self.grants: list[tuple[str, tuple[str, ...], bool]] = []

    async def grant(
        self, builtin: str, required_permissions: Any, *, permanent: bool
    ) -> None:
        self.grants.append((
            builtin,
            tuple(rp.session_pattern for rp in required_permissions),
            permanent,
        ))


def _empty_plugin_mcp() -> PluginMCPCatalog:
    return PluginMCPCatalog(MCPRegistry(), MCPAuthenticationService())


def _session_stub(**attributes: Any) -> Any:
    """A Session double carrying what the adapter reaches for as it is built.

    A class-based double states its own methods, so a port the adapter starts
    requiring fails there loudly. This is only for the namespace doubles.
    """
    return SimpleNamespace(
        configure_turn_settlement=lambda _settle: None,
        pin=lambda _pin: None,
        **attributes,
    )


def _worktree_progress(name: str | None) -> WorktreeProgress:
    return WorktreeProgress(
        name=name, feed=WorktreeProgressFeed(asyncio.get_running_loop())
    )


def _preparation_context(
    session_id: str,
    replace_pending_history_entries: Callable[[tuple[Any, ...]], Awaitable[None]]
    | None = None,
) -> HarnessDeferredTurnPreparationContext:
    return HarnessDeferredTurnPreparationContext(
        session_id=session_id,
        turn_id="turn-1",
        started_at=0,
        replace_pending_history_entries=(
            replace_pending_history_entries or AsyncMock()
        ),
    )


def _stub_unified_context(
    tmp_path: Path, orchestrator: Any, **context_overrides: Any
) -> tuple[Any, Any]:
    """The standard stub session context for adapter tests, plus its derivation.

    Tests that exercise one adapter operation rather than plugin or legacy
    machinery all need the same scaffolding: harness files, an agent manager, a
    derivation snapshot, and a context whose plugin and legacy surfaces are
    inert. Field differences (``account_gateway``, ``experiment_manager``)
    pass through ``context_overrides``.
    """
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedRuntimeDerivation,
        UnifiedSessionContext,
    )

    harness_files = get_harness_files_manager()
    agents = AgentManager(
        orchestrator, orchestrator.config.default_agent, harness_files=harness_files
    )
    derivation = UnifiedRuntimeDerivation(
        runtime=build_unified_runtime_snapshot(orchestrator, agents),
        core_config=_stub_core_config(),
        adapter_config=_stub_adapter_config(),
        skill_payloads={},
    )
    context = UnifiedSessionContext(
        storage_root=str(tmp_path),
        legacy_source_loader=cast(Any, None),
        legacy_source_resolver=cast(Any, None),
        plugins=cast(Any, object()),
        plugin_provider=cast(Any, object()),
        requested_plugins=(),
        config_orchestrator=cast(Any, orchestrator),
        harness_files=harness_files,
        agents=agents,
        derive=lambda _settings: derivation,
        permissions=cast(Any, None),
        mcp_catalog=ResolvedMCPCatalog(revision="test", servers=()),
        mcp_authorization_provider=MCPAuthenticationService(),
        plugin_mcp=_empty_plugin_mcp(),
        mcp_cache_root=str(tmp_path / "mcp-descriptors"),
        mcp_enable_system_trust_store=False,
        **context_overrides,
    )
    return context, derivation


def _inert_adapter(
    session: object,
    cwd: str | None,
    storage_root: str,
    *,
    runtime: object | None = None,
    host: object | None = None,
    services: object | None = None,
    deferred_turns: DeferredTurnStartCapability | None = None,
    telemetry_client: TelemetryClient | None = None,
    permissions: object | None = None,
    completion_attribution: object | None = None,
) -> Any:
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedRuntimeDerivation,
        UnifiedSessionContext,
    )

    context = UnifiedSessionContext(
        storage_root=storage_root,
        legacy_source_loader=cast(Any, None),
        legacy_source_resolver=cast(Any, None),
        plugins=cast(Any, object()),
        plugin_provider=cast(Any, object()),
        requested_plugins=(),
        config_orchestrator=cast(Any, None),
        harness_files=cast(Any, None),
        agents=cast(Any, None),
        derive=cast(Any, None),
        permissions=cast(Any, permissions),
        mcp_catalog=ResolvedMCPCatalog(revision="test", servers=()),
        mcp_authorization_provider=MCPAuthenticationService(),
        plugin_mcp=_empty_plugin_mcp(),
        mcp_cache_root=str(Path(storage_root) / "mcp-descriptors"),
        mcp_enable_system_trust_store=False,
    )
    derivation = UnifiedRuntimeDerivation(
        runtime=cast(Any, runtime if runtime is not None else object()),
        core_config=_stub_core_config(),
        adapter_config=_stub_adapter_config(),
        skill_payloads={},
    )
    cast(Any, session).cwd = cwd
    return UnifiedHarnessBackendAdapter(
        cast(Any, session),
        context,
        derivation,
        deferred_turns=deferred_turns,
        host=cast(Any, host),
        services=cast(Any, services),
        telemetry_client=telemetry_client,
        completion_attribution=cast(Any, completion_attribution),
    )


@pytest.mark.asyncio
async def test_background_process_output_tolerates_unknown_harness_fields(
    tmp_path: Path,
) -> None:
    """*Prepare*: A Harness output page with a field unknown to this app-server.
    *Do*: Read the page through the Unified Harness adapter.
    *Assert*: The additive field is ignored and the known output is preserved.
    """

    class ForwardCompatibleOutput:
        availability = "available"

        def model_dump(
            self, *, mode: str = "json", by_alias: bool = True
        ) -> dict[str, object]:
            return {
                "availability": "available",
                "processId": "process-1",
                "outputBase64": base64.b64encode(b"ready").decode("ascii"),
                "outputStartCursor": 0,
                "nextCursor": 5,
                "bytesAvailable": 5,
                "hasMore": False,
                "truncatedBefore": False,
                "isFinal": True,
                "newHarnessField": "additive",
            }

    class FakeSession(_RecordingSession):
        async def read_background_process_output(self, _request: object) -> object:
            return ForwardCompatibleOutput()

    # Prepare
    adapter = _inert_adapter(FakeSession(), None, str(tmp_path))

    # Do
    result = await adapter.dispatch_extension(
        "session/backgroundProcess/output",
        {
            "sessionId": "session-1",
            "processId": "process-1",
            "fromEnd": False,
            "cursor": 0,
            "waitMs": 0,
            "maxBytes": 64_000,
        },
    )

    # Assert
    assert result.response.process_id == "process-1"
    assert result.response.output_base64 == base64.b64encode(b"ready").decode("ascii")


@pytest.mark.parametrize(
    ("windows", "git_bash_path", "expected"),
    [
        (False, None, "unix"),
        (True, "C:/Program Files/Git/bin/bash.exe", "git_bash"),
        (True, None, "powershell"),
    ],
)
def test_unified_command_environment_follows_platform_shell_support(
    monkeypatch: pytest.MonkeyPatch,
    windows: bool,
    git_bash_path: str | None,
    expected: str,
) -> None:
    monkeypatch.setattr(runtime_module, "is_windows", lambda: windows)
    monkeypatch.setattr(runtime_module, "get_windows_bash_path", lambda: git_bash_path)

    assert runtime_module._command_environment_mode() == expected


def test_published_mcp_names_invert_the_routes_the_runtime_normalized() -> None:
    """A route is built from normalized halves; the config key keeps the raw ones.

    Kebab case, a leading digit and a post-normalization collision all move the
    route away from the name Vibe published, so nothing but the snapshot pairs
    the two -- and a wrong pairing reads an unwritten key, which asks rather
    than honouring a configured ``never``.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._mcp_models import (
        MCPAuthorizationRef,
        MCPRemoteToolDescriptor,
        ResolvedMCPServerConfig,
    )
    from mistralai_vibe_local_harness.vibe._mcp_naming import build_route_snapshot
    from vibe.app_server._unified_harness_backend_adapter import _published_mcp_names

    server = ResolvedMCPServerConfig(
        name="data-api",
        transport="stdio",
        command="server",
        authorization=MCPAuthorizationRef("data-api", "fingerprint", "none", "rev-1"),
    )
    remote_names = ("create-issue", "1st-query", "search-issues", "search_issues")
    snapshot = build_route_snapshot(
        catalog_revision="catalog-1",
        resolved=[
            (
                server,
                tuple(MCPRemoteToolDescriptor(remote_name=n) for n in remote_names),
            )
        ],
        sources=(),
    )

    names = _published_mcp_names(snapshot)

    assert set(names) == {f"mcp_data_api.{route[1]}" for route in snapshot.routes}
    assert sorted(names.values()) == [
        "data-api_1st-query",
        "data-api_create-issue",
        "data-api_search-issues",
        "data-api_search_issues",
    ]


def test_published_connector_names_invert_the_routes_the_runtime_normalized() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._connector_models import (
        ResolvedConnector,
        ResolvedConnectorCatalog,
        ResolvedConnectorSelection,
        ResolvedConnectorTool,
    )
    from mistralai_vibe_local_harness.vibe._connector_naming import (
        build_connector_snapshot,
    )
    from vibe.app_server._unified_harness_backend_adapter import (
        _published_connector_names,
    )

    snapshot = build_connector_snapshot(
        catalog=ResolvedConnectorCatalog(
            revision="catalog-1",
            connectors=(
                ResolvedConnector(
                    raw_id="raw-github",
                    alias="git-hub",
                    display_name="GitHub",
                    ready=True,
                    auth_action="none",
                    tools=(
                        ResolvedConnectorTool("create-issue", None, {}),
                        ResolvedConnectorTool("1st-query", None, {}),
                    ),
                ),
            ),
        ),
        selection=ResolvedConnectorSelection(
            selection_revision="sel-1",
            enable_connectors=True,
            implicit_source_enabled=True,
            connector_settings=(),
            enabled_tools=(),
            disabled_tools=(),
        ),
    )

    assert _published_connector_names(snapshot) == {
        "connector_git_hub.create_issue": "connector_git-hub_create-issue",
        "connector_git_hub._1st_query": "connector_git-hub_1st-query",
    }


def test_unified_connector_state_flattens_remote_tool_descriptions() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe import (
        ConnectorRouteSnapshot,
        ConnectorSourceState,
    )
    from mistralai_vibe_local_harness.vibe._connector_models import (
        ConnectorToolDescriptor,
    )
    from vibe.app_server._unified_harness_backend_adapter import (
        _session_connector_state,
    )

    def descriptor(remote_name: str, description: str) -> Any:
        return ConnectorToolDescriptor(
            raw_connector_id="raw-github",
            alias="github",
            remote_name=remote_name,
            group_name="github",
            programmatic_name=f"connector_github_{remote_name}",
            display_name=f"connector_github_{remote_name}",
            description=description,
            input_schema={"type": "object"},
            enabled=True,
        )

    state = _session_connector_state(
        ConnectorRouteSnapshot(
            catalog_revision="cat-1",
            selection_revision="sel-1",
            route_revision="route-1",
            groups=(),
            routes={},
            sources=(
                ConnectorSourceState(
                    raw_id="raw-github",
                    alias="github",
                    display_name="GitHub",
                    status="connected",
                    tools=(
                        descriptor("search", "Search issues.\n\nUsage notes go here."),
                        descriptor("create", "[github] Create an issue."),
                        descriptor("plain", "Already one line."),
                    ),
                ),
            ),
        )
    )

    # The `/mcp` detail view renders one non-wrapping row per tool, so the
    # harness must not leak multi-line remote descriptions past this seam.
    assert [tool.description for tool in state.sources[0].tools] == [
        "Search issues.",
        "Create an issue.",
        "Already one line.",
    ]


@pytest.mark.parametrize(
    ("active_model", "pinned"),
    [("beta", True), ("", False)],
    ids=["pinned", "unpinned"],
)
def test_unified_runtime_snapshot_reports_the_configured_default_model(
    active_model: str, pinned: bool
) -> None:
    """The snapshot must carry the configured default, not the active model.

    The unified snapshot projected the config view without
    ``active_model_pinned``, so ``/model`` showed "Default (currently <pin>)"
    and kept its current-marker on the ``Default`` row while a pin was active.
    ``config/read`` already passed the flag, so the two disagreed.
    """
    from vibe.core.config import ModelConfig

    models = [
        ModelConfig(name="model-a", provider="mistral", alias="alpha"),
        ModelConfig(name="model-b", provider="mistral", alias="beta"),
    ]
    harness_files = get_harness_files_manager()
    orchestrator = FakeConfigOrchestrator(
        build_test_vibe_config(models=models, active_model=active_model)
    )
    agents = AgentManager(
        orchestrator, orchestrator.config.default_agent, harness_files=harness_files
    )

    snapshot = build_unified_runtime_snapshot(orchestrator, agents)

    assert snapshot.config.active_model_pinned is pinned
    assert snapshot.config.default_model_alias == "alpha"
    assert snapshot.config.active_model.alias == (active_model or "alpha")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entrypoint", "expect_title_model"),
    [
        ("cli", True),
        ("desktop", True),
        ("acp", False),
        ("programmatic", False),
        ("unknown", False),
    ],
)
async def test_unified_title_generation_gated_on_entrypoint(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
    expect_title_model: bool,
) -> None:
    """Only cli/desktop clients get a background title model, matching legacy."""
    monkeypatch.setenv("VIBE_SESSION_LOGGING__GENERATE_TITLES", "true")
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["session_logging"] = {"generate_titles": True}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = runtime_module.HarnessProcess(experimental_harness=True)
    try:
        context = await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path)), entrypoint=cast(Any, entrypoint)
        )
        derivation = context.derive(UnifiedSessionSettings())
    finally:
        await process.close()

    has_title_model = derivation.adapter_config.title_model is not None
    assert has_title_model is expect_title_model


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("configured", "expected"), [(None, None), (0.0, None), (600.0, 600.0)]
)
async def test_unified_stream_idle_timeout_follows_config(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured: float | None,
    expected: float | None,
) -> None:
    """The stream idle limit is off unless ``api_stream_idle_timeout`` sets one."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    if configured is not None:
        config_file = config_dir / "config.toml"
        config = tomllib.loads(config_file.read_text(encoding="utf-8"))
        config["api_stream_idle_timeout"] = configured
        config_file.write_text(tomli_w.dumps(config), encoding="utf-8")

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = runtime_module.HarnessProcess(experimental_harness=True)
    try:
        context = await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path)), entrypoint="desktop"
        )
        derivation = context.derive(UnifiedSessionSettings())
    finally:
        await process.close()

    assert derivation.adapter_config.stream_idle_timeout_s == expected


@pytest.mark.asyncio
async def test_unified_title_model_uses_active_model_on_unprobed_custom_endpoint(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["providers"][0]["api_base"] = "https://customer.mistral.ai/v1"
    config["session_logging"] = {"generate_titles": True}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = runtime_module.HarnessProcess(experimental_harness=True)
    try:
        context = await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path)), entrypoint="desktop"
        )
        derivation = context.derive(UnifiedSessionSettings())
    finally:
        await process.close()

    assert derivation.adapter_config.title_model is not None
    assert derivation.adapter_config.title_model.model == "mistral-vibe-cli-latest"
    assert derivation.adapter_config.title_provider is None


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_unified_title_model_rides_the_session_model_even_when_probed(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The title completion sees the session transcript, so it stays on the
    session's own model and provider even when a fast model is available.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings
    from vibe.core.config import ProviderConfig
    from vibe.core.llm.model_probe import MODEL_AVAILABILITY
    from vibe.core.llm.utility_completion import FAST_MODEL_CANDIDATES
    from vibe.core.types import Backend

    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["providers"][0]["api_base"] = "https://customer.mistral.ai/v1"
    config["session_logging"] = {"generate_titles": True}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    MODEL_AVAILABILITY.remember(
        provider=ProviderConfig(
            name="mistral",
            api_base="https://customer.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
            backend=Backend.MISTRAL,
        ),
        model=FAST_MODEL_CANDIDATES[0],
        available=True,
    )

    process = runtime_module.HarnessProcess(experimental_harness=True)
    try:
        context = await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path)), entrypoint="desktop"
        )
        derivation = context.derive(UnifiedSessionSettings())
    finally:
        await process.close()

    assert derivation.adapter_config.title_model is not None
    assert derivation.adapter_config.title_model.model == "mistral-vibe-cli-latest"
    assert derivation.adapter_config.title_provider is None


@pytest.mark.asyncio
async def test_unified_runtime_enables_large_output_offloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A Unified Harness session context for a local CLI workspace.
    *Do*: Derive the Core configuration used to start the session.
    *Assert*: Large outputs use the same filesystem thresholds as Vibe Work.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.protocol import RustFilesystemLargeOutputPolicy
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = runtime_module.HarnessProcess(experimental_harness=True)
    try:
        context = await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path))
        )

        # Do
        derivation = context.derive(UnifiedSessionSettings())
    finally:
        await process.close()

    # Assert
    assert (
        derivation.core_config.settings.tools.large_output
        == RustFilesystemLargeOutputPolicy()
    )


@pytest.mark.asyncio
async def test_unified_runtime_declares_the_vibe_tool_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A Unified Harness session context for a local CLI workspace.
    *Do*: Derive the Core configuration used to start the session.
    *Assert*: The `vibe` group carries the tools the Harness does not ship, and
    no builtin hook is bound.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = runtime_module.HarnessProcess(experimental_harness=True)
    try:
        context = await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path))
        )

        # Do
        derivation = context.derive(UnifiedSessionSettings())
    finally:
        await process.close()

    # Assert
    groups = {
        group.name: [tool.name for tool in group.tools]
        for group in derivation.core_config.capabilities.tool_groups
    }
    assert groups["vibe"] == ["todo", SCRATCHPAD_TOOL_NAME, "cron"]
    # Declaring a second group must not displace the one that was already there.
    assert groups["ui"] == ["ask_user_question"]
    # Vibe binds no builtin hook: a non-empty binding list defers `commit_turn` for
    # every session that derives from this config, subagents included.
    assert derivation.core_config.capabilities.hook_bindings == []


def test_unified_mcp_projection_update_preserves_connector_sources() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    class FakeSession:
        session_id = "session-1"

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

    runtime = build_runtime_snapshot(
        SessionOptions(),
        FakeConfigOrchestrator(build_test_vibe_config()),
        get_harness_files_manager(),
    ).model_copy(
        update={
            "mcp": MCPState(
                sources=[
                    MCPSourceSummary(
                        name="github",
                        kind=MCPSourceKind.CONNECTOR,
                        transport="connector",
                        status=MCPSourceStatus.CONNECTED,
                    )
                ],
                discovery_errors={"github": "connector error"},
                connector_error="bootstrap warning",
            )
        }
    )
    adapter = _inert_adapter(FakeSession(), None, ".", runtime=runtime)

    adapter.update_mcp_projection(
        MCPState(
            sources=[
                MCPSourceSummary(
                    name="local",
                    kind=MCPSourceKind.SERVER,
                    transport="stdio",
                    status=MCPSourceStatus.ENABLED,
                )
            ],
            discovery_errors={"local": "server error"},
        )
    )

    projected = adapter.runtime_updated_params().runtime.mcp
    assert [(source.name, source.kind) for source in projected.sources] == [
        ("local", MCPSourceKind.SERVER),
        ("github", MCPSourceKind.CONNECTOR),
    ]
    assert projected.discovery_errors == {
        "local": "server error",
        "github": "connector error",
    }
    assert projected.connector_error == "bootstrap warning"


def test_mcp_catalog_carries_the_global_tool_globs() -> None:
    """*Prepare*: A config with global enabled/disabled tool globs.
    *Do*: Resolve them for the harness MCP catalog.
    *Assert*: The filter rides the catalog; an unconstrained config sends none.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        _harness_mcp_catalog,
        _mcp_tool_filter,
    )

    enabled = _mcp_tool_filter(build_test_vibe_config(enabled_tools=["linear_*"]))
    assert enabled is not None
    assert enabled.allows("linear_get_issue")
    assert not enabled.allows("github_create_pr")

    denied = _mcp_tool_filter(
        build_test_vibe_config(disabled_tools=["github_create_*"])
    )
    assert denied is not None
    assert denied.allows("github_get_issue")
    assert not denied.allows("github_create_pr")

    catalog = _harness_mcp_catalog(
        ResolvedMCPCatalog(revision="test", servers=()), enabled
    )
    assert catalog.tool_filter is enabled

    assert _mcp_tool_filter(build_test_vibe_config()) is None


@pytest.mark.asyncio
async def test_legacy_session_start_records_the_legacy_harness(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client_transport, server_transport = memory_transport_pair()
    server = build_test_app_server(build_test_agent_loop(), server_transport)
    client = AppServerClient(client_transport, run_peer=server.serve)

    with caplog.at_level("DEBUG", logger="vibe"):
        session = await AppServerSession.start(
            client,
            client_info=ClientInfo(name="test", version="0"),
            capabilities=ClientCapabilities(),
        )
        try:
            recorded = _recorded_sessions(caplog)
        finally:
            await session.close()

    assert recorded == [("legacy", session.session_id)]


@pytest.mark.asyncio
async def test_unified_harness_session_start_records_the_unified_harness(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, server = _connect_harness_host()

    with caplog.at_level("DEBUG", logger="vibe"):
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        try:
            started = SessionReadResponse.model_validate(
                await client.request("session/start", SessionStartParams())
            )
        finally:
            await server.close()

    recorded = _recorded_sessions(caplog)
    assert recorded == [("unified", started.state.session.id)]
    assert started.state.history == []
    assert started.state.session.cwd is not None


@pytest.mark.asyncio
async def test_unified_adapter_tracks_open_callbacks_for_delivery_lifecycle(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._session import _approval_callback

    class FakeSession:
        session_id = "session-1"
        rejected: object | None = None

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        async def respond_to_callback(self, params: object) -> object:
            self.rejected = params
            return object()

    fake_session = FakeSession()
    adapter = _inert_adapter(fake_session, None, str(tmp_path))
    callback = _approval_callback(
        session_id="session-1",
        callback_id="approval-call-1",
        action=_approval_action("turn-1"),
        created_at=1,
        input_entry_id="user-entry-1",
        required_permissions=(),
    )

    events = adapter._callback_events({
        "type": "callback_requested",
        "callback": callback,
    })

    assert events is not None
    assert isinstance(adapter.open_callbacks()[0], PublicCallbackEntry)
    assert adapter.open_callbacks()[0].callback_id == "approval-call-1"
    assert adapter.open_callbacks()[0].input_entry_id == "user-entry-1"
    assert adapter.open_callbacks()[0].related_entry_id == "effect-action-1"
    detail = cast(Any, adapter.open_callbacks()[0].detail)
    assert detail.related_entry_id == "effect-action-1"
    assert isinstance(detail.effect, ShellEffectDetail)
    assert detail.effect.input is not None
    assert detail.effect.input.command == "echo hi"

    await adapter.reject_callback_delivery(
        "session-1", "approval-call-1", CallbackResultError(message="not delivered")
    )

    assert fake_session.rejected is not None
    rejected = cast(Any, fake_session.rejected)
    assert rejected.result.callback_id == "approval-call-1"
    assert rejected.result.error.message == "not delivered"


@pytest.mark.asyncio
async def test_unified_adapter_adds_path_scope_choices_to_approval_callbacks(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._session import _approval_callback

    class FakeSession:
        session_id = "session-1"

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

    adapter = _inert_adapter(FakeSession(), None, str(tmp_path))
    callback = _approval_callback(
        session_id="session-1",
        callback_id="approval-call-1",
        action=_approval_action("turn-1"),
        created_at=1,
        input_entry_id="user-entry-1",
        required_permissions=(
            cast(
                Any,
                {
                    "scope": "outside_directory",
                    "invocationPattern": "/outside/missing",
                    "sessionPattern": "vibe-path:exact:/outside/missing",
                    "label": "outside workdir (/outside/missing)",
                },
            ),
        ),
    )

    events = adapter._callback_events({
        "type": "callback_requested",
        "callback": callback,
    })

    assert events is not None
    [opened] = adapter.open_callbacks()
    detail = cast(Any, opened.detail)
    assert detail.path_scope_choices == [PathGrantScope.EXACT]


@pytest.mark.asyncio
async def test_unified_adapter_routes_child_callback_through_the_local_host(
    tmp_path: Path,
) -> None:
    """*Prepare*: A child callback is registered under an attached Unified root.
    *Do*: Reject delivery through the root adapter.
    *Assert*: The Host receives the result addressed to the child Session.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._session import _approval_callback

    callback = _approval_callback(
        session_id="session-child",
        callback_id="approval-child-call",
        action=_approval_action("child-turn-1"),
        created_at=1,
        input_entry_id="user-child-entry-1",
        required_permissions=(),
    )

    class FakeSession:
        session_id = "session-root"

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        async def respond_to_callback(self, _params: object) -> object:
            raise AssertionError("child callback must not be sent to the root Session")

    class FakeHost:
        routed: tuple[str, object] | None = None

        def open_callbacks(self, root_session_id: str) -> tuple[dict[str, Any], ...]:
            assert root_session_id == "session-root"
            return (callback,)

        def references_child(self, root_session_id: str, child_session_id: str) -> bool:
            return (
                root_session_id == "session-root"
                and child_session_id == "session-child"
            )

        async def respond_to_callback(
            self, root_session_id: str, params: object
        ) -> object:
            self.routed = (root_session_id, params)
            return object()

    host = FakeHost()
    adapter = _inert_adapter(FakeSession(), None, str(tmp_path), host=host)

    # Do
    await adapter.reject_callback_delivery(
        "session-child",
        "approval-child-call",
        CallbackResultError(message="not delivered"),
    )

    # Assert
    assert adapter.references_child("session-child")
    assert adapter.open_callbacks()[0].session_id == "session-child"
    assert host.routed is not None
    root_session_id, routed = host.routed
    assert root_session_id == "session-root"
    assert cast(Any, routed).session_id == "session-child"


@pytest.mark.asyncio
async def test_unified_adapter_reads_and_pages_child_history_through_the_host(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    class FakeSession(_RecordingSession):
        def __init__(self) -> None:
            super().__init__()
            self.read_session_ids: list[str] = []

        async def read(self, params: Any) -> object:
            self.read_session_ids.append(params.session_id)
            return await super().read(params)

    class FakeHost:
        def __init__(self) -> None:
            self.read_session_ids: list[str] = []

        def references_child(self, root_session_id: str, child_session_id: str) -> bool:
            return (
                root_session_id == "session-1" and child_session_id == "session-child"
            )

        async def read(self, params: Any) -> object:
            self.read_session_ids.append(params.session_id)
            result = await child.read(params)
            return SimpleNamespace(snapshot=result.snapshot, cwd=str(tmp_path))

    session = FakeSession()
    session.sent.append(SimpleNamespace(message=[TextContentBlock(text="root")]))
    child = _RecordingSession()
    child.session_id = "session-child"
    child.sent.append(SimpleNamespace(message=[TextContentBlock(text="child")]))
    host = FakeHost()
    adapter = _inert_adapter(session, None, str(tmp_path), host=host)

    read = await adapter.read(
        SessionReadParams(
            session_id="session-child", history=PageRequest(limit=20), turns=None
        )
    )

    result = await adapter.dispatch_extension(
        "session/history/list",
        {"sessionId": "session-child", "page": {"limit": 20, "direction": "backward"}},
    )

    assert read.state.session.id == "session-child"
    assert [entry.session_id for entry in read.state.history or []] == ["session-child"]
    assert [entry.session_id for entry in result.response.items] == ["session-child"]
    assert session.read_session_ids == []
    assert host.read_session_ids == ["session-child", "session-child"]

    with pytest.raises(SessionBackendError) as exc_info:
        await adapter.dispatch_extension(
            "session/history/list",
            {
                "sessionId": "session-unrelated",
                "page": {"limit": 20, "direction": "backward"},
            },
        )

    assert exc_info.value.code is ProtocolErrorCode.NOT_FOUND
    assert session.read_session_ids == []
    assert host.read_session_ids == ["session-child", "session-child"]


@pytest.mark.asyncio
async def test_unified_history_pages_back_to_the_first_entry_beyond_the_read_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import vibe.app_server._unified_harness_backend_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "_HISTORY_PAGE_WINDOW", 3)
    session = _RecordingSession()
    for index in range(10):
        session.sent.append(
            SimpleNamespace(message=[TextContentBlock(text=f"message {index}")])
        )
    adapter = _inert_adapter(session, None, str(tmp_path))

    read = await adapter.read(
        SessionReadParams(
            session_id=session.session_id, history=PageRequest(limit=2), turns=None
        )
    )
    paged = [entry.id for entry in read.state.history or []]
    cursor = read.state.history_before_cursor
    while cursor is not None:
        result = await adapter.dispatch_extension(
            "session/history/list",
            {
                "sessionId": session.session_id,
                "page": {"cursor": cursor, "limit": 2, "direction": "backward"},
            },
        )
        paged = [*(entry.id for entry in result.response.items), *paged]
        cursor = result.response.next_cursor

    assert read.state.history_before_cursor == "entry-8"
    assert paged == [f"entry-{index}" for index in range(10)]


@pytest.mark.asyncio
async def test_unified_history_paging_stops_when_a_read_ignores_its_limit(
    tmp_path: Path,
) -> None:
    from vibe.app_server._unified_harness_backend_adapter import _HistoryWindows
    from vibe.app_server.protocol import SessionHistoryListParams

    session = _RecordingSession()
    for index in range(5):
        session.sent.append(
            SimpleNamespace(message=[TextContentBlock(text=f"message {index}")])
        )
    adapter = _inert_adapter(session, None, str(tmp_path))
    truncated = (
        await adapter.read(
            SessionReadParams(
                session_id=session.session_id, history=PageRequest(limit=2), turns=None
            )
        )
    ).state
    reads: list[int] = []

    async def cached_read(history_limit: int) -> Any:
        reads.append(history_limit)
        return truncated

    page = await asyncio.wait_for(
        _HistoryWindows().list(
            SessionHistoryListParams(
                session_id=session.session_id,
                page=PageRequest(cursor="entry-3", limit=2),
            ),
            cached_read,
        ),
        timeout=5,
    )

    assert truncated.history_before_cursor == "entry-3"
    assert len(reads) == 2
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_unified_history_window_size_is_kept_until_paging_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import vibe.app_server._unified_harness_backend_adapter as adapter_module
    from vibe.app_server._unified_harness_backend_adapter import _HistoryWindows
    from vibe.app_server.protocol import SessionHistoryListParams

    monkeypatch.setattr(adapter_module, "_HISTORY_PAGE_WINDOW", 3)
    session = _RecordingSession()
    for index in range(8):
        session.sent.append(
            SimpleNamespace(message=[TextContentBlock(text=f"message {index}")])
        )
    adapter = _inert_adapter(session, None, str(tmp_path))
    windows = _HistoryWindows()

    reads: list[int] = []

    async def read(history_limit: int) -> Any:
        reads.append(history_limit)
        result = await adapter.read(
            SessionReadParams(
                session_id=session.session_id,
                history=PageRequest(limit=history_limit),
                turns=None,
            )
        )
        return result.state

    def page(cursor: str, limit: int) -> SessionHistoryListParams:
        return SessionHistoryListParams(
            session_id=session.session_id, page=PageRequest(cursor=cursor, limit=limit)
        )

    partial_page = await windows.list(page("entry-6", 2), read)
    assert partial_page.next_cursor == "entry-4"
    assert windows._sizes == {session.session_id: 6}

    complete_page = await windows.list(page("entry-2", 1), read)
    assert complete_page.next_cursor == "entry-1"
    assert windows._sizes == {session.session_id: 12}

    reads.clear()
    final_page = await windows.list(page("entry-1", 1), read)
    assert final_page.next_cursor is None
    assert reads == [12]
    assert windows._sizes == {}


def test_unified_history_windows_evict_the_least_recently_paged_session() -> None:
    import vibe.app_server._unified_harness_backend_adapter as adapter_module
    from vibe.app_server._unified_harness_backend_adapter import _HistoryWindows

    windows = _HistoryWindows()
    for index in range(adapter_module._HISTORY_WINDOW_SESSIONS + 1):
        windows._remember(f"session-{index}", 3)

    assert len(windows._sizes) == adapter_module._HISTORY_WINDOW_SESSIONS
    assert "session-0" not in windows._sizes


@pytest.mark.asyncio
async def test_unified_rewind_finds_an_entry_beyond_the_read_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import vibe.app_server._unified_harness_backend_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "_HISTORY_PAGE_WINDOW", 3)
    session = _RecordingSession()
    for index in range(10):
        session.sent.append(
            SimpleNamespace(message=[TextContentBlock(text=f"message {index}")])
        )
    adapter = _inert_adapter(session, None, str(tmp_path))

    entry = await adapter._rewound_entry("entry-0")

    assert entry.id == "entry-0"


@pytest.mark.asyncio
async def test_unified_adapter_rejects_direct_child_turn_mutation(
    tmp_path: Path,
) -> None:
    """*Prepare*: A root adapter recognizes a parent-owned child Session.
    *Do*: Address a new turn directly to the child through the public backend.
    *Assert*: The adapter rejects it as model-owned without calling the root Session.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    class FakeHost:
        def references_child(self, root_session_id: str, child_session_id: str) -> bool:
            return (
                root_session_id == "session-1" and child_session_id == "session-child"
            )

    session = _RecordingSession()
    adapter = _inert_adapter(session, None, str(tmp_path), host=FakeHost())

    # Do
    with pytest.raises(SessionBackendError) as exc_info:
        await adapter.start_turn(
            TurnStartParams(
                session_id="session-child",
                message=[TextContentBlock(text="bypass the parent")],
            )
        )

    # Assert
    assert exc_info.value.code is ProtocolErrorCode.FORBIDDEN
    assert exc_info.value.data == {
        "reason": "child_model_owned",
        "sessionId": "session-child",
    }
    assert session.sent == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("approve", []),
        ("approve_for_session", [("file_system.bash", ("npm test *",), False)]),
        ("approve_permanently", [("file_system.bash", ("npm test *",), True)]),
        ("deny", []),
    ],
)
async def test_unified_adapter_records_only_an_approval_that_outlives_the_call(
    tmp_path: Path, decision: str, expected: list[Any]
) -> None:
    """*Prepare*: An open approval callback naming the command pattern it needs.
    *Do*: Answer it with each decision the client can send.
    *Assert*: Only the widened approvals are recorded, scoped to that pattern. The
    Runtime collapses every yes to one boolean, so "once" and "for the session"
    are indistinguishable downstream unless the difference is kept here.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._session import _approval_callback
    from vibe.app_server.protocol import CallbackResult, CallbackResultParams

    # Prepare
    class FakeSession:
        session_id = "session-1"

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        async def respond_to_callback(self, _params: object) -> object:
            return SimpleNamespace(
                response={"accepted": True, "last_event_id": 1}, after_response=None
            )

    permissions = _RecordingPermissions()
    adapter = _inert_adapter(
        FakeSession(), None, str(tmp_path), permissions=permissions
    )
    adapter._callback_events({
        "type": "callback_requested",
        "callback": _approval_callback(
            session_id="session-1",
            callback_id="approval-call-1",
            action=_approval_action("turn-1"),
            created_at=1,
            input_entry_id="user-entry-1",
            required_permissions=(
                cast(
                    Any,
                    {
                        "scope": "command_pattern",
                        "invocationPattern": "npm test",
                        "sessionPattern": "npm test *",
                        "label": "npm test *",
                    },
                ),
            ),
        ),
    })

    # Do
    await adapter.respond_to_callback(
        CallbackResultParams(
            session_id="session-1",
            result=CallbackResult(
                callback_id="approval-call-1",
                output={"type": "approval", "decision": {"type": decision}},
            ),
        )
    )

    # Assert
    assert permissions.grants == expected


@pytest.mark.asyncio
async def test_unified_adapter_forwards_an_unoffered_path_scope_without_granting_it(
    tmp_path: Path,
) -> None:
    """An invalid sticky scope must not leave the Harness callback open."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._session import _approval_callback
    from vibe.app_server.protocol import CallbackResult, CallbackResultParams

    class FakeSession:
        session_id = "session-1"
        received: object | None = None

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        async def respond_to_callback(self, params: object) -> object:
            self.received = params
            return SimpleNamespace(
                response={"accepted": True, "last_event_id": 1}, after_response=None
            )

    session = FakeSession()
    permissions = _RecordingPermissions()
    adapter = _inert_adapter(session, None, str(tmp_path), permissions=permissions)
    adapter._callback_events({
        "type": "callback_requested",
        "callback": _approval_callback(
            session_id="session-1",
            callback_id="approval-call-1",
            action=_approval_action("turn-1"),
            created_at=1,
            input_entry_id="user-entry-1",
            required_permissions=(
                cast(
                    Any,
                    {
                        "scope": "outside_directory",
                        "invocationPattern": "/outside/config.json",
                        "sessionPattern": "vibe-path:exact:/outside/config.json",
                        "label": "outside workdir (/outside/config.json)",
                    },
                ),
            ),
        ),
    })
    params = CallbackResultParams(
        session_id="session-1",
        result=CallbackResult(
            callback_id="approval-call-1",
            output={
                "type": "approval",
                "decision": {
                    "type": "approve_for_session",
                    "pathScope": "directory_recursive",
                },
            },
        ),
    )

    response = await adapter.respond_to_callback(params)

    assert response.response.accepted
    assert session.received == params
    assert permissions.grants == []


@pytest.mark.asyncio
async def test_unified_harness_projects_the_session_config_as_its_runtime() -> None:
    client, server = _connect_harness_host()

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        runtime = RuntimeReadResponse.model_validate(
            await client.request(
                "runtime/read", RuntimeReadParams(session_id=started.state.session.id)
            )
        )
    finally:
        await server.close()

    # The Harness owns no Vibe runtime yet, so agents and models come from the
    # session config while everything an `AgentLoop` would supply stays empty.
    assert runtime.ready
    assert runtime.runtime.active_agent.name
    assert runtime.runtime.config.active_model.alias
    assert runtime.runtime.tools == []


def test_unified_image_projection_is_a_valid_public_message() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.protocol import (
        RustIdleTurn,
        RustImageContentBlock,
        RustNoNextAction,
        RustSessionTransition,
        RustTextContentBlock,
        RustTurnStartedObservation,
    )
    from mistralai_vibe_local_harness.session_protocol import (
        TURN_QUEUE_MAX_ITEMS,
        HistoryCursor,
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._projection import SessionProjector
    from mistralai_vibe_local_harness.vibe._storage import ProjectionStateV1

    session_id = "019ffb1e-741d-7f90-84df-ef66011876ca"
    transition = RustSessionTransition(
        protocol_version=1,
        input_id=1,
        next=RustNoNextAction(),
        observations=[
            RustTurnStartedObservation(
                turn_id="turn-1",
                content=[
                    RustTextContentBlock(text="describe image"),
                    RustImageContentBlock(data="aW1hZ2U=", mime_type="image/png"),
                ],
            )
        ],
        turn=RustIdleTurn(),
    )
    projector = SessionProjector(
        ProjectionStateV1(
            session_id=session_id,
            snapshot_sequence=0,
            watermark=0,
            snapshot=HarnessPublicSessionState(
                session=HarnessPublicSession(
                    id=session_id,
                    status=IdleSessionStatus(),
                    created_at=1,
                    updated_at=1,
                ),
                history=LatestPublicHistoryPage(cursor=HistoryCursor()),
                turn_queue=HarnessTurnQueue(
                    items=[], paused=False, max_items=TURN_QUEUE_MAX_ITEMS
                ),
            ),
        )
    )

    raw = projector.apply(
        transition, observed_at=2
    ).projection.snapshot.history.entries[0]
    message = validate_history_entry(raw)

    assert isinstance(message, PublicMessageEntry)
    image = cast(Any, message.content[1])
    assert image.attachment.source.kind == "inline"
    assert image.attachment.source.data == "aW1hZ2U="
    assert image.attachment.mime_type == "image/png"


@pytest.mark.asyncio
async def test_unified_harness_history_resource_reads_the_backend_snapshot() -> None:
    client, server = _connect_harness_host()
    session = await AppServerSession.start(
        client,
        client_info=ClientInfo(name="test", version="0"),
        capabilities=ClientCapabilities(),
    )

    try:
        history = await session.resources.sessions.get_session_history(
            session.session_id
        )
    finally:
        await session.close()

    assert history == []


@pytest.mark.parametrize("failed", [False, True])
def test_unified_tool_result_projection_is_a_valid_public_effect(failed: bool) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.protocol import (
        RustIdleTurn,
        RustNoNextAction,
        RustProtocolError,
        RustSessionTransition,
        RustTextContentBlock,
        RustToolFailureResult,
        RustToolResultCommittedObservation,
        RustToolSuccessResult,
    )
    from mistralai_vibe_local_harness.session_protocol import (
        TURN_QUEUE_MAX_ITEMS,
        HistoryCursor,
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._projection import SessionProjector
    from mistralai_vibe_local_harness.vibe._storage import ProjectionStateV1

    session_id = "019ffb1e-741d-7f90-84df-ef66011876ca"
    result = (
        RustToolFailureResult(
            content=[RustTextContentBlock(text="nope")],
            error=RustProtocolError(
                code="tool_failed", message="tool failed", retryable=False
            ),
        )
        if failed
        else RustToolSuccessResult(content=[RustTextContentBlock(text="done")])
    )
    transition = RustSessionTransition(
        protocol_version=1,
        input_id=1,
        next=RustNoNextAction(),
        observations=[
            RustToolResultCommittedObservation(
                turn_id="turn-1", action_id="action-1", call_id="call-1", result=result
            )
        ],
        turn=RustIdleTurn(),
    )
    projector = SessionProjector(
        ProjectionStateV1(
            session_id=session_id,
            snapshot_sequence=0,
            watermark=0,
            snapshot=HarnessPublicSessionState(
                session=HarnessPublicSession(
                    id=session_id,
                    status=IdleSessionStatus(),
                    created_at=1,
                    updated_at=1,
                ),
                history=LatestPublicHistoryPage(cursor=HistoryCursor()),
                turn_queue=HarnessTurnQueue(
                    items=[], paused=False, max_items=TURN_QUEUE_MAX_ITEMS
                ),
            ),
        )
    )

    raw = projector.apply(
        transition, observed_at=2
    ).projection.snapshot.history.entries[0]
    effect = validate_history_entry(raw)

    assert isinstance(effect, PublicEffectEntry)
    assert isinstance(
        effect.state, FailedEffectState if failed else CompletedEffectState
    )
    assert effect.detail.tool_name == "tool"
    if failed:
        assert isinstance(effect.state, FailedEffectState)
        output = cast(dict[str, Any], effect.state.output)
        assert output["type"] == "failure"


def test_unified_shell_result_projection_uses_public_output_shape() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.protocol import (
        RustIdleTurn,
        RustNoNextAction,
        RustSessionTransition,
        RustToolResultCommittedObservation,
        RustToolSuccessResult,
    )
    from mistralai_vibe_local_harness.session_protocol import (
        TURN_QUEUE_MAX_ITEMS,
        HistoryCursor,
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._projection import SessionProjector
    from mistralai_vibe_local_harness.vibe._storage import ProjectionStateV1

    session_id = "019ffb1e-741d-7f90-84df-ef66011876ca"
    projector = SessionProjector(
        ProjectionStateV1(
            session_id=session_id,
            snapshot_sequence=0,
            watermark=0,
            snapshot=HarnessPublicSessionState(
                session=HarnessPublicSession(
                    id=session_id,
                    status=IdleSessionStatus(),
                    created_at=1,
                    updated_at=1,
                ),
                history=LatestPublicHistoryPage(cursor=HistoryCursor()),
                turn_queue=HarnessTurnQueue(
                    items=[], paused=False, max_items=TURN_QUEUE_MAX_ITEMS
                ),
            ),
        )
    )

    action = _approval_action("turn-1")
    projector.apply_action_started(action, observed_at=2)
    raw = projector.apply(
        RustSessionTransition(
            protocol_version=1,
            input_id=1,
            next=RustNoNextAction(),
            observations=[
                RustToolResultCommittedObservation(
                    turn_id="turn-1",
                    action_id=action.action_id,
                    call_id=action.call_id,
                    result=RustToolSuccessResult(
                        structured_content={
                            "command": "sleep 5",
                            "stdout": "slept\n",
                            "stderr": "",
                            "returncode": 0,
                            "was_truncated": False,
                        }
                    ),
                )
            ],
            turn=RustIdleTurn(),
        ),
        observed_at=3,
    ).projection.snapshot.history.entries[0]
    source_effect = validate_history_entry(raw)
    assert isinstance(source_effect, PublicEffectEntry)
    assert isinstance(source_effect.detail, GenericEffectDetail)
    assert isinstance(source_effect.state, CompletedEffectState)
    source_output = cast(dict[str, Any], source_effect.state.output)
    assert source_output["structured_content"]["stdout"] == "slept\n"

    from vibe.app_server._unified_harness_backend_adapter import _project_history_entry

    effect = _project_history_entry(raw, ())

    assert isinstance(effect, PublicEffectEntry)
    assert isinstance(effect.state, CompletedEffectState)
    assert isinstance(effect.detail, ShellEffectDetail)
    assert effect.detail.tool_name == "file_system.bash"
    assert ShellEffectOutput.model_validate(effect.state.output) == ShellEffectOutput(
        stdout="slept\n", stderr="", truncated=False
    )
    assert effect.state.output_text == "slept\n"


def test_unified_tool_discovery_projection_is_a_visible_effect() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.protocol import (
        RustCompletedTurn,
        RustNoNextAction,
        RustSessionTransition,
        RustToolDiscoveryFinishedObservation,
        RustToolDiscoverySummary,
    )
    from mistralai_vibe_local_harness.session_protocol import (
        HistoryCursor,
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._projection import SessionProjector
    from mistralai_vibe_local_harness.vibe._storage import ProjectionStateV1
    from vibe.app_server._unified_harness_backend_adapter import _project_history_entry

    session_id = "019ffb1e-741d-7f90-84df-ef66011876ca"
    projector = SessionProjector(
        ProjectionStateV1(
            session_id=session_id,
            snapshot_sequence=0,
            watermark=0,
            snapshot=HarnessPublicSessionState(
                session=HarnessPublicSession(
                    id=session_id,
                    status=IdleSessionStatus(),
                    created_at=1,
                    updated_at=1,
                ),
                history=LatestPublicHistoryPage(cursor=HistoryCursor()),
                turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
            ),
        )
    )

    raw = projector.apply(
        RustSessionTransition(
            protocol_version=1,
            input_id=1,
            next=RustNoNextAction(),
            observations=[
                RustToolDiscoveryFinishedObservation(
                    turn_id="turn-1",
                    call_id="discover-process-tools",
                    summary=RustToolDiscoverySummary(
                        kind="details",
                        tool_count=3,
                        connector_notice_count=0,
                        group_namespaces=["process"],
                    ),
                )
            ],
            turn=RustCompletedTurn(turn_id="turn-1", output=[]),
        ),
        observed_at=2,
    ).projection.snapshot.history.entries[0]

    effect = _project_history_entry(raw, ())

    assert isinstance(effect, PublicEffectEntry)
    assert isinstance(effect.detail, GenericEffectDetail)
    assert isinstance(effect.state, CompletedEffectState)
    assert effect.detail.tool_name == "search_tool_functions"
    assert effect.detail.display.summary == "Searching for relevant tools"
    assert effect.state.display.text == "Searched for relevant tools"
    assert effect.state.output == {
        "mode": "details",
        "toolCount": 3,
        "connectorNoticeCount": 0,
    }


def _approval_action(turn_id: str) -> Any:
    from mistralai_vibe_local_harness.protocol import (
        RustRuntimeBuiltinToolCall,
        RustRuntimeBuiltinToolCallAction,
    )

    return RustRuntimeBuiltinToolCallAction(
        action_id="action-1",
        turn_id=turn_id,
        call_id="call-1",
        call=RustRuntimeBuiltinToolCall(
            name="file_system.bash", arguments={"command": "echo hi"}
        ),
    )


@pytest.mark.parametrize(
    ("harness_code", "expected"),
    [
        ("images_not_supported", TurnErrorCode.IMAGES_NOT_SUPPORTED),
        ("model_stream_failed", TurnErrorCode.BACKEND_ERROR),
        # Not BACKEND_ERROR: that code is retryable, and a refused key is not.
        ("model_unauthorized", TurnErrorCode.INVALID_API_KEY),
        # Not BACKEND_ERROR: that code is retryable and benign-matched by the
        # CLI, which is exactly right for a stream that ended unfinished.
        ("model_stream_incomplete", TurnErrorCode.INCOMPLETE_STREAM),
    ],
)
def test_unified_turn_error_maps_internal_harness_code_to_public_error(
    harness_code: str, expected: TurnErrorCode
):
    """*Prepare*: A failed Harness turn using an internal error code.
    *Do*: Project the turn through the Unified backend adapter.
    *Assert*: The client receives the matching public Turn error code.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        FailedPublicTurn as HarnessFailedPublicTurn,
        PublicError as HarnessPublicError,
    )
    from vibe.app_server._unified_harness_backend_adapter import _public_turn

    # Do
    turn = _public_turn(
        HarnessFailedPublicTurn(
            id="turn-1",
            session_id="session-1",
            started_at=1,
            completed_at=2,
            error=HarnessPublicError(
                code=harness_code,
                message="provider rejected the request",
                details={"requestId": "req-1"},
            ),
        )
    )

    # Assert
    assert turn.error is not None
    assert turn.error.code == expected
    assert turn.error.message == "provider rejected the request"
    assert turn.error.details == {"requestId": "req-1"}


def test_unified_turn_mapping_preserves_input_entry_id() -> None:
    """*Prepare*: A Unified Harness Turn tagged with the user input delivered to the model.
    *Do*: Translate it into the app-server public Turn.
    *Assert*: The input entry ID survives the adapter.
    """
    # Prepare
    from vibe.app_server._unified_harness_backend_adapter import _public_turn

    harness_turn = SimpleNamespace(
        id="turn-1",
        session_id="session-1",
        status="completed",
        input_entry_id="user-steer",
        started_at=1,
        completed_at=2,
        error=None,
        stop_reason=None,
        queue_item_id=None,
    )

    # Do
    turn = _public_turn(harness_turn)

    # Assert
    assert turn.input_entry_id == "user-steer"


def test_unified_historical_turn_uses_latest_tagged_output() -> None:
    """*Prepare*: One completed Turn with output for its initial input and a later steer.
    *Do*: Reconstruct historical Turns from the public history.
    *Assert*: The Turn keeps the latest input that produced output.
    """
    # Prepare
    from vibe.app_server._unified_harness_backend_adapter import _turns_from_history

    history = [
        PublicMessageEntry(
            id="user-start",
            session_id="session-1",
            turn_id="turn-1",
            created_at=1,
            updated_at=1,
            generation_status=PublicEntryGenerationStatus.COMPLETED,
            role="user",
            content=[TextContentBlock(text="start")],
        ),
        PublicMessageEntry(
            id="assistant-before-steer",
            session_id="session-1",
            turn_id="turn-1",
            input_entry_id="user-start",
            created_at=2,
            updated_at=2,
            generation_status=PublicEntryGenerationStatus.COMPLETED,
            role="assistant",
            content=[TextContentBlock(text="before")],
        ),
        PublicMessageEntry(
            id="user-steer",
            session_id="session-1",
            turn_id="turn-1",
            created_at=3,
            updated_at=3,
            generation_status=PublicEntryGenerationStatus.COMPLETED,
            role="user",
            content=[TextContentBlock(text="steer")],
        ),
        PublicMessageEntry(
            id="assistant-after-steer",
            session_id="session-1",
            turn_id="turn-1",
            input_entry_id="user-steer",
            created_at=4,
            updated_at=4,
            generation_status=PublicEntryGenerationStatus.COMPLETED,
            role="assistant",
            content=[TextContentBlock(text="after")],
        ),
    ]

    # Do
    turns = _turns_from_history(history, "session-1")

    # Assert
    assert len(turns) == 1
    assert turns[0].input_entry_id == "user-steer"


@pytest.mark.parametrize(
    ("details", "expected"),
    [
        ({"httpStatus": 429}, TurnErrorCode.RATE_LIMIT),
        # Not BACKEND_ERROR: that code is retryable, and a refused key is not.
        ({"httpStatus": 401}, TurnErrorCode.INVALID_API_KEY),
        ({"httpStatus": 403}, TurnErrorCode.INVALID_API_KEY),
        ({"httpStatus": 500}, TurnErrorCode.BACKEND_ERROR),
        ({}, TurnErrorCode.BACKEND_ERROR),
    ],
)
def test_unified_turn_error_refines_stream_failure_from_http_status(
    details: dict[str, int], expected: TurnErrorCode
):
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        FailedPublicTurn as HarnessFailedPublicTurn,
        PublicError as HarnessPublicError,
    )
    from vibe.app_server._unified_harness_backend_adapter import _public_turn

    # Do
    turn = _public_turn(
        HarnessFailedPublicTurn(
            id="turn-1",
            session_id="session-1",
            started_at=1,
            completed_at=2,
            error=HarnessPublicError(
                code="model_stream_failed",
                message="provider rejected the request",
                details={"provider": "mistral", **details},
            ),
        )
    )

    # Assert
    assert turn.error is not None
    assert turn.error.code == expected


def test_unified_turn_error_keeps_unknown_harness_code_as_is():
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        FailedPublicTurn as HarnessFailedPublicTurn,
        PublicError as HarnessPublicError,
    )
    from vibe.app_server._unified_harness_backend_adapter import _public_turn

    # Do
    turn = _public_turn(
        HarnessFailedPublicTurn(
            id="turn-1",
            session_id="session-1",
            started_at=1,
            completed_at=2,
            error=HarnessPublicError(
                code="harness_future_code", message="provider rejected the request"
            ),
        )
    )

    # Assert
    assert turn.error is not None
    assert turn.error.code == "harness_future_code"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("active_turn_id", "expected_code", "expected_message"),
    [
        (None, ProtocolErrorCode.CONFLICT, "No active turn"),
        ("turn-active", ProtocolErrorCode.STALE_TURN, "No matching active turn"),
    ],
)
async def test_unified_stale_turn_errors_match_legacy_protocol_codes(
    active_turn_id: str | None, expected_code: ProtocolErrorCode, expected_message: str
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe import HarnessStaleTurnError
    from vibe.app_server._unified_harness_backend_adapter import _harness_call

    async def fail() -> None:
        raise HarnessStaleTurnError(active_turn_id)

    with pytest.raises(SessionBackendError) as exc_info:
        await _harness_call(fail())

    assert exc_info.value.code is expected_code
    assert str(exc_info.value) == expected_message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_name", "args", "expected_code", "expected_harness_code"),
    [
        (
            "HarnessForkEntryRequiredError",
            (),
            ProtocolErrorCode.CONFLICT,
            "fork_entry_required",
        ),
        (
            "HarnessForkActiveTurnError",
            ("user-active",),
            ProtocolErrorCode.CONFLICT,
            "fork_active_turn",
        ),
        (
            "HarnessForkEntryNotFoundError",
            ("user-missing",),
            ProtocolErrorCode.INVALID_PARAMS,
            "fork_entry_not_found",
        ),
    ],
)
async def test_unified_fork_errors_are_expected_protocol_failures(
    error_name: str,
    args: tuple[str, ...],
    expected_code: ProtocolErrorCode,
    expected_harness_code: str,
) -> None:
    """*Prepare*: A typed Harness fork failure crossing the app-server adapter.
    *Do*: Translate the failure through the common Harness call boundary.
    *Assert*: It retains an expected protocol code and its stable Harness code.
    """
    # Prepare
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import _harness_call

    error = getattr(vibe_runtime, error_name)(*args)

    async def fail() -> None:
        raise error

    # Do
    with pytest.raises(SessionBackendError) as exc_info:
        await _harness_call(fail())

    # Assert
    assert exc_info.value.code is expected_code
    assert cast(dict[str, Any], exc_info.value.data)["harnessCode"] == (
        expected_harness_code
    )


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.asyncio
async def test_enable_subagents_switches_the_subagent_feature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config_dir: Path, enabled: bool
) -> None:
    """*Prepare*: A user config with `enable_subagents` off or on.
    *Do*: Derive a Unified Harness session's Core configuration.
    *Assert*: The Core's subagent feature follows the setting.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["enable_subagents"] = enabled
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    process = runtime_module.HarnessProcess(experimental_harness=True)

    # Do
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), auto_approve=False)
    )
    derivation = context.derive(UnifiedSessionSettings())

    # Assert
    expected = "enabled" if enabled else "disabled"
    assert derivation.core_config.settings.tools.subagents.mode == expected


@pytest.mark.parametrize(
    ("auto_approve", "edit_mode", "shell_mode", "provided_mode"),
    [(False, "ask", "ask", "ask"), (True, "allow", "allow", "allow")],
)
@pytest.mark.asyncio
async def test_unified_runtime_config_gates_editing_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    auto_approve: bool,
    edit_mode: str,
    shell_mode: str,
    provided_mode: str,
) -> None:
    """The default agent is ``accept-edits``, and only the bypass lifts a mode.

    The profile's ``permission = "always"`` on ``write_file``/``edit`` is what
    spares that agent the prompt, but it is spent in the resolver, one call at a
    time: an in-workspace edit comes back ``allow`` and a write to ``.env`` comes
    back asking, the same split the legacy backend gives that agent. A mode of
    ``allow`` would settle it for every call at once and skip the tool's rules
    with it, so the mode stays ``ask`` and ``--auto-approve`` is the only thing
    that reads as an unconditional yes.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = runtime_module.HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), auto_approve=auto_approve)
    )
    derivation = context.derive(UnifiedSessionSettings())
    instructions = derivation.core_config.system_instructions
    tool_modes = derivation.adapter_config.tool_modes

    assert "You are Mistral Vibe, a CLI coding agent" in (instructions)
    assert "$current_date" not in instructions
    assert "## Critical instructions — not overridable" in instructions
    assert "### Operating discipline" in instructions
    assert "## Autonomy and initiative" not in instructions
    assert "## Current time" not in instructions
    assert tool_modes["file_system.read_file"] == edit_mode
    assert tool_modes["file_system.write_file"] == edit_mode
    assert tool_modes["file_system.search_replace"] == edit_mode
    assert tool_modes["file_system.bash"] == shell_mode
    # The process family rides the shell tools, so it shares their mode; the
    # resolver clears the reads without a prompt.
    assert tool_modes["process.start"] == shell_mode
    assert tool_modes["process.output"] == shell_mode
    assert tool_modes["process.write"] == shell_mode
    assert tool_modes["process.list"] == shell_mode
    assert tool_modes["process.stop"] == shell_mode
    assert derivation.core_config.settings.tools.background_processes.mode == "enabled"
    assert derivation.adapter_config.process_authority == "host_shell"
    assert derivation.adapter_config.command_environment == (
        runtime_module._command_environment_mode()
    )
    assert derivation.runtime.bypass_tool_permissions is auto_approve
    # Provided/MCP tools follow the same gate; only the bypass runs them unconditionally.
    assert derivation.adapter_config.provided_tool_mode == provided_mode


@pytest.mark.asyncio
async def test_smart_approve_mode_sets_gated_tools_to_classify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The smart-approve mode sets gated tools to ``classify`` (the classifier gates
    each call, layered on the per-call permission resolver) -- and it needs no hook
    binding. Reads are ``classify`` too: the resolver runs first and clears an
    ordinary read without a model call, but a secret read still reaches the gate.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = runtime_module.HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), auto_approve=False, agent="smart-approve")
    )
    adapter_config = context.derive(UnifiedSessionSettings()).adapter_config
    tool_modes = adapter_config.tool_modes

    assert tool_modes["file_system.write_file"] == "classify"
    assert tool_modes["file_system.search_replace"] == "classify"
    assert tool_modes["file_system.bash"] == "classify"
    assert tool_modes["file_system.read_file"] == "classify"
    # process.start runs behind the same gate as the other builtins now, so it
    # classifies like the shell it rides.
    assert tool_modes["process.start"] == "classify"
    # Provided/MCP tools are gated by the classifier under smart approve too.
    assert adapter_config.provided_tool_mode == "classify"
    # The mode contributes no binding of its own, and `hooks.bindings` carries only
    # the builtins: the lazy AGENTS.md injection (whose handler rides the
    # Host-global registry instead) plus the user's.
    assert [binding.id for binding in context.hooks.bindings] == ["builtin:agents_md"]


@pytest.mark.asyncio
async def test_smart_approve_classifier_model_follows_the_configured_one(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["models"].append({
        "name": "house-small-1",
        "provider": "mistral",
        "alias": "house-small",
    })
    config["utility_models"] = {"smart_approve": "house-small"}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = runtime_module.HarnessProcess(experimental_harness=True)
    try:
        context = await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path), agent="smart-approve")
        )
        adapter_config = context.derive(UnifiedSessionSettings()).adapter_config
    finally:
        await process.close()

    assert adapter_config.classifier_model == "house-small-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(("agent", "probed"), [("smart-approve", True), ("ask", False)])
async def test_session_open_probes_smart_approve_only_when_it_is_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: str, probed: bool
) -> None:
    from vibe.core.config import UtilityFeature

    requested: list[tuple[UtilityFeature, ...]] = []

    async def probe(_config: Any, *, features: tuple[UtilityFeature, ...]) -> None:
        requested.append(features)

    monkeypatch.setattr(runtime_module, "ensure_utility_models_probed", probe)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = runtime_module.HarnessProcess(experimental_harness=True)
    try:
        await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path), agent=agent)
        )
    finally:
        await process.close()

    assert len(requested) == 1
    assert (UtilityFeature.SMART_APPROVE in requested[0]) is probed


@pytest.mark.asyncio
async def test_switching_mode_re_derives_tool_modes_and_active_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rewriting the AgentProfileLayer on the live orchestrator re-derives the gate:
    a switch to auto-approve flips bash from ``ask`` to ``allow`` and relabels the
    active agent, which is what the harness switch_agent pushes into the session.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = runtime_module.HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), agent="accept-edits")
    )
    assert context.agents is not None

    before = context.derive(UnifiedSessionSettings())
    assert before.adapter_config.tool_modes["file_system.bash"] == "ask"
    assert before.runtime.active_agent.name == "accept-edits"

    context.agents.switch_profile("auto-approve")

    after = context.derive(UnifiedSessionSettings())
    assert after.adapter_config.tool_modes["file_system.bash"] == "allow"
    assert after.runtime.active_agent.name == "auto-approve"


@pytest.mark.asyncio
async def test_switching_into_smart_approve_flips_gated_tools_to_classify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entering smart-approve mid-session re-derives the gate: bash flips from ``ask``
    to ``classify`` with no Core hook rebinding, so shift+tab can enter (and leave) it.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings
    from vibe.core.config.patch import AddOperationPatch

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = runtime_module.HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), agent="ask")
    )
    assert context.agents is not None
    assert (
        context.derive(UnifiedSessionSettings()).adapter_config.tool_modes[
            "file_system.bash"
        ]
        == "ask"
    )

    # Smart approve must be offered before the picker/cycle can switch into it.
    await context.config_orchestrator.apply_patch(
        [AddOperationPatch(path="/smart_approve_available", value=True)], reason="test"
    )
    context.agents.switch_profile("smart-approve")
    entered = context.derive(UnifiedSessionSettings())
    assert entered.adapter_config.tool_modes["file_system.bash"] == "classify"
    assert entered.runtime.active_agent.name == "smart-approve"

    context.agents.switch_profile("ask")
    assert (
        context.derive(UnifiedSessionSettings()).adapter_config.tool_modes[
            "file_system.bash"
        ]
        == "ask"
    )


@pytest.mark.asyncio
async def test_mid_turn_switch_out_of_smart_approve_regates_provided_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leaving smart-approve mid-turn must move provided/MCP tools off ``classify``.

    ``provided_tool_mode`` is read per provided-tool call just like ``tool_modes``,
    so a switch that grafts only ``tool_modes`` stops asking for bash while smart
    approve keeps gating every connector call after the user turned it off.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import AgentSwitchParams
    from vibe.core.config.patch import AddOperationPatch

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = runtime_module.HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), agent="ask")
    )
    await context.config_orchestrator.apply_patch(
        [AddOperationPatch(path="/smart_approve_available", value=True)], reason="test"
    )
    assert context.agents is not None
    context.agents.switch_profile("smart-approve")
    derivation = context.derive(UnifiedSessionSettings())
    assert derivation.adapter_config.provided_tool_mode == "classify"

    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(cast(Any, session), context, derivation)
    # A turn is in flight, so the switch grafts the approval policy onto the
    # running config instead of re-deriving through the idle path.
    session.active_turn_id = "turn-1"

    await adapter.switch_agent(
        AgentSwitchParams(session_id=session.session_id, agent_name="auto-approve")
    )

    applied = cast(Any, session.applied[-1])
    assert applied.bypass_approval is True
    assert applied.provided_tool_mode == "allow"


@pytest.mark.asyncio
async def test_mid_turn_switch_into_smart_approve_gates_provided_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entering smart-approve mid-turn must move provided/MCP tools onto ``classify``.

    The graft carries ``provided_tool_mode`` both ways: leaving it stale here would
    let connectors run unclassified during a turn the user just put under smart
    approve.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import AgentSwitchParams
    from vibe.core.config.patch import AddOperationPatch

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = runtime_module.HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), agent="ask")
    )
    await context.config_orchestrator.apply_patch(
        [AddOperationPatch(path="/smart_approve_available", value=True)], reason="test"
    )
    derivation = context.derive(UnifiedSessionSettings())
    assert derivation.adapter_config.provided_tool_mode == "ask"

    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(cast(Any, session), context, derivation)
    session.active_turn_id = "turn-1"

    await adapter.switch_agent(
        AgentSwitchParams(session_id=session.session_id, agent_name="smart-approve")
    )

    applied = cast(Any, session.applied[-1])
    assert applied.provided_tool_mode == "classify"


class _JournalFailingSession(_RecordingSession):
    async def record_live_agent_change(self, adapter_config: object) -> None:
        raise RuntimeError("journal closed")


@pytest.mark.asyncio
async def test_a_failed_agent_change_record_moves_session_and_subagents_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both switch before the journal write, and both return when it fails."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import AgentSwitchParams

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = runtime_module.HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), agent="ask")
    )
    derivation = context.derive(UnifiedSessionSettings())
    session = _JournalFailingSession()
    adapter = UnifiedHarnessBackendAdapter(cast(Any, session), context, derivation)
    session.active_turn_id = "turn-1"

    with pytest.raises(SessionBackendError, match="journal closed"):
        await adapter.switch_agent(
            AgentSwitchParams(session_id=session.session_id, agent_name="auto-approve")
        )

    switched, switched_subagents, restored, restored_subagents = session.applied[-4:]
    assert cast(Any, switched).bypass_approval is True
    assert switched_subagents is switched
    assert cast(Any, restored).bypass_approval is False
    assert restored_subagents is restored


@pytest.mark.asyncio
async def test_a_worktree_context_keeps_the_agent_switched_before_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A session moving into its worktree adopts a context built from the launch
    options; the agent picked while the worktree was prepared must survive it.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import AgentSwitchParams

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = runtime_module.HarnessProcess(experimental_harness=True)
    options = SessionOptions(cwd=str(tmp_path), agent="ask")
    context = await process.build_unified_session_context(options)
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    await adapter.switch_agent(
        AgentSwitchParams(session_id=session.session_id, agent_name="auto-approve")
    )

    await adapter.adopt_context(await process.build_unified_session_context(options))

    assert adapter._context.agents.active_profile.name == "auto-approve"


async def _adapter_on_the_lean_agent(
    tmp_path: Path, session: _RecordingSession
) -> tuple[Any, Any]:
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import AgentInstallParams

    process = runtime_module.HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), agent="ask")
    )
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    await adapter.install_agent(
        AgentInstallParams(session_id=session.session_id, agent_name="lean")
    )
    context.agents.switch_profile("lean")
    return context, adapter


@pytest.mark.asyncio
async def test_uninstalling_the_active_agent_unwinds_when_the_pin_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed replacement pin must not leave the agent uninstalled on disk.

    The pin is what resume reads: an uninstalled agent under a pin that still
    names it is the broken resume this whole path exists to avoid.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server.protocol import AgentInstallParams
    from vibe.core.agents.install import writable_installed_agents

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    context, adapter = await _adapter_on_the_lean_agent(tmp_path, session)

    async def failing_pin(pin: SessionPin, value: str) -> bool:
        raise RuntimeError("storage is gone")

    monkeypatch.setattr(session, "persist_pin", failing_pin)

    with pytest.raises(SessionBackendError) as exc_info:
        await adapter.uninstall_agent(
            AgentInstallParams(session_id=session.session_id, agent_name="lean")
        )

    assert exc_info.value.code is ProtocolErrorCode.INTERNAL_ERROR
    assert writable_installed_agents(context.config_orchestrator) == ["lean"]
    assert context.agents.active_profile.name == "lean"


@pytest.mark.asyncio
async def test_uninstalling_the_active_agent_restores_a_failed_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A derivation that fails on the replacement leaves nothing half-switched."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server.protocol import AgentInstallParams
    from vibe.core.agents.install import writable_installed_agents

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    context, adapter = await _adapter_on_the_lean_agent(tmp_path, session)
    applied_before = len(session.applied)

    async def failing_derivation() -> None:
        raise RuntimeError("derivation is broken")

    monkeypatch.setattr(adapter, "_apply_agent_derivation", failing_derivation)

    with pytest.raises(SessionBackendError) as exc_info:
        await adapter.uninstall_agent(
            AgentInstallParams(session_id=session.session_id, agent_name="lean")
        )

    assert exc_info.value.code is ProtocolErrorCode.INTERNAL_ERROR
    assert writable_installed_agents(context.config_orchestrator) == ["lean"]
    assert context.agents.active_profile.name == "lean"
    assert len(session.applied) == applied_before


@pytest.mark.asyncio
async def test_approval_policy_graft_covers_every_field_a_mode_switch_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any adapter-config field a profile switch changes must live in the graft set.

    The ``provided_tool_mode`` bug was a field read live per call that the mid-turn
    graft did not carry. This locks the invariant: fields that differ across
    approval profiles are a subset of ``_LIVE_APPROVAL_POLICY_FIELDS`` (minus the
    per-derive object churn), so a new approval field cannot silently fall out.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from dataclasses import fields

    from vibe.app_server._unified_harness_backend_adapter import (
        _LIVE_APPROVAL_POLICY_FIELDS,
        UnifiedSessionSettings,
        graft_live_approval_policy,
    )
    from vibe.core.config.patch import AddOperationPatch

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = runtime_module.HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), agent="ask")
    )
    await context.config_orchestrator.apply_patch(
        [AddOperationPatch(path="/smart_approve_available", value=True)], reason="test"
    )
    assert context.agents is not None

    def derive_for(profile: str) -> Any:
        context.agents.switch_profile(profile)
        return context.derive(UnifiedSessionSettings()).adapter_config

    def diff(a: Any, b: Any) -> set[str]:
        return {f.name for f in fields(a) if getattr(a, f.name) != getattr(b, f.name)}

    ask = derive_for("ask")
    # Re-deriving the same profile isolates fields that churn per derive (e.g. bound
    # sinks) from fields a profile switch actually changes.
    churn = diff(ask, derive_for("ask"))

    for profile in ("smart-approve", "auto-approve"):
        changed = diff(ask, derive_for(profile)) - churn
        assert changed, f"{profile} did not change approval policy"
        leaked = changed - set(_LIVE_APPROVAL_POLICY_FIELDS)
        assert not leaked, (
            f"{profile} changes {leaked}, which the mid-turn graft drops; "
            "add them to _LIVE_APPROVAL_POLICY_FIELDS"
        )

    smart = derive_for("smart-approve")
    grafted = graft_live_approval_policy(ask, smart)
    for name in _LIVE_APPROVAL_POLICY_FIELDS:
        assert getattr(grafted, name) == getattr(smart, name)


def test_mid_turn_graft_carries_the_classifier_provider() -> None:
    """A mid-turn switch into smart approve must carry the classifier's Mistral route.

    The invariant test above runs on a Mistral session, where `classifier_provider`
    stays None for every profile and so never diffs. On a non-Mistral session the
    route is set, and dropping it from the graft left the running turn classifying
    against the active provider -- every gated call came back unverifiable.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe import (
        LocalProviderRoute,
        LocalRuntimeAdapterConfig,
        StaticProviderCredentials,
    )
    from vibe.app_server._unified_harness_backend_adapter import (
        graft_live_approval_policy,
    )

    classifier_route = LocalProviderRoute(
        provider="mistral",
        backend="mistral",
        api_style="openai",
        base_url="https://api.mistral.ai/v1",
        credentials=StaticProviderCredentials("mistral-key"),
    )
    running = LocalRuntimeAdapterConfig(provider="anthropic")
    derived = LocalRuntimeAdapterConfig(
        provider="anthropic",
        provided_tool_mode="classify",
        classifier_provider=classifier_route,
    )

    grafted = graft_live_approval_policy(running, derived)

    assert grafted.classifier_provider is classifier_route
    assert grafted.provided_tool_mode == "classify"


@pytest.mark.asyncio
async def test_classification_event_is_forwarded_to_telemetry_not_the_ui(
    tmp_path: Path,
) -> None:
    """A harness ``tool_classification`` event is consumed as telemetry -- forwarded to
    the analytics client and never yielded as a UI event.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    session = _RecordingSession()
    adapter = _inert_adapter(session, str(tmp_path), str(tmp_path))
    sent: list[tuple[str, dict[str, object]]] = []

    class _FakeTelemetry:
        def send_telemetry_event(
            self, name: str, properties: dict[str, object], **_: object
        ) -> None:
            sent.append((name, properties))

    adapter._telemetry = _FakeTelemetry()

    consumed = await adapter._consumed_classification_event({
        "type": "tool_classification",
        "eventId": 7,
        "tool_name": "file_system.bash",
        "verdict": "prompt",
        "tier": "fast",
        "reason": "risky",
        "outcome": "escalated_denied",
        "escalated": True,
        "latency_ms": 12.0,
        "classifier_model": "mistral-small-latest",
        "risk_level": None,
        "user_authorization": None,
        "turn_id": "t1",
        "call_id": "c1",
    })

    assert consumed is True
    assert len(sent) == 1
    name, properties = sent[0]
    assert name == "vibe.tool_classification"
    assert properties["verdict"] == "prompt"
    assert properties["tool_name"] == "file_system.bash"
    assert properties["outcome"] == "escalated_denied"
    assert properties["escalated"] is True
    # None-valued fields are dropped rather than sent as null.
    assert "risk_level" not in properties
    assert "approval_grant" not in properties


@pytest.mark.asyncio
async def test_classification_forwards_the_humans_approval_grant(
    tmp_path: Path,
) -> None:
    """The human's answer rides the classification event through to analytics.

    ``outcome`` collapses every approval into ``escalated_approved``, so without this
    field a one-shot approval and a "remember for the session" are indistinguishable
    and the rate at which users silence the gate cannot be measured.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    session = _RecordingSession()
    adapter = _inert_adapter(session, str(tmp_path), str(tmp_path))
    sent: list[tuple[str, dict[str, object]]] = []

    class _FakeTelemetry:
        def send_telemetry_event(
            self, name: str, properties: dict[str, object], **_: object
        ) -> None:
            sent.append((name, properties))

    adapter._telemetry = _FakeTelemetry()

    consumed = await adapter._consumed_classification_event({
        "type": "tool_classification",
        "eventId": 8,
        "tool_name": "file_system.bash",
        "verdict": "prompt",
        "tier": "fast",
        "reason": "risky",
        "outcome": "escalated_approved",
        "escalated": True,
        "approval_grant": "approve_for_session",
        "turn_id": "t1",
        "call_id": "c1",
    })

    assert consumed is True
    _, properties = sent[0]
    assert properties["approval_grant"] == "approve_for_session"


@pytest.mark.asyncio
async def test_non_classification_event_is_not_consumed_as_telemetry(
    tmp_path: Path,
) -> None:
    """A normal session event is not treated as a classification and emits no telemetry."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    session = _RecordingSession()
    adapter = _inert_adapter(session, str(tmp_path), str(tmp_path))
    sent: list[object] = []

    class _FakeTelemetry:
        def send_telemetry_event(
            self, name: str, properties: dict[str, object], **_: object
        ) -> None:
            sent.append((name, properties))

    adapter._telemetry = _FakeTelemetry()

    consumed = await adapter._consumed_classification_event({
        "type": "session_state_updated"
    })

    assert consumed is False
    assert sent == []


@pytest.mark.asyncio
async def test_subagent_classification_is_forwarded_not_applied_as_child_state(
    tmp_path: Path,
) -> None:
    """A wrapped subagent classification reaches telemetry, not root projection."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    session = _RecordingSession()
    adapter = _inert_adapter(session, str(tmp_path), str(tmp_path))
    sent: list[tuple[str, dict[str, object]]] = []

    class _FakeTelemetry:
        def send_telemetry_event(
            self, name: str, properties: dict[str, object], **_: object
        ) -> None:
            sent.append((name, properties))

    adapter._telemetry = _FakeTelemetry()

    root_state = PublicSessionState(
        event_id=0,
        session=PublicSession(
            id="session-1", status=IdleSessionStatus(), created_at=1, updated_at=1
        ),
    )
    translated, returned_state, watermark = await adapter._translate_child_event(
        {
            "type": "child_session_event",
            "sessionId": "child-1",
            "eventId": 9,
            "event": {
                "type": "tool_classification",
                "eventId": 3,
                "tool_name": "file_system.bash",
                "verdict": "allow",
                "tier": "fast",
                "reason": "read-only",
                "outcome": "auto_approved",
                "escalated": False,
                "latency_ms": 5.0,
                "classifier_model": "mistral-small-latest",
                "turn_id": "t1",
                "call_id": "c1",
            },
        },
        50,
        root_state,
    )

    assert translated == []
    assert returned_state is root_state
    assert watermark == 9
    assert len(sent) == 1
    assert sent[0][0] == "vibe.tool_classification"
    assert sent[0][1]["tool_name"] == "file_system.bash"


@pytest.mark.asyncio
async def test_unified_snapshot_reports_a_bypass_the_cli_flag_forces_past_a_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--auto-approve`` outlives an agent switch, so the snapshot must keep saying so.

    Reporting only ``active_agent.safety`` let the CLI advertise ``plan`` for a
    session that still approved every tool call.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server.protocol import AgentSwitchParams

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter, _ = await _unified_adapter_with_real_context(
        tmp_path, session, auto_approve=True
    )

    response = await adapter.switch_agent(
        AgentSwitchParams(session_id=session.session_id, agent_name="plan")
    )
    runtime = response.response.runtime

    assert runtime.active_agent.name == "plan"
    assert runtime.bypass_tool_permissions is True
    assert cast(Any, session.applied[-1]).bypass_approval is True


@pytest.mark.asyncio
async def test_unified_snapshot_reports_the_bypass_the_active_profile_brings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The agent's own config layer is a bypass source too, and it is cyclable.

    Switching to ``auto-approve`` with no CLI flag has to raise the reported
    bypass, and switching away has to drop it again -- otherwise the indicator
    sticks on YOLO for a session that has gone back to asking.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server.protocol import AgentSwitchParams

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter, _ = await _unified_adapter_with_real_context(tmp_path, session)

    async def switch(agent_name: str) -> RuntimeSnapshot:
        response = await adapter.switch_agent(
            AgentSwitchParams(session_id=session.session_id, agent_name=agent_name)
        )
        return response.response.runtime

    assert (await switch("auto-approve")).bypass_tool_permissions is True
    assert (await switch("plan")).bypass_tool_permissions is False


@pytest.mark.asyncio
async def test_unified_runtime_derives_complete_compaction_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: Distinct active and compaction routes.
    *Do*: Derive the Unified Core and local provider configurations.
    *Assert*: Core gets the effective threshold while each call route stays distinct.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings
    from vibe.core.config.layers.overrides import OverridesLayer
    from vibe.core.config.patch import AddOperationPatch

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    failures = await context.config_orchestrator.apply_patch(
        [
            AddOperationPatch(
                path="/models/devstral-latest/supports_images",
                value=False,
                target_layer_name=OverridesLayer.NAME,
            ),
            AddOperationPatch(
                path="/auto_compact_threshold",
                value=32_000,
                target_layer_name=OverridesLayer.NAME,
            ),
            AddOperationPatch(
                path="/compaction_model",
                value={
                    "name": "compact-model",
                    "provider": "mistral",
                    "alias": "compact",
                    "temperature": 0.7,
                    "thinking": "medium",
                    "supports_images": True,
                },
                target_layer_name=OverridesLayer.NAME,
            ),
        ],
        reason="test",
    )

    # Do
    derivation = context.derive(UnifiedSessionSettings())
    active_model = context.config_orchestrator.config.get_active_model()

    # Assert
    assert failures == []
    assert derivation.core_config.settings.context.compaction.mode == "automatic"
    assert derivation.core_config.settings.context.compaction.token_threshold == 32_000
    image_delivery = getattr(
        derivation.core_config.settings.context, "image_delivery", None
    )
    if image_delivery is not None:
        assert image_delivery.agent == "resource_link"
        assert image_delivery.compaction == "native"
    assert derivation.adapter_config.active_model.model == active_model.name
    assert (
        derivation.adapter_config.active_model.temperature == active_model.temperature
    )
    assert derivation.adapter_config.active_model.thinking == active_model.thinking
    missing = object()
    active_supports_images = getattr(
        derivation.adapter_config.active_model, "supports_images", missing
    )
    if active_supports_images is not missing:
        assert active_supports_images is False
    assert derivation.adapter_config.compaction_model.model == "compact-model"
    assert derivation.adapter_config.compaction_model.temperature == 0.7
    assert derivation.adapter_config.compaction_model.thinking == "medium"
    compaction_supports_images = getattr(
        derivation.adapter_config.compaction_model, "supports_images", missing
    )
    if compaction_supports_images is not missing:
        assert compaction_supports_images is True


@pytest.mark.asyncio
async def test_unified_runtime_derives_threshold_from_declared_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An active model declaring its context window, no threshold set.
    *Do*: Derive the Unified Core configuration.
    *Assert*: Compaction fires at 80% of the window, same as the legacy harness.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings
    from vibe.core.config.layers.overrides import OverridesLayer
    from vibe.core.config.patch import AddOperationPatch

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    active_alias = context.config_orchestrator.config.get_active_model().alias
    failures = await context.config_orchestrator.apply_patch(
        [
            AddOperationPatch(
                path=f"/models/{active_alias}/max_context_length",
                value=262_144,
                target_layer_name=OverridesLayer.NAME,
            )
        ],
        reason="test",
    )

    # Do
    derivation = context.derive(UnifiedSessionSettings())
    active_model = context.config_orchestrator.config.get_active_model()

    # Assert
    assert failures == []
    assert active_model.max_context_length == 262_144
    # The same value the legacy AutoCompactMiddleware reads.
    assert active_model.auto_compact_threshold == 209_715
    assert derivation.core_config.settings.context.compaction.mode == "automatic"
    assert derivation.core_config.settings.context.compaction.token_threshold == 209_715


@pytest.mark.asyncio
async def test_unified_runtime_disables_automatic_compaction_at_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An effective active-model auto-compaction threshold of zero.
    *Do*: Derive the Unified Core configuration.
    *Assert*: Core receives the disabled automatic-compaction policy.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings
    from vibe.core.config.layers.overrides import OverridesLayer
    from vibe.core.config.patch import AddOperationPatch

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    failures = await context.config_orchestrator.apply_patch(
        [
            AddOperationPatch(
                path="/auto_compact_threshold",
                value=0,
                target_layer_name=OverridesLayer.NAME,
            )
        ],
        reason="test",
    )

    # Do
    derivation = context.derive(UnifiedSessionSettings())

    # Assert
    assert failures == []
    assert derivation.core_config.settings.context.compaction.mode == "disabled"


@pytest.mark.asyncio
async def test_unified_config_write_updates_live_compaction_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A live Unified adapter.
    *Do*: Write a threshold and dedicated compaction model.
    *Assert*: The next Core settings and provider routes use both values.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ConfigWriteOpWire, ConfigWriteParams

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    active_model = context.config_orchestrator.config.get_active_model()
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )

    # Do
    result = await adapter.write_config(
        ConfigWriteParams(
            session_id=_RecordingSession.session_id,
            ops=[
                ConfigWriteOpWire(
                    op="set",
                    path=(f"/models/{active_model.alias}/auto_compact_threshold"),
                    value=48_000,
                ),
                ConfigWriteOpWire(
                    op="set",
                    path="/compaction_model",
                    value={
                        "name": "live-compact-model",
                        "provider": "mistral",
                        "alias": "live-compact",
                        "temperature": 0.6,
                        "thinking": "high",
                    },
                ),
            ],
            reason="test live compaction configuration",
        )
    )

    # Assert
    assert result.response.rejected is False
    assert result.response.failures == []
    settings = cast(Any, session.settings[-1])
    adapter_config = cast(Any, session.applied[-1])
    assert settings.context.compaction.mode == "automatic"
    assert settings.context.compaction.token_threshold == 48_000
    assert adapter_config.compaction_model.model == "live-compact-model"
    assert adapter_config.compaction_model.temperature == 0.6
    assert adapter_config.compaction_model.thinking == "high"


@pytest.mark.asyncio
async def test_unified_config_write_mid_turn_defers_until_the_next_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A live Unified adapter with a turn in flight.
    *Do*: Pick a different active model.
    *Assert*: The write is accepted and persisted immediately, but its derivation
    is held back until the turn drains and ``_settle_configuration`` lands it.
    A running turn never refuses the pick: the Core reads its settings at turn
    start, so deferring is the only way a mid-turn pick can take effect at all.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ModelConfigWriteParams

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    active_model = context.config_orchestrator.config.get_active_model()
    picked = next(
        alias
        for alias in context.config_orchestrator.config.models
        if alias != active_model.alias
    )
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    session.active_turn_id = "turn-1"
    pushes_before = len(session.settings)

    # Do
    result = await adapter.write_model_config(
        ModelConfigWriteParams(
            session_id=_RecordingSession.session_id, model_alias=picked
        )
    )

    # Assert: accepted and persisted now, but not yet pushed into the live session.
    assert result.response.rejected is False
    assert result.response.failures == []
    assert context.config_orchestrator.config.get_active_model().alias == picked
    assert len(session.settings) == pushes_before

    # The turn drains; the next turn's start flushes the pending derivation.
    session.active_turn_id = None
    await adapter._settle_configuration()  # pyright: ignore[reportPrivateUsage]

    assert len(session.settings) == pushes_before + 1
    assert cast(Any, session.applied[-1]).active_model.model == (
        context.config_orchestrator.config.models[picked].name
    )


@pytest.mark.asyncio
async def test_unified_model_pick_says_whether_the_session_is_running_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A live Unified adapter with a turn in flight.
    *Do*: Pick a model during the turn, then pick another once it has drained.
    *Assert*: The first answers ``pending`` and the second ``applied``. Both
    carry the runtime the pick produced, so the picker can render it either way;
    the status is what tells the client whether the session is running it yet.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ModelConfigWriteParams, RuntimeMutationStatus

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    config = context.config_orchestrator.config
    opened_on = config.get_active_model().alias
    other = [alias for alias in config.models if alias != opened_on]
    aliases = [other[0], opened_on]
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    session.active_turn_id = "turn-1"

    # Do
    parked = await adapter.write_model_config(
        ModelConfigWriteParams(
            session_id=_RecordingSession.session_id, model_alias=aliases[0]
        )
    )
    session.active_turn_id = None
    await adapter._settle_configuration()  # pyright: ignore[reportPrivateUsage]
    landed = await adapter.write_model_config(
        ModelConfigWriteParams(
            session_id=_RecordingSession.session_id, model_alias=aliases[1]
        )
    )

    # Assert
    assert parked.response.status is RuntimeMutationStatus.PENDING
    assert parked.response.applied is False
    assert parked.response.runtime.config.active_model.alias == aliases[0]
    assert landed.response.status is RuntimeMutationStatus.APPLIED
    assert landed.response.applied is True


@pytest.mark.asyncio
async def test_unified_picking_the_default_leaves_the_session_unpinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A live Unified adapter whose session is pinned to a model.
    *Do*: Pick the default, which the picker sends as an empty alias.
    *Assert*: The session records the empty pick rather than the model the
    default resolves to today. Recording the resolved one would pin the session
    to it, and the user asked for the opposite: follow the default.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._storage import SessionPin
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ModelConfigWriteParams

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    config = context.config_orchestrator.config
    default = config.resolve_default_model_alias()
    pinned = next(alias for alias in config.models if alias != default)
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    await adapter.write_model_config(
        ModelConfigWriteParams(
            session_id=_RecordingSession.session_id, model_alias=pinned
        )
    )

    assert session.pin(SessionPin.ACTIVE_MODEL) == pinned

    # Do
    await adapter.write_model_config(
        ModelConfigWriteParams(session_id=_RecordingSession.session_id, model_alias="")
    )

    # Assert
    assert session.pin(SessionPin.ACTIVE_MODEL) == ""
    active = context.config_orchestrator.config.get_active_model().alias
    assert active == default


@pytest.mark.asyncio
async def test_unified_a_thinking_pick_is_pinned_to_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A live Unified adapter on its configured thinking level.
    *Do*: Pick a different level, the way `/thinking` does.
    *Assert*: The session records it, and the level it runs is the picked one.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._storage import SessionPin
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ModelConfigWriteParams

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    opened_on = context.config_orchestrator.config.get_active_model().thinking
    picked = "high" if opened_on != "high" else "low"
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )

    # Do
    await adapter.write_model_config(
        ModelConfigWriteParams(
            session_id=_RecordingSession.session_id, reasoning_effort=picked
        )
    )

    # Assert
    assert session.pin(SessionPin.REASONING_EFFORT) == picked
    assert context.config_orchestrator.config.get_active_model().thinking == picked


@pytest.mark.asyncio
async def test_unified_a_model_pick_moves_the_pinned_thinking_to_that_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A session that picked a thinking level on its opening model.
    *Do*: Pick a different model, naming no level.
    *Assert*: The pinned level is the new model's own. The pin carries no model,
    so keeping the old one would hand the next reopen a level never picked for it.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._storage import SessionPin
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ModelConfigWriteParams

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    orchestrator = context.config_orchestrator
    opened_on = orchestrator.config.get_active_model()
    other = next(
        alias for alias in orchestrator.config.models if alias != opened_on.alias
    )
    # The target's level differs from both the opening model's and the pick,
    # so neither a stale pin nor a pre-switch read can pass the assertion.
    target_level = next(
        level
        for level in orchestrator.config.models[other].thinking_levels
        if level != opened_on.thinking
    )
    await orchestrator.set_field(f"/models/{other}/thinking", target_level)
    await orchestrator.reload()
    picked_level = next(
        level
        for level in orchestrator.config.models[opened_on.alias].thinking_levels
        if level != opened_on.thinking and level != target_level
    )
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    await adapter.write_model_config(
        ModelConfigWriteParams(
            session_id=_RecordingSession.session_id, reasoning_effort=picked_level
        )
    )
    assert session.pin(SessionPin.REASONING_EFFORT) == picked_level

    # Do
    await adapter.write_model_config(
        ModelConfigWriteParams(
            session_id=_RecordingSession.session_id, model_alias=other
        )
    )

    # Assert
    now_active = context.config_orchestrator.config.get_active_model()
    assert now_active.alias == other
    assert session.pin(SessionPin.REASONING_EFFORT) == now_active.thinking


@pytest.mark.asyncio
async def test_unified_moving_to_a_profile_owned_model_clears_the_pinned_thinking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A session that picked a level, then moved to a model an agent
    profile declares, as switching to Lean does.
    *Do*: Record the level the session now runs.
    *Assert*: The pin is cleared rather than left on the previous model's level.
    The profile owns that model's level, and a stale pin would be read back as
    this session's own by every client that reads it without resuming it.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._storage import SessionPin
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ModelConfigWriteParams
    from vibe.core.agents.registry import apply_profile_overrides

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    orchestrator = context.config_orchestrator
    opened_on = orchestrator.config.get_active_model()
    picked = "high" if opened_on.thinking != "high" else "low"
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    await adapter.write_model_config(
        ModelConfigWriteParams(
            session_id=_RecordingSession.session_id, reasoning_effort=picked
        )
    )
    assert session.pin(SessionPin.REASONING_EFFORT) == picked
    apply_profile_overrides(
        orchestrator,
        {
            "active_model": "profile-owned",
            "models": [
                {
                    "name": "profile-owned-model",
                    "provider": "mistral",
                    "alias": "profile-owned",
                    "thinking": "medium",
                }
            ],
        },
    )

    # Do
    await adapter._persist_session_reasoning_effort()  # pyright: ignore[reportPrivateUsage]

    # Assert
    assert orchestrator.config.get_active_model().alias == "profile-owned"
    assert not session.pin(SessionPin.REASONING_EFFORT)


@pytest.mark.asyncio
async def test_unified_queueing_a_turn_leaves_the_running_one_on_its_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A running turn, and the default picked during it -- which the
    session records as the empty alias the user chose.
    *Do*: Queue a follow-up turn.
    *Assert*: The adapter still serves the model the running turn started on.
    Enqueue records the model a queued turn will run, and resolving the empty
    pin reads as a change; adopting it there would swap the model under the
    turn already running, which is the whole reason a pick is parked.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._storage import SessionPin
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ModelConfigWriteParams

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    config = context.config_orchestrator.config
    default = config.resolve_default_model_alias()
    pinned = next(alias for alias in config.models if alias != default)
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    await adapter.write_model_config(
        ModelConfigWriteParams(
            session_id=_RecordingSession.session_id, model_alias=pinned
        )
    )
    session.active_turn_id = "turn-1"
    running_model = adapter._model
    await adapter.write_model_config(
        ModelConfigWriteParams(session_id=_RecordingSession.session_id, model_alias="")
    )

    # Do
    await adapter.enqueue_turn(
        TurnEnqueueParams(
            session_id=_RecordingSession.session_id,
            idempotency_key="enqueue",
            entries=[
                TurnUserInputEntry(content=[SessionTextContentBlock(text="queued")])
            ],
        )
    )

    # Assert
    assert adapter._model == running_model
    assert adapter._deferred.parked is True
    assert session.pin(SessionPin.ACTIVE_MODEL) == ""
    assert context.config_orchestrator.config.get_active_model().alias == default


@pytest.mark.asyncio
async def test_unified_a_model_pick_waits_for_a_teleport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A session mid-teleport.
    *Do*: Pick a model.
    *Assert*: Refused. A teleport copies the session as it stands, so a
    configuration moving underneath it is the one thing a pick cannot do --
    unlike a running turn, which parks the pick instead.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._session_backend_port import SessionBackendError
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ModelConfigWriteParams

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    config = context.config_orchestrator.config
    picked = next(
        alias for alias in config.models if alias != config.get_active_model().alias
    )
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    adapter._teleport_active = "teleport-1"

    # Do / Assert
    with pytest.raises(SessionBackendError) as refused:
        await adapter.write_model_config(
            ModelConfigWriteParams(
                session_id=_RecordingSession.session_id, model_alias=picked
            )
        )

    assert refused.value.code is ProtocolErrorCode.CONFLICT
    assert context.config_orchestrator.config.get_active_model().alias != picked


@pytest.mark.asyncio
async def test_unified_settled_pick_announces_the_runtime_it_landed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A live Unified adapter holding a pick parked by a running turn.
    *Do*: Drain the turn and settle, then settle again with nothing parked.
    *Assert*: Settling emits ``runtime/updated`` carrying the picked model, once.
    The write answered ``pending``, so dispatch emitted nothing for it: without
    this notice, subscribers keep the outgoing model after the session has
    already moved off it.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ModelConfigWriteParams, RuntimeUpdatedParams

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    config = context.config_orchestrator.config
    picked = next(
        alias for alias in config.models if alias != config.get_active_model().alias
    )
    services = Mock()
    services.notify = AsyncMock()
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session),
        context,
        context.derive(UnifiedSessionSettings()),
        services=cast(Any, services),
    )
    session.active_turn_id = "turn-1"
    await adapter.write_model_config(
        ModelConfigWriteParams(
            session_id=_RecordingSession.session_id, model_alias=picked
        )
    )

    assert services.notify.await_count == 0

    # Do
    session.active_turn_id = None
    await adapter._settle_configuration()  # pyright: ignore[reportPrivateUsage]
    await adapter._settle_configuration()  # pyright: ignore[reportPrivateUsage]

    # Assert
    services.notify.assert_awaited_once()
    method, params = services.notify.await_args.args
    assert method == "runtime/updated"
    assert isinstance(params, RuntimeUpdatedParams)
    assert params.session_id == _RecordingSession.session_id
    assert params.runtime.config.active_model.alias == picked


@pytest.mark.asyncio
async def test_unified_a_failed_announcement_still_lets_the_turn_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A parked pick and a transport that refuses notifications.
    *Do*: Drain the turn and settle.
    *Assert*: Settling still lands the pick and does not raise. A turn opens on
    this path, so an announcement nobody can receive must not be what keeps the
    session from starting it.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ModelConfigWriteParams

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    config = context.config_orchestrator.config
    picked = next(
        alias for alias in config.models if alias != config.get_active_model().alias
    )

    services = Mock()
    services.notify = AsyncMock(side_effect=ConnectionResetError("the client is gone"))
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session),
        context,
        context.derive(UnifiedSessionSettings()),
        services=cast(Any, services),
    )
    session.active_turn_id = "turn-1"
    await adapter.write_model_config(
        ModelConfigWriteParams(
            session_id=_RecordingSession.session_id, model_alias=picked
        )
    )

    # Do
    session.active_turn_id = None
    await adapter._settle_configuration()  # pyright: ignore[reportPrivateUsage]

    # Assert
    assert cast(Any, session.applied[-1]).active_model.model == (
        context.config_orchestrator.config.models[picked].name
    )


@pytest.mark.asyncio
async def test_unified_model_pick_pins_the_session_before_the_next_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A live Unified adapter with a turn in flight.
    *Do*: Pick a different model, and never start another turn.
    *Assert*: The session's own pin already names the pick. The pin is what a
    reopened session restores from, so leaving it to the next turn would reopen
    on the model the running turn started with, contradicting the transcript
    marker this same pick wrote.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._storage import SessionPin
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ModelConfigWriteParams

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    config = context.config_orchestrator.config
    picked = next(
        alias for alias in config.models if alias != config.get_active_model().alias
    )
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    session.active_turn_id = "turn-1"

    # Do
    await adapter.write_model_config(
        ModelConfigWriteParams(
            session_id=_RecordingSession.session_id, model_alias=picked
        )
    )

    # Assert
    assert session.pin(SessionPin.ACTIVE_MODEL) == picked


@pytest.mark.asyncio
async def test_unified_parked_pick_survives_a_turn_that_wins_the_flush_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A parked pick, and a turn that starts while it is being applied.
    *Do*: Flush the pending derivation.
    *Assert*: No exception, and the pick stays parked. A raise would end the
    session's live stream, and clearing the flag would drop the pick.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ConfigWriteOpWire, ConfigWriteParams

    class _TurnStartsMidFlush(_RecordingSession):
        """Idle when the flush looks, running by the time the push lands."""

        def __init__(self) -> None:
            super().__init__()
            self._turn_reads = 0

        @property
        def active_turn_id(self) -> str | None:
            self._turn_reads += 1
            return None if self._turn_reads <= 1 else "turn-1"

        @active_turn_id.setter
        def active_turn_id(self, value: str | None) -> None:
            self._active_turn_id = value

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    config = context.config_orchestrator.config
    picked = next(
        alias for alias in config.models if alias != config.get_active_model().alias
    )
    session = _TurnStartsMidFlush()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    await adapter.write_config(
        ConfigWriteParams(
            session_id=_RecordingSession.session_id,
            ops=[ConfigWriteOpWire(op="set", path="/active_model", value=picked)],
            reason="mid-turn pick",
        )
    )
    pushes_before = len(session.settings)

    # Do
    await adapter._settle_configuration()  # pyright: ignore[reportPrivateUsage]

    # Assert
    assert adapter._deferred.parked is True  # pyright: ignore[reportPrivateUsage]
    assert len(session.settings) == pushes_before


@pytest.mark.asyncio
async def test_unified_config_write_mid_turn_conflicts_beyond_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A live Unified adapter with a turn in flight.
    *Do*: Write a setting that is not the model or its thinking level.
    *Assert*: ``CONFLICT``. Only the settings a running turn cannot read defer.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ConfigWriteOpWire, ConfigWriteParams

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    active_model = context.config_orchestrator.config.get_active_model()
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    session.active_turn_id = "turn-1"

    # Do / Assert
    with pytest.raises(SessionBackendError) as exc_info:
        await adapter.write_config(
            ConfigWriteParams(
                session_id=_RecordingSession.session_id,
                ops=[
                    ConfigWriteOpWire(
                        op="set",
                        path=f"/models/{active_model.alias}/auto_compact_threshold",
                        value=48_000,
                    )
                ],
                reason="mid-turn write",
            )
        )

    assert exc_info.value.code is ProtocolErrorCode.CONFLICT


@pytest.mark.asyncio
async def test_unified_config_write_rejects_invalid_core_settings_before_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A live Unified adapter with its accepted compaction threshold.
    *Do*: Write a schema-valid threshold too small for Harness Core's base context.
    *Assert*: The write is rejected without changing persisted or live configuration.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ConfigWriteOpWire, ConfigWriteParams

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    previous_threshold = (
        context.config_orchestrator.config.get_active_model().auto_compact_threshold
    )
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )

    # Do
    result = await adapter.write_config(
        ConfigWriteParams(
            session_id=_RecordingSession.session_id,
            ops=[ConfigWriteOpWire(op="set", path="/auto_compact_threshold", value=1)],
            reason="test rejected compaction configuration",
        )
    )

    # Assert
    assert result.response.rejected is True
    assert (
        context.config_orchestrator.config.get_active_model().auto_compact_threshold
        == previous_threshold
    )
    assert session.settings == []
    assert session.applied == []


@pytest.mark.asyncio
async def test_unified_runtime_denies_a_tool_disabled_by_a_live_config_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings
    from vibe.core.config.patch import AddOperationPatch

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), auto_approve=True)
    )
    before = context.derive(UnifiedSessionSettings()).adapter_config.tool_modes

    # Every name the shell tool goes by, so the assertion holds on the platform
    # whose catalogue spells it ``powershell`` or ``git_bash`` too.
    failures = await context.config_orchestrator.apply_patch(
        [
            AddOperationPatch(
                path="/disabled_tools", value=["bash", "powershell", "git_bash"]
            )
        ],
        reason="test",
    )
    after = context.derive(UnifiedSessionSettings()).adapter_config.tool_modes

    assert failures == []
    assert before["file_system.bash"] == "allow"
    assert before["process.start"] == "allow"
    assert after["file_system.bash"] == "deny"
    assert after["process.start"] == "deny"
    assert after["file_system.read_file"] == "allow"


def _write_workspace_skill(
    root: Path, name: str, body: str, *, disable_model_invocation: bool = False
) -> Path:
    path = root / ".vibe" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    invocation_policy = (
        "disable-model-invocation: true\n" if disable_model_invocation else ""
    )
    path.write_text(
        f"---\nname: {name}\ndescription: Reviews a diff.\n"
        f"{invocation_policy}---\n\n{body}\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio
async def test_unified_runtime_config_carries_a_workspace_skill_to_both_sides_of_the_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a workspace ``SKILL.md``.

    Do derive a runtime configuration.

    Assert Core gets the path it renders into the prompt and the adapter gets
    the rendered body. Core advertises a skill the Runtime is then asked to
    serve, so the two halves have to come out of the same derivation.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    skill_path = _write_workspace_skill(tmp_path, "code-review", "Read the diff twice.")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), trust_workspace=True)
    )
    derivation = context.derive(UnifiedSessionSettings())

    definitions = {
        definition.name: definition
        for definition in derivation.core_config.capabilities.skills
    }
    assert definitions["code-review"].path == str(skill_path)
    assert definitions["code-review"].description == "Reviews a diff."
    assert "Read the diff twice." in derivation.adapter_config.skills["code-review"]
    # Nothing Core can name may be missing a body: the enum on the `skill` tool
    # is built from the catalogue, so a gap is a call that can only fail.
    assert set(definitions) <= set(derivation.adapter_config.skills)


@pytest.mark.asyncio
async def test_unified_explicit_only_skill_stays_available_to_slash_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    _write_workspace_skill(
        tmp_path,
        "manual-review",
        "Review only when asked.",
        disable_model_invocation=True,
    )
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), trust_workspace=True)
    )
    derivation = context.derive(UnifiedSessionSettings())

    assert "manual-review" not in {
        skill.name for skill in derivation.core_config.capabilities.skills
    }
    assert "manual-review" not in derivation.adapter_config.skills
    assert "manual-review" in derivation.skill_payloads
    assert "manual-review" in {skill.name for skill in derivation.runtime.skills}

    session = _RecordingSession()
    session.cwd = str(tmp_path)
    adapter = UnifiedHarnessBackendAdapter(cast(Any, session), context, derivation)
    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id,
            message=[TextContentBlock(text="/manual-review")],
        )
    )

    assert "Review only when asked." in session.sent[-1].message[-1].text


@pytest.mark.asyncio
async def test_unified_runtime_config_picks_up_a_skill_added_after_the_session_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a session, then add a ``SKILL.md`` to the workspace.

    Do derive again, the way ``/reload`` does.

    Assert the new skill is there. Skill discovery runs in the constructor, so a
    manager hoisted out of ``derive`` would keep serving the catalogue it read
    at startup and ``/reload`` would silently never converge.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), trust_workspace=True)
    )
    before = context.derive(UnifiedSessionSettings())

    _write_workspace_skill(tmp_path, "code-review", "Read the diff twice.")
    after = context.derive(UnifiedSessionSettings())

    assert "code-review" not in {s.name for s in before.core_config.capabilities.skills}
    assert "code-review" in {s.name for s in after.core_config.capabilities.skills}
    assert "code-review" in after.adapter_config.skills


@pytest.mark.asyncio
async def test_unified_reload_pushes_the_new_catalogue_into_the_live_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a live adapter, then add a ``SKILL.md`` to its workspace.

    Do reload the configuration.

    Assert both halves of the seam were pushed, bodies before catalogue. Only
    Core decides what the prompt advertises, so a reload that re-derives without
    reconfiguring converges the client's view and nothing the model can see.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ConfigReloadParams

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), trust_workspace=True)
    )
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    _write_workspace_skill(tmp_path, "code-review", "Read the diff twice.")

    result = await adapter.reload_config(
        ConfigReloadParams(session_id=_RecordingSession.session_id)
    )

    pushed = cast(Any, session.capabilities[-1])
    assert "code-review" in {skill.name for skill in pushed.skills}
    assert "code-review" in cast(Any, session.applied[-1]).skills
    assert "code-review" in {skill.name for skill in result.response.runtime.skills}
    # The body has to be servable before Core is allowed to advertise it.
    assert len(session.applied) == len(session.capabilities)


@pytest.mark.asyncio
async def test_unified_reload_keeps_what_the_session_is_connected_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a live adapter holding an MCP server and a connected connector.

    Do reload the configuration.

    Assert both survive. A derivation only projects the layered config and
    leaves ``mcp``/``connectors`` empty, so adopting its snapshot whole drops
    every connection the session actually holds — the client would show no MCP
    sources and no connectors until some later catalogue call re-projected them.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ConfigReloadParams

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), trust_workspace=True)
    )
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    adapter.update_mcp_projection(
        MCPState(
            sources=[
                MCPSourceSummary(
                    name="local",
                    kind=MCPSourceKind.SERVER,
                    transport="stdio",
                    status=MCPSourceStatus.CONNECTED,
                )
            ]
        )
    )
    adapter._update_connector_projection(
        SessionConnectorState(
            accepted_catalog_revision="rev",
            accepted_selection_revision="rev",
            route_revision="rev",
            sources=(
                SessionConnectorSourceState(
                    raw_id="github",
                    alias="github",
                    display_name="GitHub",
                    status="connected",
                ),
            ),
            discovery_errors={},
        )
    )

    runtime = (
        await adapter.reload_config(
            ConfigReloadParams(session_id=_RecordingSession.session_id)
        )
    ).response.runtime

    assert [
        (source.name, source.display_name, source.kind)
        for source in runtime.mcp.sources
    ] == [
        ("local", "local", MCPSourceKind.SERVER),
        ("github", "GitHub", MCPSourceKind.CONNECTOR),
    ]
    assert runtime.connectors == ConnectorCounts(connected=1, total=1)


async def _unified_trust_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, Any, Any]:
    """A live unified adapter over an untrusted cwd containing an AGENTS.md."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )

    (tmp_path / "AGENTS.md").write_text("Trusted instructions", encoding="utf-8")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    session = _RecordingSession()
    session.cwd = str(tmp_path)
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    return adapter, context, session


@pytest.mark.asyncio
async def test_unified_trust_decision_grants_the_session_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, context, session = await _unified_trust_adapter(tmp_path, monkeypatch)
    reload_config = AsyncMock(wraps=context.config_orchestrator.reload)
    monkeypatch.setattr(context.config_orchestrator, "reload", reload_config)

    def project_instructions() -> str:
        return runtime_module._build_unified_system_instructions(
            context.config_orchestrator.config, context.harness_files, cwd=tmp_path
        )

    assert "Trusted instructions" not in project_instructions()

    result = await adapter.dispatch_extension(
        "workspace/trust/decision",
        {"sessionId": _RecordingSession.session_id, "decision": "trust_cwd"},
    )

    assert result.runtime_updated is True
    assert result.response.status == "trusted"
    assert trusted_folders_manager.is_trusted(tmp_path) is True
    reload_config.assert_awaited_once()
    # runtime_updated is only the dispatch flag; the re-derived runtime must
    # also reach the session, and the trusted project doc must land in it.
    assert session.applied
    assert "Trusted instructions" in project_instructions()


@pytest.mark.asyncio
async def test_unified_trust_decision_rejects_an_unknown_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, _, _ = await _unified_trust_adapter(tmp_path, monkeypatch)

    with pytest.raises(SessionBackendError) as excinfo:
        await adapter.dispatch_extension(
            "workspace/trust/decision",
            {"sessionId": "missing", "decision": "trust_cwd"},
        )

    assert excinfo.value.code is ProtocolErrorCode.NOT_FOUND
    assert trusted_folders_manager.is_trusted(tmp_path) is not True


@pytest.mark.asyncio
async def test_unified_trust_decision_accepts_a_symlink_to_the_session_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, context, _ = await _unified_trust_adapter(tmp_path, monkeypatch)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)

    result = await adapter.dispatch_extension(
        "workspace/trust/decision",
        {
            "sessionId": _RecordingSession.session_id,
            "decision": "trust_cwd",
            "cwd": str(alias),
        },
    )

    assert result.response.status == "trusted"
    assert trusted_folders_manager.is_trusted(tmp_path) is True
    assert trusted_folders_manager.is_trusted(alias) is True


@pytest.mark.asyncio
async def test_unified_trust_decision_rejects_a_foreign_cwd_while_a_turn_is_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, _, session = await _unified_trust_adapter(tmp_path, monkeypatch)
    session.active_turn_id = "turn-1"
    other = tmp_path / "attacker-dir"
    other.mkdir()

    # The cwd pin outranks the idle requirement: a foreign cwd is an
    # invalid request no matter the turn state.
    with pytest.raises(SessionBackendError) as excinfo:
        await adapter.dispatch_extension(
            "workspace/trust/decision",
            {
                "sessionId": _RecordingSession.session_id,
                "decision": "trust_cwd",
                "cwd": str(other),
            },
        )

    assert excinfo.value.code is ProtocolErrorCode.INVALID_PARAMS
    assert trusted_folders_manager.is_trusted(tmp_path) is not True


@pytest.mark.asyncio
@pytest.mark.parametrize("cwd", [None, ""])
async def test_unified_trust_decision_rejects_a_session_without_a_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cwd: str | None
) -> None:
    adapter, _, session = await _unified_trust_adapter(tmp_path, monkeypatch)
    # The harness's legacy-migration fallback can persist an empty cwd; neither
    # it nor a missing cwd may resolve to the server process cwd.
    session.cwd = cwd

    with pytest.raises(SessionBackendError) as excinfo:
        await adapter.dispatch_extension(
            "workspace/trust/decision",
            {"sessionId": _RecordingSession.session_id, "decision": "trust_cwd"},
        )

    assert excinfo.value.code is ProtocolErrorCode.INVALID_PARAMS
    assert trusted_folders_manager.is_trusted(tmp_path) is not True


@pytest.mark.asyncio
async def test_unified_trust_decision_rejects_a_foreign_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, context, _ = await _unified_trust_adapter(tmp_path, monkeypatch)
    reload_config = AsyncMock(wraps=context.config_orchestrator.reload)
    monkeypatch.setattr(context.config_orchestrator, "reload", reload_config)
    other = tmp_path / "attacker-dir"
    other.mkdir()
    (other / "AGENTS.md").write_text("Hostile instructions", encoding="utf-8")

    with pytest.raises(SessionBackendError) as excinfo:
        await adapter.dispatch_extension(
            "workspace/trust/decision",
            {
                "sessionId": _RecordingSession.session_id,
                "decision": "trust_cwd",
                "cwd": str(other),
            },
        )

    assert excinfo.value.code is ProtocolErrorCode.INVALID_PARAMS
    assert trusted_folders_manager.is_trusted(other) is not True
    assert trusted_folders_manager.is_trusted(tmp_path) is not True
    reload_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_unified_trust_decision_decline_writes_no_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, context, _ = await _unified_trust_adapter(tmp_path, monkeypatch)
    reload_config = AsyncMock(wraps=context.config_orchestrator.reload)
    monkeypatch.setattr(context.config_orchestrator, "reload", reload_config)

    result = await adapter.dispatch_extension(
        "workspace/trust/decision",
        {"sessionId": _RecordingSession.session_id, "decision": "decline"},
    )

    assert result.runtime_updated is False
    assert result.response.status == "untrusted"
    assert trusted_folders_manager.is_explicitly_untrusted(tmp_path) is True
    reload_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_unified_trust_decision_grant_requires_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, _, session = await _unified_trust_adapter(tmp_path, monkeypatch)
    session.active_turn_id = "turn-1"

    with pytest.raises(SessionBackendError) as excinfo:
        await adapter.dispatch_extension(
            "workspace/trust/decision",
            {"sessionId": _RecordingSession.session_id, "decision": "trust_cwd"},
        )

    assert excinfo.value.code is ProtocolErrorCode.CONFLICT
    assert trusted_folders_manager.is_trusted(tmp_path) is not True


@pytest.mark.asyncio
async def test_unified_connector_projection_keeps_server_error_on_name_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare an MCP server "buildkite" that failed discovery, then project a connector
    that is also aliased "buildkite".

    Do project the connector state.

    Assert the server's discovery error survives so the TUI can warn about it. It was
    dropped before because the projection discarded any discovery error whose name
    matched a connector alias, and a server and connector shared the name "buildkite".
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), trust_workspace=True)
    )
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    adapter.update_mcp_projection(
        MCPState(
            sources=[
                MCPSourceSummary(
                    name="buildkite",
                    kind=MCPSourceKind.SERVER,
                    transport="streamable-http",
                    status=MCPSourceStatus.UNAVAILABLE,
                )
            ],
            discovery_errors={"buildkite": "authentication was rejected"},
        )
    )

    adapter._update_connector_projection(
        SessionConnectorState(
            accepted_catalog_revision="rev",
            accepted_selection_revision="rev",
            route_revision="rev",
            sources=(
                SessionConnectorSourceState(
                    raw_id="buildkite",
                    alias="buildkite",
                    display_name="Buildkite",
                    status="needs_auth",
                ),
            ),
            discovery_errors={},
        )
    )

    assert adapter._runtime.mcp.discovery_errors == {
        "buildkite": "authentication was rejected"
    }

    # A later server projection where "buildkite" recovered must drop the error rather
    # than freezing it as a connector error under the shared name.
    adapter.update_mcp_projection(
        MCPState(
            sources=[
                MCPSourceSummary(
                    name="buildkite",
                    kind=MCPSourceKind.SERVER,
                    transport="streamable-http",
                    status=MCPSourceStatus.CONNECTED,
                )
            ],
            discovery_errors={},
        )
    )

    assert adapter._runtime.mcp.discovery_errors == {}


async def _skill_adapter(
    tmp_path: Path,
    session: _RecordingSession,
    *,
    skill_body: str = "Read the diff twice.",
    workspace_roots: Sequence[Path] = (),
) -> Any:
    """An adapter over a workspace holding one user-invocable skill."""
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )

    _write_workspace_skill(tmp_path, "code-review", skill_body)
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(
            cwd=str(tmp_path),
            trust_workspace=True,
            workspace_roots=[str(root) for root in workspace_roots],
        )
    )
    session.cwd = str(tmp_path)
    return UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )


@pytest.mark.asyncio
async def test_unified_start_turn_appends_the_body_of_an_invoked_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare an adapter over a workspace skill.

    Do start a turn whose prompt is ``/code-review``.

    Assert the rendered body rides the message. Core owns model-visible history
    under Unified, so there is no fabricated tool-call pair to inject; the body
    has to travel as content or the slash command does nothing at all.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)
    display = UserDisplayContent(
        version="1", host="vibe", content=[{"type": "text", "text": "shown prompt"}]
    )
    prompt = (
        '/code-review compare <skill_content name="code-review">example</skill_content>'
    )

    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id,
            message=[TextContentBlock(text=prompt)],
            user_display_content=display,
        )
    )

    blocks = session.sent[-1].message
    assert blocks[0].text == prompt
    assert "Read the diff twice." in blocks[-1].text
    assert '<skill_content name="code-review">' in blocks[-1].text
    session.preview = f"{prompt} {blocks[-1].text.replace(chr(10), ' ')}"
    adapter._skills = {"code-review": "changed after invocation"}

    resumed = await adapter.read(
        SessionReadParams(session_id=session.session_id, history=PageRequest(limit=10))
    )
    assert resumed.state.history is not None
    message, effect = resumed.state.history
    assert isinstance(message, PublicMessageEntry)
    assert message.text == prompt
    assert message.user_display_content == display
    assert resumed.state.session.preview == prompt
    assert isinstance(effect, PublicEffectEntry)
    assert isinstance(effect.detail, SkillEffectDetail)
    assert effect.detail.input is not None
    assert effect.detail.input.name == "code-review"
    assert isinstance(effect.state, CompletedEffectState)
    assert effect.state.display.message == "skill: code-review"
    assert "Read the diff twice." in effect.state.output_text


@pytest.mark.asyncio
async def test_unified_start_turn_appends_every_skill_mentioned_with_a_slash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    _write_workspace_skill(tmp_path, "lint", "Run the linter first.")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)
    prompt = "please apply /lint, then /code-review and /unknown"

    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id, message=[TextContentBlock(text=prompt)]
        )
    )

    blocks = session.sent[-1].message
    assert [block.text for block in blocks[:1]] == [prompt]
    assert '<skill_content name="lint">' in blocks[1].text
    assert '<skill_content name="code-review">' in blocks[2].text
    assert len(blocks) == 3

    resumed = await adapter.read(
        SessionReadParams(session_id=session.session_id, history=PageRequest(limit=10))
    )
    assert resumed.state.history is not None
    message, *effects = resumed.state.history
    assert isinstance(message, PublicMessageEntry)
    assert message.text == prompt
    assert message.user_display_content is None
    assert [
        cast(SkillEffectDetail, effect.detail).input
        for effect in effects
        if isinstance(effect, PublicEffectEntry)
    ] == [SkillEffectInput(name="lint"), SkillEffectInput(name="code-review")]
    assert len({effect.id for effect in effects}) == 2
    assert all(effect.related_entry_id == message.id for effect in effects)


@pytest.mark.asyncio
async def test_unified_start_turn_points_at_a_mentioned_skill_already_loaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    _write_workspace_skill(tmp_path, "lint", "Run the linter first.")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)

    for prompt in ("/code-review", "now /lint and /code-review"):
        await adapter.start_turn(
            TurnStartParams(
                session_id=session.session_id, message=[TextContentBlock(text=prompt)]
            )
        )

    _prompt, lint, review = session.sent[-1].message
    assert "Run the linter first." in lint.text
    assert review.text == already_loaded_message("code-review")


@pytest.mark.asyncio
async def test_unified_enqueue_turn_appends_and_hides_mentioned_skill_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    _write_workspace_skill(tmp_path, "lint", "Run the linter first.")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)
    prompt = "/code-review with /lint"

    await adapter.enqueue_turn(
        TurnEnqueueParams(
            session_id=session.session_id,
            entries=[
                TurnUserInputEntry(content=[SessionTextContentBlock(text=prompt)])
            ],
        )
    )

    blocks = session.sent[-1].entries[-1].content
    assert blocks[0].text == prompt
    assert '<skill_content name="code-review">' in blocks[1].text
    assert '<skill_content name="lint">' in blocks[2].text
    queue = await adapter.read_turn_queue(
        TurnQueueReadParams(session_id=session.session_id)
    )
    public_entry = queue.response.queue.items[0].entries[0]
    assert [cast(Any, block).text for block in public_entry.content] == [prompt]
    assert public_entry.annotations.vibe_user_display_content is None


@pytest.mark.asyncio
async def test_unified_prepared_prompt_counts_mentioned_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    (tmp_path / "notes.md").write_text("notes", encoding="utf-8")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)

    result = await adapter.dispatch_extension(
        "workspace/prompt/prepare",
        {
            "sessionId": session.session_id,
            "message": "/code-review /code-review @notes.md /unknown",
        },
    )

    mentions = cast(WorkspacePromptPrepareResponse, result.response).prompt.mentions
    assert mentions.count == 2
    assert mentions.context_types == {"file": 1, "skill": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("history_limit", [1, 2])
async def test_unified_read_keeps_bounded_skill_history_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, history_limit: int
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)

    for prompt in ("older message", "/code-review"):
        await adapter.start_turn(
            TurnStartParams(
                session_id=session.session_id, message=[TextContentBlock(text=prompt)]
            )
        )
    resumed = await adapter.read(
        SessionReadParams(
            session_id=session.session_id, history=PageRequest(limit=history_limit)
        )
    )

    assert resumed.state.history is not None
    assert len(resumed.state.history) == history_limit + 1
    message, effect = resumed.state.history[-2:]
    assert isinstance(message, PublicMessageEntry)
    assert message.text == "/code-review"
    assert isinstance(effect, PublicEffectEntry)
    assert effect.related_entry_id == message.id
    assert isinstance(effect.detail, SkillEffectDetail)
    assert effect.detail.input is not None
    assert effect.detail.input.name == "code-review"


@pytest.mark.asyncio
@pytest.mark.parametrize("already_loaded", [False, True])
async def test_unified_history_projects_a_pre_marker_skill_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, already_loaded: bool
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)
    payload = (
        already_loaded_message("code-review")
        if already_loaded
        else adapter._skills["code-review"]
    )
    session.sent.append(
        SimpleNamespace(
            message=[
                TextContentBlock(text="/code-review please"),
                TextContentBlock(text=payload),
            ]
        )
    )

    resumed = await adapter.read(
        SessionReadParams(session_id=session.session_id, history=PageRequest(limit=10))
    )

    assert resumed.state.history is not None
    message, effect = resumed.state.history
    assert isinstance(message, PublicMessageEntry)
    assert message.text == "/code-review please"
    assert isinstance(effect, PublicEffectEntry)
    assert isinstance(effect.detail, SkillEffectDetail)
    assert effect.detail.input is not None
    assert effect.detail.input.name == "code-review"


@pytest.mark.asyncio
async def test_unified_history_hides_mixed_case_already_loaded_skill_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)
    session.sent.append(
        SimpleNamespace(
            message=[
                TextContentBlock(text="/MySkill please"),
                TextContentBlock(text=already_loaded_message("MySkill")),
            ]
        )
    )

    resumed = await adapter.read(
        SessionReadParams(session_id=session.session_id, history=PageRequest(limit=10))
    )

    assert resumed.state.history is not None
    message, effect = resumed.state.history
    assert isinstance(message, PublicMessageEntry)
    assert message.text == "/MySkill please"
    assert isinstance(effect, PublicEffectEntry)
    assert isinstance(effect.detail, SkillEffectDetail)
    assert effect.detail.input is not None
    assert effect.detail.input.name == "MySkill"


@pytest.mark.asyncio
async def test_unified_history_projects_a_skill_body_containing_skill_markup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nested marker text in a skill cannot make its outer payload public."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    nested = (
        '<skill_content name="code-review">\n'
        "# Skill: code-review\n\nexample\n"
        "</skill_content>"
    )
    session = _RecordingSession()
    adapter = await _skill_adapter(
        tmp_path, session, skill_body=f"Document `</skill_content>` and this: {nested}"
    )

    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id,
            message=[TextContentBlock(text="/code-review")],
        )
    )
    blocks = session.sent[-1].message
    session.preview = f"/code-review {blocks[-1].text.replace(chr(10), ' ')}"

    resumed = await adapter.read(
        SessionReadParams(session_id=session.session_id, history=PageRequest(limit=10))
    )
    assert resumed.state.history is not None
    assert resumed.state.session.preview == "/code-review"
    message, effect = resumed.state.history
    assert isinstance(message, PublicMessageEntry)
    assert message.text == "/code-review"
    assert isinstance(effect, PublicEffectEntry)
    assert isinstance(effect.state, CompletedEffectState)
    assert nested in effect.state.output_text

    nested_marker = '<skill_content name="code-review">'
    nested_at = session.preview.rfind(nested_marker)
    assert nested_at >= 0
    session.preview = (
        session.preview[: nested_at + len(nested_marker)] + " # Skill: code-rev…"
    )
    truncated = await adapter.read(
        SessionReadParams(session_id=session.session_id, history=PageRequest(limit=10))
    )
    assert truncated.state.session.preview == "/code-review"


@pytest.mark.asyncio
async def test_unified_enqueue_turn_appends_skill_body_after_mentioned_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A previously loaded skill whose body mentions another file.
    *Do*: Enqueue the skill invocation with a user-authored file mention.
    *Assert*: Core retains the body, while the public queue hides only that body.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    (tmp_path / "notes.md").write_text("user file", encoding="utf-8")
    (tmp_path / "secret.md").write_text("skill file", encoding="utf-8")
    _write_workspace_skill(tmp_path, "mention-doc", "Use @secret.md as an example.")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)
    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id,
            message=[TextContentBlock(text="/mention-doc")],
        )
    )

    # Do
    await adapter.enqueue_turn(
        TurnEnqueueParams(
            session_id=session.session_id,
            entries=[
                TurnUserInputEntry(
                    content=[
                        SessionTextContentBlock(text="/mention-doc read @notes.md")
                    ]
                )
            ],
        )
    )

    # Assert
    blocks = session.sent[-1].entries[-1].content
    resource_names = [
        Path(block.uri).name for block in blocks if block.type == "embedded_resource"
    ]
    assert resource_names == ["notes.md"]
    assert blocks[0].text == "/mention-doc read @notes.md"
    assert "Use @secret.md as an example." in blocks[-1].text
    assert '<skill_content name="mention-doc">' in blocks[-1].text
    adapter._skills = {"mention-doc": "changed while queued"}

    queue = await adapter.read_turn_queue(
        TurnQueueReadParams(session_id=session.session_id)
    )
    public_entry = queue.response.queue.items[0].entries[0]
    assert [block.type for block in public_entry.content] == [
        "text",
        "embedded_resource",
    ]
    assert cast(Any, public_entry.content[0]).text == "/mention-doc read @notes.md"
    assert public_entry.annotations.vibe_user_display_content is None


@pytest.mark.asyncio
async def test_unified_replace_queued_turn_appends_the_body_of_an_invoked_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A queued turn replacement that invokes a workspace skill.
    *Do*: Replace the queued turn through the Unified adapter.
    *Assert*: The replacement keeps its ID and includes the rendered skill body.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)

    # Do
    result = await adapter.replace_queued_turn(
        TurnQueueReplaceParams(
            session_id=session.session_id,
            queue_item_id="queue-1",
            entries=[
                TurnUserInputEntry(
                    content=[SessionTextContentBlock(text="/code-review please")]
                )
            ],
        )
    )

    # Assert
    blocks = session.sent[-1].entries[-1].content
    assert result.response.queue_item_id == "queue-1"
    assert blocks[0].text == "/code-review please"
    assert "Read the diff twice." in blocks[-1].text
    assert '<skill_content name="code-review">' in blocks[-1].text


@pytest.mark.asyncio
async def test_unified_queued_steer_forwards_only_stored_queue_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A Unified adapter and an atomic queued-steer request.
    *Do*: Send the request through the adapter.
    *Assert*: The Harness receives only IDs and the response follows flushed events.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    if getattr(harness_session_protocol, "TurnQueueSteerParams", None) is None:
        pytest.skip("installed Unified Harness does not support queued steering")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)
    flushed = False

    async def flush_events() -> None:
        nonlocal flushed
        flushed = True
        adapter._event_id = 7

    monkeypatch.setattr(adapter, "flush_events", flush_events)
    params = TurnQueueSteerParams(
        session_id="session-1", queue_item_id="queue-1", expected_turn_id="turn-1"
    )

    # Do
    result = await adapter.steer_queued_turn(params)

    # Assert
    harness_params = session.sent[-1]
    assert harness_params.model_dump(mode="json") == {
        "sessionId": "session-1",
        "queueItemId": "queue-1",
        "expectedTurnId": "turn-1",
    }
    assert flushed
    assert result.response == TurnQueueSteerResponse(
        queue_item_id="queue-1", turn_id="turn-1", last_event_id=7
    )


@pytest.mark.asyncio
async def test_unified_queued_steer_rejects_an_older_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An installed Harness without queued steering fails as unsupported."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.delattr(harness_session_protocol, "TurnQueueSteerParams", raising=False)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)

    with pytest.raises(SessionBackendError) as exc_info:
        await adapter.steer_queued_turn(
            TurnQueueSteerParams(
                session_id="session-1",
                queue_item_id="queue-1",
                expected_turn_id="turn-1",
            )
        )

    assert exc_info.value.code is ProtocolErrorCode.NOT_IMPLEMENTED
    assert session.sent == []


@pytest.mark.asyncio
async def test_unified_start_turn_points_at_a_skill_the_conversation_already_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare an adapter that has already run one ``/code-review`` turn.

    Do invoke the same skill again.

    Assert the second turn carries a pointer rather than the body. Legacy
    collapses a repeat through ``build_skill_result(already_loaded=...)``;
    re-injecting instead pays for the whole body on every invocation in a
    context that already holds a verbatim copy of it.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)

    for _ in range(2):
        await adapter.start_turn(
            TurnStartParams(
                session_id=session.session_id,
                message=[TextContentBlock(text="/code-review")],
            )
        )

    assert "Read the diff twice." in session.sent[0].message[-1].text
    assert session.sent[1].message[-1].text == already_loaded_message("code-review")


@pytest.mark.asyncio
async def test_unified_start_turn_leaves_an_unknown_slash_command_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare an adapter over a workspace skill.

    Do start a turn whose prompt is a slash command that is not a skill.

    Assert nothing is appended. ``/clear`` and friends never reach a backend,
    but a typo has to arrive at the model as the text the user typed rather
    than silently picking up some other skill's instructions.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)

    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id,
            message=[TextContentBlock(text="/code-revue please")],
        )
    )

    assert [block.text for block in session.sent[-1].message] == ["/code-revue please"]


@pytest.mark.asyncio
async def test_unified_steer_and_inject_honour_the_invoked_skill_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare an adapter over a workspace skill.

    Do steer with the flag off and inject context with it on.

    Assert only the caller that asked for it gets the body. The flag is how a
    client distinguishes a prompt the user typed from one it is replaying, and
    a replay that re-expands its own slash command duplicates the skill.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)

    await adapter.steer_turn(
        TurnSteerParams(
            session_id=session.session_id,
            expected_turn_id="turn-1",
            message=[TextContentBlock(text="/code-review")],
            inject_invoked_skill=False,
        )
    )
    await adapter.inject_context(
        ContextInjectParams(
            session_id=session.session_id,
            input=[TextContentBlock(text="/code-review")],
            inject_invoked_skill=True,
        )
    )

    assert [block.text for block in session.sent[0].message] == ["/code-review"]
    assert "Read the diff twice." in session.sent[1].input[-1].text


@pytest.mark.asyncio
async def test_unified_start_turn_does_not_expand_mentions_inside_a_skill_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a skill whose body documents the ``@file`` syntax.

    Do start a turn invoking it alongside a mention the user typed.

    Assert only the user's file is inlined. The skill body is appended after
    expansion, not scanned: a skill that merely *mentions* a path would
    otherwise silently inline it, and one naming a path outside the workspace
    would fail the turn outright.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    (tmp_path / "notes.md").write_text("user file", encoding="utf-8")
    (tmp_path / "secret.md").write_text("skill file", encoding="utf-8")
    _write_workspace_skill(tmp_path, "mention-doc", "Write @secret.md to name a file.")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)

    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id,
            message=[TextContentBlock(text="/mention-doc read @notes.md")],
        )
    )

    blocks = session.sent[-1].message
    resources = [
        block.resource.uri
        for block in blocks
        if isinstance(block, ResourceContentBlock)
    ]
    assert [Path(uri).name for uri in resources] == ["notes.md"]
    assert "Write @secret.md to name a file." in blocks[-1].text


@pytest.mark.asyncio
async def test_unified_inject_context_does_not_expand_mentions_inside_a_skill_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a skill whose body documents the ``@file`` syntax.

    Do inject it as context alongside a mention the caller wrote.

    Assert only the caller's file is inlined. ``inject_context`` runs the same
    two steps as a turn and has the same ordering to get right, so a client
    replaying a slash command through it must not inherit the skill's mentions.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    (tmp_path / "notes.md").write_text("caller file", encoding="utf-8")
    (tmp_path / "secret.md").write_text("skill file", encoding="utf-8")
    _write_workspace_skill(tmp_path, "mention-doc", "Write @secret.md to name a file.")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)

    await adapter.inject_context(
        ContextInjectParams(
            session_id=session.session_id,
            input=[TextContentBlock(text="/mention-doc read @notes.md")],
            inject_invoked_skill=True,
        )
    )

    blocks = session.sent[-1].input
    resources = [
        block.resource.uri
        for block in blocks
        if isinstance(block, ResourceContentBlock)
    ]
    assert [Path(uri).name for uri in resources] == ["notes.md"]
    assert "Write @secret.md to name a file." in blocks[-1].text


@pytest.mark.asyncio
async def test_unified_session_keeps_the_cwd_among_its_workspace_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare session options carrying one ``--add-dir`` root beside the cwd.

    Do build the session context and derive its adapter config.

    Assert the cwd leads the root set. The harness reads a non-empty set as the
    complete one, so an added directory that replaced the cwd would leave the
    file tools unable to read the project the session was started in.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    process = HarnessProcess(experimental_harness=True)

    context = await process.build_unified_session_context(
        SessionOptions(
            cwd=str(workspace), trust_workspace=True, workspace_roots=[str(downloads)]
        )
    )

    derivation = context.derive(UnifiedSessionSettings())
    assert derivation.adapter_config.workspace.roots == (workspace, downloads)


@pytest.mark.asyncio
async def test_unified_start_turn_inlines_a_mention_from_an_added_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a session whose workspace roots add a directory beside the cwd.

    Do start a turn mentioning a file in the cwd and one in the added root.

    Assert both are inlined. The file tools resolve against the same root set,
    so a session that refuses to attach a path ``read_file`` would happily read
    holds two contradictory definitions of its own workspace.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (workspace / "notes.md").write_text("user file", encoding="utf-8")
    (downloads / "build.log").write_text("vitest failed", encoding="utf-8")
    session = _RecordingSession()
    adapter = await _skill_adapter(workspace, session, workspace_roots=[downloads])

    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id,
            message=[
                TextContentBlock(
                    text=f"compare @notes.md with @{downloads / 'build.log'}"
                )
            ],
        )
    )

    blocks = session.sent[-1].message
    resources = [
        block.resource.uri
        for block in blocks
        if isinstance(block, ResourceContentBlock)
    ]
    assert sorted(Path(uri).name for uri in resources) == ["build.log", "notes.md"]


@pytest.mark.asyncio
async def test_unified_start_turn_keeps_an_unreachable_mention_as_plain_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a file outside every workspace root of the session.

    Do start a turn mentioning it alongside a file inside the workspace.

    Assert the turn is sent with only the reachable file inlined. Rejecting the
    request instead would discard a prompt the user had already composed over
    one path they typed themselves.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.md").write_text("user file", encoding="utf-8")
    outside = tmp_path / "build.log"
    outside.write_text("vitest failed", encoding="utf-8")
    session = _RecordingSession()
    adapter = await _skill_adapter(workspace, session)

    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id,
            message=[TextContentBlock(text=f"compare @notes.md with @{outside}")],
        )
    )

    blocks = session.sent[-1].message
    resources = [
        block.resource.uri
        for block in blocks
        if isinstance(block, ResourceContentBlock)
    ]
    assert [Path(uri).name for uri in resources] == ["notes.md"]
    assert str(outside) in blocks[0].text


@pytest.mark.asyncio
async def test_unified_runtime_reports_a_skill_it_could_not_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a workspace ``SKILL.md`` whose frontmatter is missing a field.

    Do derive a runtime configuration.

    Assert the runtime snapshot names the file. Discovery drops a skill it
    cannot parse, so without the issue reaching the snapshot the only signal
    the author gets is their skill quietly never appearing.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    broken = tmp_path / ".vibe" / "skills" / "half-written" / "SKILL.md"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("---\nname: half-written\n---\n\nBody.\n", encoding="utf-8")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), trust_workspace=True)
    )

    derivation = await asyncio.to_thread(context.derive, UnifiedSessionSettings())

    assert [issue.file for issue in derivation.runtime.issues] == [str(broken)]
    assert "half-written" not in {
        skill.name for skill in derivation.core_config.capabilities.skills
    }


@pytest.mark.asyncio
async def test_unified_runtime_config_withholds_skills_when_the_skill_tool_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a workspace skill and a config that disables the skill tool.

    Do derive.

    Assert the model-visible catalogue and payloads are empty while the client
    catalogue and slash-invocation payloads retain the skill.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings
    from vibe.core.config.patch import AddOperationPatch

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    _write_workspace_skill(tmp_path, "code-review", "Read the diff twice.")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), trust_workspace=True)
    )

    failures = await context.config_orchestrator.apply_patch(
        [AddOperationPatch(path="/disabled_tools", value=["skill"])], reason="test"
    )
    derivation = context.derive(UnifiedSessionSettings())

    assert failures == []
    assert derivation.core_config.capabilities.skills == []
    assert derivation.adapter_config.tool_modes["skill.read"] == "deny"
    # A disabled tool is not a deleted skill: the client still lists it and
    # ``/skill-name`` still has a body to inject, exactly as on the legacy loop.
    assert "code-review" in {skill.name for skill in derivation.runtime.skills}
    assert "code-review" in derivation.skill_payloads
    assert "code-review" not in derivation.adapter_config.skills


@pytest.mark.asyncio
async def test_unified_harness_lists_its_skills() -> None:
    """Prepare a Unified session.

    Do ask for the runtime and the skill list.

    Assert both report the same catalogue. ``skills/list`` used to be
    unroutable, so the CLI could not resolve ``/skill-name`` at all.
    """
    client, server = _connect_harness_host()

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        listed = SkillsListResponse.model_validate(
            await client.request(
                "skills/list", SkillsListParams(session_id=started.state.session.id)
            )
        )
        runtime = RuntimeReadResponse.model_validate(
            await client.request(
                "runtime/read", RuntimeReadParams(session_id=started.state.session.id)
            )
        )
    finally:
        await server.close()

    names = {skill.name for skill in listed.skills}
    assert "vibe" in names
    assert {skill.name for skill in runtime.runtime.skills} == names
    assert all(skill.prompt for skill in listed.skills)


@pytest.mark.asyncio
async def test_unified_harness_skills_installed() -> None:
    """``skills/installed`` must be routable on the Unified Harness backend.

    The method used to fall through to ``method_not_found`` because only
    ``skills/list`` had a case in ``dispatch_extension``.
    """
    client, server = _connect_harness_host()

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        installed = SkillsInstalledResponse.model_validate(
            await client.request(
                "skills/installed",
                SkillsInstalledParams(session_id=started.state.session.id),
            )
        )
    finally:
        await server.close()

    names = {skill.name for skill in installed.skills}
    # ``skills/installed`` reports registry pins and local skills, not builtins,
    # so it may be empty — the point is that the method is routable.
    assert isinstance(names, set)


async def _unified_adapter_with_real_context(
    tmp_path: Path,
    session: object,
    *,
    auto_approve: bool = False,
    deferred_turns: DeferredTurnStartCapability | None = None,
    services: object | None = None,
) -> tuple[Any, Any]:
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )

    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), agent="ask", auto_approve=auto_approve)
    )
    derivation = context.derive(UnifiedSessionSettings())
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session),
        context,
        derivation,
        deferred_turns=deferred_turns,
        services=cast(Any, services),
    )
    return adapter, derivation


@pytest.mark.asyncio
async def test_switching_to_an_agent_before_the_first_turn_carries_its_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A project agent names a prompt, and no turn has run yet.
    *Do*: Switch to it the way Shift+Tab does.
    *Assert*: The prompt reaches the session, so the Core that is created for
    the first turn is built with it. Core is handed its instructions at
    creation and holds them for life, so a push that leaves them out is the
    difference between the agent working and the agent being a label.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server.protocol import AgentSwitchParams
    from vibe.core.trusted_folders import trusted_folders_manager

    agents_dir = tmp_path / ".vibe" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "duck.toml").write_text(
        'description = "Quacks"\nsystem_prompt_id = "duck"\n', encoding="utf-8"
    )
    prompts_dir = tmp_path / ".vibe" / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (prompts_dir / "duck.md").write_text("You are a duck.", encoding="utf-8")
    trusted_folders_manager.trust_for_session(tmp_path)
    # A project prompt resolves against the process working directory, which for
    # a real session is the directory Vibe was launched in.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter, _ = await _unified_adapter_with_real_context(tmp_path, session)

    # Do
    result = await adapter.switch_agent(
        AgentSwitchParams(session_id=_RecordingSession.session_id, agent_name="duck")
    )

    # Assert
    assert result.response.runtime.active_agent.name == "duck"
    assert session.system_instructions
    assert (session.system_instructions[-1] or "").startswith("You are a duck.")


@pytest.mark.asyncio
async def test_unified_agent_switch_to_auto_approve_bypasses_tool_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shift+Tab into auto-approve has to reach the Runtime's approval policy.

    The backend used to reject ``session/agent/update`` outright, so the CLI
    showed the new mode while every tool call still asked for approval.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server.protocol import AgentSwitchParams

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter, derivation = await _unified_adapter_with_real_context(tmp_path, session)

    assert derivation.adapter_config.bypass_approval is False
    assert derivation.adapter_config.tool_modes["file_system.bash"] == "ask"

    result = await adapter.switch_agent(
        AgentSwitchParams(
            session_id=_RecordingSession.session_id, agent_name="auto-approve"
        )
    )

    applied = cast(Any, session.applied[-1])
    assert result.response.runtime.active_agent.name == "auto-approve"
    assert applied.bypass_approval is True
    assert applied.tool_modes["file_system.bash"] == "allow"
    assert applied.tool_modes["file_system.write_file"] == "allow"


@pytest.mark.asyncio
async def test_unified_agent_switch_away_from_auto_approve_restores_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cycling past auto-approve must not leave the bypass latched on."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server.protocol import AgentSwitchParams

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter, _ = await _unified_adapter_with_real_context(tmp_path, session)

    await adapter.switch_agent(
        AgentSwitchParams(
            session_id=_RecordingSession.session_id, agent_name="auto-approve"
        )
    )
    result = await adapter.switch_agent(
        AgentSwitchParams(session_id=_RecordingSession.session_id, agent_name="ask")
    )

    applied = cast(Any, session.applied[-1])
    assert result.response.runtime.active_agent.name == "ask"
    assert applied.bypass_approval is False
    assert applied.tool_modes["file_system.bash"] == "ask"


@pytest.mark.asyncio
async def test_unified_agent_switch_applies_while_a_turn_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shortcut is pressed mid-turn, and the legacy backend switches then too.

    The local adapter reads its approval policy per tool action, so the new
    policy lands on the running turn's next tool call.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server.protocol import AgentSwitchParams

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    session.active_turn_id = "turn-1"
    adapter, _ = await _unified_adapter_with_real_context(tmp_path, session)

    result = await adapter.switch_agent(
        AgentSwitchParams(
            session_id=_RecordingSession.session_id, agent_name="auto-approve"
        )
    )

    assert result.response.runtime.active_agent.name == "auto-approve"
    assert cast(Any, session.applied[-1]).bypass_approval is True


@pytest.mark.asyncio
async def test_unified_agent_switch_mid_turn_defers_only_the_core_half(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half a running turn cannot take waits for the next one, and lands there.

    Core reads its settings when a turn starts, so the mid-turn switch moves
    the approval policy alone and leaves the settings for later. That is only
    safe because it is not a drop: the next ``start_turn`` flushes what was
    held, so both halves end up on the agent the user asked for.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server.protocol import AgentSwitchParams, TurnStartParams

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    session.active_turn_id = "turn-1"
    adapter, _ = await _unified_adapter_with_real_context(tmp_path, session)

    switched = await adapter.switch_agent(
        AgentSwitchParams(
            session_id=_RecordingSession.session_id, agent_name="auto-approve"
        )
    )

    # The approval policy landed, but the agent has not taken over, and the
    # response says so rather than reading as a completed switch.
    assert switched.response.applied is False
    assert cast(Any, session.applied[-1]).bypass_approval is True
    assert session.settings == []

    session.active_turn_id = None
    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id, message=[TextContentBlock(text="carry on")]
        )
    )

    assert len(session.settings) == 1
    assert cast(Any, session.applied[-1]).bypass_approval is True

    landed = await adapter.switch_agent(
        AgentSwitchParams(session_id=_RecordingSession.session_id, agent_name="ask")
    )

    assert landed.response.applied is True


@pytest.mark.asyncio
async def test_unified_agent_switch_mid_turn_holds_the_model_for_the_running_turn(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the approval policy jumps the turn boundary -- the model waits.

    ``bypass_approval`` and ``tool_modes`` are read per tool action, so moving
    them mid-turn is the whole point of the switch. ``active_model`` is not
    like that: the adapter serves the turn's completions with it while Core
    keeps the settings it read at turn start, so pushing it early would put
    the two on different models for the rest of the turn.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server.protocol import AgentSwitchParams, TurnStartParams

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    # ``lean`` is the built-in profile that repoints ``active_model``, and it
    # only becomes switchable once installed.
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["installed_agents"] = ["ask", "lean"]
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")

    session = _RecordingSession()
    session.active_turn_id = "turn-1"
    adapter, derivation = await _unified_adapter_with_real_context(tmp_path, session)
    started_on = derivation.adapter_config.active_model

    await adapter.switch_agent(
        AgentSwitchParams(session_id=_RecordingSession.session_id, agent_name="lean")
    )

    assert cast(Any, session.applied[-1]).active_model == started_on

    session.active_turn_id = None
    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id, message=[TextContentBlock(text="carry on")]
        )
    )

    # Next turn, the model moves with the settings Core reads alongside it.
    assert cast(Any, session.applied[-1]).active_model != started_on


@pytest.mark.asyncio
async def test_unified_agent_switch_rejects_an_unknown_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server.protocol import AgentSwitchParams

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter, _ = await _unified_adapter_with_real_context(tmp_path, session)

    with pytest.raises(SessionBackendError) as excinfo:
        await adapter.switch_agent(
            AgentSwitchParams(
                session_id=_RecordingSession.session_id, agent_name="nope"
            )
        )

    assert excinfo.value.code is ProtocolErrorCode.INVALID_PARAMS
    assert session.applied == []


@pytest.mark.asyncio
async def test_unified_agent_switch_to_plan_denies_editing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan advertises itself as read-only, so the Runtime has to stop editing.

    Plan's editing tools are ``never`` plus an allowlist for its own plan file, so
    their mode is ``ask`` -- ``deny`` would short-circuit the resolver the
    allowlist lives in -- and the refusal is the resolver's to make per call.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server.protocol import AgentSwitchParams

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter, _ = await _unified_adapter_with_real_context(tmp_path, session)

    await adapter.switch_agent(
        AgentSwitchParams(session_id=_RecordingSession.session_id, agent_name="plan")
    )
    planning = cast(Any, session.applied[-1]).tool_modes
    permissions = adapter._context.permissions

    workspace_write = await permissions.resolve(
        "file_system.write_file", {"path": str(tmp_path / "src.py"), "content": "x"}
    )
    plan_write = await permissions.resolve(
        "file_system.write_file",
        {"path": str(PLANS_DIR.path / "plan.md"), "content": "x"},
    )

    assert planning["file_system.write_file"] == "ask"
    assert planning["file_system.search_replace"] == "ask"
    assert workspace_write.decision == "deny"
    assert plan_write.decision == "allow"


@pytest.mark.asyncio
async def test_unified_agent_switch_keeps_the_mcp_and_connector_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-deriving must not blank the banner the mixins layered onto the snapshot.

    ``build_unified_runtime_snapshot`` always emits an empty ``mcp``/``connectors``;
    the live values arrive later by projection. Replacing the snapshot wholesale
    dropped them, so the server and connector counts fell to ``0/0`` after every
    Shift+Tab until an unrelated connector event happened to re-project.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server.protocol import AgentSwitchParams

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter, _ = await _unified_adapter_with_real_context(tmp_path, session)
    adapter.update_mcp_projection(
        MCPState(
            sources=[
                MCPSourceSummary(
                    name="docs",
                    kind=MCPSourceKind.SERVER,
                    transport="stdio",
                    status=MCPSourceStatus.CONNECTED,
                )
            ]
        )
    )
    adapter._runtime = adapter._runtime.model_copy(
        update={"connectors": ConnectorCounts(connected=2, total=3)}
    )

    result = await adapter.switch_agent(
        AgentSwitchParams(session_id=_RecordingSession.session_id, agent_name="plan")
    )

    runtime = result.response.runtime
    assert [source.name for source in runtime.mcp.sources] == ["docs"]
    assert runtime.connectors == ConnectorCounts(connected=2, total=3)


@pytest.mark.asyncio
async def test_unified_agent_switch_restores_the_profile_when_deriving_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-applied switch must not strand the session on an agent it never ran.

    ``switch_profile`` mutates the orchestrator before anything can fail, so a
    failing ``derive`` used to leave the new agent layer installed while the
    Runtime kept the old policy -- and raised an untyped error past the adapter.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from dataclasses import replace

    from vibe.app_server.protocol import AgentSwitchParams

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter, _ = await _unified_adapter_with_real_context(tmp_path, session)
    working = adapter._context.derive
    calls = {"count": 0}

    def derive_once_then_fail(settings: Any) -> Any:
        calls["count"] += 1
        # The restore has to be able to re-derive, so only the switch itself fails.
        if calls["count"] == 1:
            raise RuntimeError("provider is unreachable")
        return working(settings)

    adapter._context = replace(adapter._context, derive=derive_once_then_fail)

    with pytest.raises(SessionBackendError) as excinfo:
        await adapter.switch_agent(
            AgentSwitchParams(
                session_id=_RecordingSession.session_id, agent_name="plan"
            )
        )

    assert excinfo.value.code is ProtocolErrorCode.INTERNAL_ERROR
    assert adapter._context.agents.active_profile.name == "ask"
    assert cast(Any, session.applied[-1]).tool_modes["file_system.write_file"] == "ask"


@pytest.mark.asyncio
async def test_first_turn_pin_reports_the_model_pinned_without_rederiving(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first turn pins the model the session already runs.

    That used to re-derive, rediscovering every tool and skill from disk for a
    runtime identical to the one session/start derived, save for the pinned flag
    the client's ``/model`` menu reads.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from dataclasses import replace

    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    del config["active_model"]
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    adapter, _ = await _unified_adapter_with_real_context(tmp_path, _RecordingSession())
    derives: list[Any] = []
    adapter._context = replace(adapter._context, derive=derives.append)
    assert adapter._runtime.config.active_model_pinned is False

    assert await adapter._pin_session_model_choice() is True

    assert derives == []
    assert adapter._runtime.config.active_model_pinned is True
    assert await adapter._pin_session_model_choice() is False


@pytest.mark.asyncio
async def test_first_turn_pin_rederives_when_its_reload_brings_in_a_config_edit(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Writing the model pin reloads every config layer from disk.

    An edit made since session/start lands in the config then, and nothing else
    re-derives for it, so the pin has to.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from dataclasses import replace

    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    del config["active_model"]
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    adapter, _ = await _unified_adapter_with_real_context(tmp_path, _RecordingSession())
    derive = adapter._context.derive
    derives: list[Any] = []

    def recording_derive(settings: Any) -> Any:
        derives.append(settings)
        return derive(settings)

    adapter._context = replace(adapter._context, derive=recording_derive)
    config_file.write_text(
        tomli_w.dumps({**config, "theme": "probe-theme"}), encoding="utf-8"
    )

    assert await adapter._pin_session_model_choice() is True

    assert len(derives) == 1
    assert adapter._runtime.config.theme == "probe-theme"
    assert adapter._runtime.config.active_model_pinned is True


def _always(_tool_name: str) -> ToolPermission:
    return ToolPermission.ALWAYS


def _no_allowlist(_tool_name: str) -> tuple[str, ...]:
    return ()


@pytest.mark.parametrize(
    ("permission", "expected_mode"),
    [("always", "ask"), ("ask", "ask"), ("never", "deny")],
)
def test_unified_builtin_modes_follow_the_configured_tool_permission(
    permission: str, expected_mode: str
) -> None:
    """A profile's per-tool ``permission`` is what makes plan and accept-edits real.

    The mapping used to read only the catalogue and the global bypass, so
    ``plan``'s ``never`` on ``write_file`` came out as ``ask`` -- the Runtime
    offered to edit files in a mode that advertises itself as read-only. Plan's
    own ``write_file`` does land on ``ask`` again today, but on the strength of
    its allowlist rather than in spite of its ``never``; the resolver refuses
    every path the list does not cover, which is the case below with no list.

    ``always`` stops at ``ask`` rather than ``allow``: the mode is the only gate
    the Runtime checks, and ``allow`` retires the resolver along with every rule
    the tool applies to the call itself. What ``always`` means is settled one
    call at a time, by the resolver, which clears the ordinary ones without a
    prompt -- see ``test_unified_permissions``.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import ToolGate, _rust_tool_modes

    modes = _rust_tool_modes(
        {"write_file", "read_file"},
        lambda name: (
            ToolPermission(permission)
            if name == "write_file"
            else ToolPermission.ALWAYS
        ),
        _no_allowlist,
        gate=ToolGate.PROMPT,
    )

    assert modes["file_system.write_file"] == expected_mode
    assert modes["file_system.read_file"] == "ask"


def test_unified_builtin_modes_route_background_starts_through_the_resolver() -> None:
    """*Prepare*: A catalogue whose shell is configured ``always``.
    *Do*: Map it with no bypass.
    *Assert*: The shell builtin and ``process.start`` both ask. A background
    start is the shell's own question -- it runs the command the start carries,
    so it follows the shell into the resolver, whose denylist, allowlist, and
    per-call grants all apply to it.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import ToolGate, _rust_tool_modes

    modes = _rust_tool_modes(
        {"bash", "read_file"}, _always, _no_allowlist, gate=ToolGate.PROMPT
    )

    assert modes["file_system.bash"] == "ask"
    assert modes["process.start"] == "ask"


def test_unified_builtin_bypass_wins_over_a_never_permission() -> None:
    """``AgentLoop._should_execute_tool`` short-circuits on the bypass before it
    ever reads a permission, so ``--auto-approve`` has to outrank a ``never`` here
    too -- otherwise the two backends disagree about the same config.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import ToolGate, _rust_tool_modes

    modes = _rust_tool_modes(
        {"write_file"},
        lambda _name: ToolPermission.NEVER,
        _no_allowlist,
        gate=ToolGate.BYPASS,
    )

    assert modes["file_system.write_file"] == "allow"


def test_unified_file_builtin_never_with_an_allowlist_reaches_the_resolver() -> None:
    """``deny`` short-circuits the resolver the allowlist lives in, so ``never``
    plus an allowlist came out an absolute refusal -- which is how the built-in
    ``plan`` agent stopped being able to write its own plan file.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import ToolGate, _rust_tool_modes

    modes = _rust_tool_modes(
        {"write_file", "read_file"},
        lambda _name: ToolPermission.NEVER,
        lambda name: ("plans/**",) if name == "write_file" else (),
        gate=ToolGate.PROMPT,
    )

    assert modes["file_system.write_file"] == "ask"
    assert modes["file_system.read_file"] == "deny"


def test_unified_shell_builtin_never_with_an_allowlist_still_denies() -> None:
    """A shell's allowlist ships pre-populated and the config migration writes that
    default into most users' ``config.toml``, so lifting on it would silently
    un-disable a bash a user -- or the ``disabled_tools`` migration -- turned off.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import ToolGate, _rust_tool_modes

    modes = _rust_tool_modes(
        {"bash"},
        lambda _name: ToolPermission.NEVER,
        lambda _name: ("ls", "cat"),
        gate=ToolGate.PROMPT,
    )

    assert modes["file_system.bash"] == "deny"
    assert modes["process.start"] == "deny"


def test_unified_shell_builtin_takes_the_strictest_permission_it_stands_for() -> None:
    """One Rust builtin covers three shell names; the mode has to hold for each."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import ToolGate, _rust_tool_modes

    permissions = {"bash": ToolPermission.ALWAYS, "powershell": ToolPermission.NEVER}

    modes = _rust_tool_modes(
        {"bash", "powershell"},
        permissions.__getitem__,
        _no_allowlist,
        gate=ToolGate.PROMPT,
    )

    assert modes["file_system.bash"] == "deny"


@pytest.mark.parametrize(
    "shell_tool", ["bash", "powershell", "git_bash", "powershell_and_git_bash"]
)
def test_unified_shell_builtin_follows_the_shell_the_platform_offers(
    shell_tool: str,
) -> None:
    """The managed-shell rollout renames the shell tool per platform: on Windows
    the catalogue offers ``powershell``/``git_bash`` and never ``bash``. Keying
    the Runtime's shell builtin on the literal name ``bash`` denied every command
    there, while the model still saw the tool advertised.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import ToolGate, _rust_tool_modes

    available = set(shell_tool.split("_and_")) | {"read_file"}

    modes = _rust_tool_modes(available, _always, _no_allowlist, gate=ToolGate.BYPASS)

    assert modes["file_system.bash"] == "allow"
    assert modes["process.start"] == "allow"


def test_unified_shell_builtin_is_denied_when_no_shell_tool_is_available() -> None:
    """Disabling the shell in the layered config still has to stop the Runtime."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import ToolGate, _rust_tool_modes

    modes = _rust_tool_modes(
        {"read_file"}, _always, _no_allowlist, gate=ToolGate.BYPASS
    )

    assert modes["file_system.bash"] == "deny"
    assert modes["process.start"] == "deny"
    assert modes["file_system.read_file"] == "allow"


def test_agent_ceiling_narrows_the_catalogue_with_the_profile_globs() -> None:
    # Matched with ``name_matches``, so a profile restricts the child to the same set
    # ``ToolManager.available_tools`` would have given it.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import rust_agent_tool_ceiling

    ceiling = rust_agent_tool_ceiling(
        {"read_file", "write_file", "search_replace", "bash"},
        _always,
        {"enabled_tools": ["read_*", "bash"]},
    )

    # The ceiling encodes what the profile *permits*; the Runtime takes the stricter
    # of this and the parent's mode, so "allow" here lets an auto-approve parent bypass
    # while an ask parent still resolves.
    assert ceiling["file_system.read_file"] == "allow"
    assert ceiling["file_system.bash"] == "allow"
    assert ceiling["file_system.write_file"] == "deny"
    assert ceiling["file_system.search_replace"] == "deny"


def test_agent_ceiling_disabled_tools_win_over_enabled_tools() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import rust_agent_tool_ceiling

    ceiling = rust_agent_tool_ceiling(
        {"read_file", "write_file"},
        _always,
        {"enabled_tools": ["*_file"], "disabled_tools": ["write_file"]},
    )

    assert ceiling["file_system.read_file"] == "allow"
    assert ceiling["file_system.write_file"] == "deny"


def test_agent_ceiling_permits_a_declared_always_as_allow() -> None:
    # The ceiling encodes what the profile permits; "allow" does not retire the
    # resolver on its own — the Runtime takes the stricter of this and the parent's
    # mode, so an ask-mode parent still resolves and a bypass parent lets it through.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import rust_agent_tool_ceiling

    ceiling = rust_agent_tool_ceiling(
        {"read_file"},
        lambda _name: ToolPermission.NEVER,
        {"tools": {"read_file": {"permission": "always"}}},
    )

    assert ceiling["file_system.read_file"] == "allow"


def test_agent_ceiling_declared_never_denies_a_tool_the_session_allows() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import rust_agent_tool_ceiling

    ceiling = rust_agent_tool_ceiling(
        {"read_file", "write_file"},
        _always,
        {"tools": {"write_file": {"permission": "never"}}},
    )

    assert ceiling["file_system.read_file"] == "allow"
    assert ceiling["file_system.write_file"] == "deny"


def test_agent_ceiling_ignores_malformed_profile_entries() -> None:
    # Overrides come from plugin-authored TOML: one bad key must not cost a session
    # its plugins.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import rust_agent_tool_ceiling

    ceiling = rust_agent_tool_ceiling(
        {"read_file"},
        _always,
        {
            "enabled_tools": "read_file",
            "disabled_tools": [7],
            "tools": {"read_file": {"permission": "sometimes"}},
        },
    )

    assert ceiling["file_system.read_file"] == "allow"


def test_agent_ceiling_denies_a_builtin_no_vibe_tool_stands_behind() -> None:
    # The ceiling covers the Runtime's whole vocabulary: omitting a key would leave
    # the child at the parent's mode for it.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import rust_agent_tool_ceiling

    ceiling = rust_agent_tool_ceiling(
        {"read_file", "bash"}, _always, {"enabled_tools": ["read_file"]}
    )

    assert ceiling["file_system.bash"] == "deny"
    assert ceiling["process.start"] == "deny"
    assert ceiling["file_system.read_file"] == "allow"


def test_agent_ceiling_without_overrides_matches_a_bypass_catalogue() -> None:
    # With no profile overrides, the ceiling grants exactly what the catalogue
    # permits. This matches a BYPASS-gate derivation (no allow→ask lowering), not
    # a PROMPT one, because the ceiling encodes what the profile permits and the
    # parent's mode is the other input to the stricter-of-two comparison.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import (
        ToolGate,
        _rust_tool_modes,
        rust_agent_tool_ceiling,
    )

    available = {"read_file", "write_file", "bash"}

    assert rust_agent_tool_ceiling(available, _always, {}) == _rust_tool_modes(
        available, _always, _no_allowlist, gate=ToolGate.BYPASS
    )


def test_agent_ceiling_allows_always_tools_so_a_bypass_parent_can_skip_prompts() -> (
    None
):
    # Regression: the ceiling used to be computed with gate=PROMPT, which lowered
    # every ALWAYS tool to "ask". The Runtime takes the stricter of the ceiling and
    # the parent's mode, so even an auto-approve parent (all builtins "allow") was
    # beaten by the ceiling's "ask" and the subagent prompted. The ceiling now maps
    # ALWAYS directly to "allow"; the parent's mode is what lowers it to "ask".
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import (
        ToolGate,
        _rust_tool_modes,
        rust_agent_tool_ceiling,
    )

    available = {"read_file", "bash"}

    ceiling = rust_agent_tool_ceiling(available, _always, {})
    bypass_modes = _rust_tool_modes(
        available, _always, _no_allowlist, gate=ToolGate.BYPASS
    )

    # The ceiling and a bypass-mode parent agree: everything the profile permits
    # is "allow", so the subagent runs without prompting.
    assert ceiling["file_system.read_file"] == "allow"
    assert ceiling["file_system.bash"] == "allow"
    assert ceiling == {k: v for k, v in bypass_modes.items() if v != "classify"}


def test_agent_ceiling_ask_permission_stays_ask_under_bypass_parent() -> None:
    # A tool with ASK permission in the profile still gets "ask" in the ceiling.
    # Under a bypass-mode parent ("allow"), the Runtime's stricter-of-two keeps
    # "ask", so the resolver still runs. The ceiling only turns ALWAYS into "allow";
    # it never upgrades ASK.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import rust_agent_tool_ceiling

    ceiling = rust_agent_tool_ceiling(
        {"read_file"}, lambda _name: ToolPermission.ASK, {}
    )

    assert ceiling["file_system.read_file"] == "ask"


@pytest.mark.parametrize(
    ("system_prompt_id", "expected_phrases"),
    [
        (
            "cli_2026-07_v2",
            ("Scale verification to the change.", "No fabricated URLs or paths."),
        ),
        ("cli_2026-08_v3", ("# Harness", "invoke it via the `skill` tool")),
    ],
)
def test_unified_system_instructions_use_the_selected_prompt_variant(
    tmp_path: Path, system_prompt_id: str, expected_phrases: tuple[str, ...]
) -> None:
    """*Prepare*: Vibe configuration contains a system-prompt experiment variant.
    *Do*: Resolve Unified system instructions through the Vibe composition seam.
    *Assert*: The SDK-owned instructions include that variant's product guidance.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.config.harness_files import HarnessFilesManager

    config = build_test_vibe_config(system_prompt_id=system_prompt_id)
    harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
        tmp_path
    )

    # Do
    instructions = runtime_module._build_unified_system_instructions(
        config, harness_files, cwd=tmp_path
    )

    # Assert
    assert all(phrase in instructions for phrase in expected_phrases)


@pytest.mark.parametrize("include_model_info", [True, False])
def test_unified_system_instructions_respect_include_model_info(
    tmp_path: Path, include_model_info: bool
) -> None:
    """*Prepare*: Model info is enabled or disabled in Vibe configuration.
    *Do*: Compose unified system instructions with agent-provided text.
    *Assert*: The active model alias is conditional and precedes agent extras.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.config import ModelConfig
    from vibe.core.config.harness_files import HarnessFilesManager

    config = build_test_vibe_config(
        include_model_info=include_model_info,
        models=[ModelConfig(name="test-model", provider="mistral", alias="test alias")],
        active_model="test-model",
    )
    harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
        tmp_path
    )

    instructions = runtime_module._build_unified_system_instructions(
        config, harness_files, cwd=tmp_path, base="Base prompt", extra="Agent extra"
    )

    model_info = "Your model name is: `test alias`"
    if include_model_info:
        assert model_info in instructions
        assert instructions.index(model_info) < instructions.index("Agent extra")
    else:
        assert model_info not in instructions


def test_unified_system_instructions_include_agents_md_docs(
    tmp_path: Path, config_dir: Path
) -> None:
    """*Prepare*: A user AGENTS.md and a project AGENTS.md exist on disk.
    *Do*: Resolve Unified system instructions through the Vibe composition seam.
    *Assert*: Both docs are appended after the SDK template, user before project.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.config.harness_files import HarnessFilesManager

    (config_dir / "AGENTS.md").write_text("# User doc", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# Project doc", encoding="utf-8")
    harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
        tmp_path
    )
    harness_files.trust_store.trust_for_session(tmp_path)
    # The test config builder opts out of project context by default; opt back
    # in and pin the variant so the ordering assertion is template-independent.
    config = build_test_vibe_config(
        include_project_context=True, system_prompt_id="cli"
    )

    # Do
    instructions = runtime_module._build_unified_system_instructions(
        config, harness_files, cwd=tmp_path
    )

    # Assert
    assert "# User doc" in instructions
    assert "## Project instructions (checked into the codebase)" in instructions
    assert f"Contents of {tmp_path}/AGENTS.md" in instructions
    assert "# Project doc" in instructions
    assert (
        instructions.index("## Instruction hierarchy")
        < instructions.index("## User instructions")
        < instructions.index("## Project instructions (checked into the codebase)")
    )


def test_unified_system_instructions_use_the_prompt_the_active_agent_names(
    tmp_path: Path, config_dir: Path
) -> None:
    """*Prepare*: The active agent profile names a prompt file of its own.
    *Do*: Resolve Unified system instructions through the Vibe composition seam.
    *Assert*: The file is the base text, in place of the SDK template, and the
    profile's own instructions follow it.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.agents import AgentSafety
    from vibe.core.agents.models import AgentProfile
    from vibe.core.config.harness_files import HarnessFilesManager

    prompts = config_dir / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "reviewing.md").write_text("Report, never repair.", encoding="utf-8")
    profile = AgentProfile(
        name="reviewer",
        display_name="Reviewer",
        description="Reviews a diff",
        safety=AgentSafety.SAFE,
        overrides={"system_prompt_id": "reviewing"},
        instructions="Answer in French.",
        source_path=tmp_path / "reviewer.toml",
    )
    harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
        tmp_path
    )
    config = build_test_vibe_config(system_prompt_id="reviewing")

    # Do
    base, issue = runtime_module._agent_profile_prompt(profile)
    instructions = runtime_module._build_unified_system_instructions(
        config, harness_files, cwd=tmp_path, base=base, extra=profile.instructions
    )

    # Assert
    assert issue is None
    assert instructions.startswith("Report, never repair.")
    assert "Answer in French." in instructions


def test_unified_system_instructions_keep_the_sdk_template_for_a_variant(
    tmp_path: Path,
) -> None:
    """*Prepare*: The active agent profile names no prompt of its own.
    *Do*: Resolve Unified system instructions through the Vibe composition seam.
    *Assert*: Nothing overrides the base text, so the experiment variant the
    config carries still reaches the model.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.agents.models import ACCEPT_EDITS

    # Do
    base, issue = runtime_module._agent_profile_prompt(ACCEPT_EDITS)

    # Assert
    assert base is None
    assert issue is None


def test_an_agent_naming_an_unreadable_prompt_is_reported_and_falls_back(
    tmp_path: Path,
) -> None:
    """*Prepare*: The active agent profile names a prompt file that is not there.
    *Do*: Resolve the prompt the profile asks for.
    *Assert*: No base text, and an issue naming the agent's own file, so the
    session runs on the default rather than failing to open.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.agents import AgentSafety
    from vibe.core.agents.models import AgentProfile

    path = tmp_path / "reviewer.toml"
    profile = AgentProfile(
        name="reviewer",
        display_name="Reviewer",
        description="Reviews a diff",
        safety=AgentSafety.SAFE,
        overrides={"system_prompt_id": "absent"},
        source_path=path,
    )

    # Do
    base, issue = runtime_module._agent_profile_prompt(profile)

    # Assert
    assert base is None
    assert issue is not None
    assert issue.file == str(path)
    assert "absent" in issue.message


@pytest.mark.asyncio
async def test_a_project_subagent_reaches_the_core_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A trusted project defines a subagent in ``.vibe/agents``.
    *Do*: Build a Unified session context and derive its runtime configuration.
    *Assert*: The Core is told the subagent exists, so the model can spawn it.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe._experimental_harness import create_experimental_harness_host
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings
    from vibe.core.trusted_folders import trusted_folders_manager

    agents_dir = tmp_path / ".vibe" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "reviewer.toml").write_text(
        'description = "Reviews a diff and reports what it found"\n'
        'agent_type = "subagent"\n'
        'enabled_tools = ["read_file", "grep"]\n',
        encoding="utf-8",
    )
    trusted_folders_manager.trust_for_session(tmp_path)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    # Do
    context = await HarnessProcess(
        experimental_harness=True
    ).build_unified_session_context(SessionOptions(cwd=str(tmp_path)))
    derivation = context.derive(UnifiedSessionSettings())

    # Assert
    advertised = derivation.core_config.capabilities.agent_types
    assert [definition.name for definition in advertised] == ["reviewer"]
    assert advertised[0].description == "Reviews a diff and reports what it found"

    # ...and survives the Host, which is what the model actually reads. Asserting
    # on the derivation alone passes while the Host drops every name on the way
    # in, leaving an agent that can be spawned by name and never offered.
    host = cast(Any, create_experimental_harness_host())
    host.configure_runtime(
        derivation.core_config, adapter_config=derivation.adapter_config
    )
    configured = host._runtime_config_template.capabilities.agent_types
    assert [definition.name for definition in configured] == ["reviewer"]


def test_unified_system_instructions_skip_agents_md_docs_when_context_is_disabled(
    tmp_path: Path, config_dir: Path
) -> None:
    """*Prepare*: AGENTS.md docs exist but include_project_context is disabled.
    *Do*: Resolve Unified system instructions through the Vibe composition seam.
    *Assert*: The instructions carry the template only, no docs section.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.config.harness_files import HarnessFilesManager

    (config_dir / "AGENTS.md").write_text("# User doc", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# Project doc", encoding="utf-8")
    harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
        tmp_path
    )
    harness_files.trust_store.trust_for_session(tmp_path)
    config = build_test_vibe_config(include_project_context=False)

    # Do
    instructions = runtime_module._build_unified_system_instructions(
        config, harness_files, cwd=tmp_path
    )

    # Assert
    assert "You are Mistral Vibe" in instructions
    assert "## User instructions" not in instructions
    assert "## Project instructions" not in instructions


def test_unified_system_instructions_include_git_context(
    tmp_path: Path, config_dir: Path
) -> None:
    """*Prepare*: The cwd is a git repository with a branch and commits.
    *Do*: Resolve Unified system instructions with include_project_context=True.
    *Assert*: The instructions include the current branch, main branch, and
    recent commits — matching the legacy backend's project-context block.
    """
    import subprocess

    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.config.harness_files import HarnessFilesManager

    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial commit"],
        cwd=tmp_path,
        check=True,
    )

    harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
        tmp_path
    )
    harness_files.trust_store.trust_for_session(tmp_path)
    config = build_test_vibe_config(
        include_project_context=True, system_prompt_id="cli"
    )

    instructions = runtime_module._build_unified_system_instructions(
        config, harness_files, cwd=tmp_path
    )

    assert "Current branch: main" in instructions
    assert "Main branch (you will usually use this for PRs): main" in instructions
    assert "initial commit" in instructions
    assert f"Absolute path: {tmp_path}" in instructions


def test_unified_system_instructions_include_the_commit_signature(
    tmp_path: Path,
) -> None:
    """*Prepare*: The include_commit_signature flag is on, then off.
    *Do*: Resolve Unified system instructions through the Vibe composition seam.
    *Assert*: The legacy signature section rides along when enabled and is
    omitted when disabled — matching the legacy backend's prompt.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.config.harness_files import HarnessFilesManager

    harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
        tmp_path
    )

    enabled = runtime_module._build_unified_system_instructions(
        build_test_vibe_config(include_commit_signature=True),
        harness_files,
        cwd=tmp_path,
    )
    disabled = runtime_module._build_unified_system_instructions(
        build_test_vibe_config(include_commit_signature=False),
        harness_files,
        cwd=tmp_path,
    )

    assert "Generated by Mistral Vibe." in enabled
    assert "Co-Authored-By: Mistral Vibe <vibe@mistral.ai>" in enabled
    assert "Co-Authored-By" not in disabled


# Adapter tests that install a plugin from the vibe_sdk fixtures live in
# test_unified_harness_plugins.py: the fixtures sit outside the vibe/ release
# tree, so those tests are omitted from the public tree and this file is not.


@pytest.mark.asyncio
async def test_unified_harness_prepares_a_text_prompt() -> None:
    client, server = _connect_harness_host()

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        response = WorkspacePromptPrepareResponse.model_validate(
            await client.request(
                "workspace/prompt/prepare",
                WorkspacePromptPrepareParams(
                    session_id=started.state.session.id, message="hello"
                ),
            )
        )
    finally:
        await server.close()

    assert response.prompt.display_text == "hello"
    assert response.prompt.prompt_text == "hello"
    assert response.prompt.images == []
    # Preparing a prompt no longer names the session: the agent loop generates
    # the title in the background once there is a transcript to summarize.
    assert response.prompt.auto_title is None


@pytest.mark.asyncio
async def test_unified_harness_enqueues_ephemeral_prompt_with_external_image(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    pasted_images = tmp_path / "vibe-pasted-images"
    pasted_images.mkdir()
    image_path = pasted_images / "pasted.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    session_root = tmp_path / "sessions"
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(session_root))
    )
    client, server = _connect_harness_host(config)

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request(
                "session/start",
                SessionStartParams(
                    agent_config=SessionOptions(cwd=str(workspace)),
                    kind=SessionKind.EPHEMERAL,
                ),
            )
        )
        session_id = started.state.session.id
        response: WorkspacePromptPrepareResponse = (
            WorkspacePromptPrepareResponse.model_validate(
                await client.request(
                    "workspace/prompt/prepare",
                    WorkspacePromptPrepareParams(
                        session_id=session_id, message=f"describe @{image_path}"
                    ),
                )
            )
        )
        image = response.prompt.images[0]
        assert isinstance(image.source, FileImageSource)
        snapshot_path = Path(image.source.path)
        assert snapshot_path.is_relative_to(
            session_root / "unified" / session_id / "attachments"
        )

        enqueued = TurnEnqueueResponse.model_validate(
            await client.request(
                "session/turn/enqueue",
                TurnEnqueueParams(
                    session_id=session_id,
                    entries=[
                        TurnUserInputEntry(
                            entry_id="user-1",
                            content=[
                                SessionTextContentBlock(
                                    text=response.prompt.prompt_text
                                ),
                                SessionImageContentBlock(
                                    uri=snapshot_path.as_uri(),
                                    media_type=image.mime_type,
                                    alt_text=image.alias,
                                ),
                            ],
                        )
                    ],
                ),
            )
        )
    finally:
        await server.close()

    assert enqueued.queue_item_id


@pytest.mark.asyncio
async def test_unified_harness_snapshots_mentioned_images_for_an_unpersisted_session(
    tmp_path: Path,
) -> None:
    """*Prepare*: An unpersisted Unified session and an image mention.
    *Do*: Prepare the prompt through the public app-server client.
    *Assert*: The image is copied into session attachments and removed on shutdown.
    """
    # Prepare
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    (tmp_path / "shot.png").write_bytes(image_bytes)
    config = build_test_vibe_config(
        active_model="local-llamacpp",
        models=[
            ModelConfig(name="devstral", provider="llamacpp", alias="local-llamacpp")
        ],
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path / "sessions")
        ),
    )
    assert config.get_active_model().supports_images is False
    client, server = _connect_harness_host(config)
    await client.initialize(ClientInfo(name="test", version="0"))
    await client.notify("initialized")
    started = SessionReadResponse.model_validate(
        await client.request(
            "session/start",
            SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path))),
        )
    )
    session_dir = (
        Path(config.session_logging.save_dir) / "unified" / started.state.session.id
    )

    try:
        # Do
        response: WorkspacePromptPrepareResponse = (
            WorkspacePromptPrepareResponse.model_validate(
                await client.request(
                    "workspace/prompt/prepare",
                    WorkspacePromptPrepareParams(
                        session_id=started.state.session.id, message="look at @shot.png"
                    ),
                )
            )
        )

        # Assert
        assert len(response.prompt.images) == 1
        image = response.prompt.images[0]
        assert image.alias == "shot.png"
        assert image.mime_type == "image/png"
        assert isinstance(image.source, FileImageSource)
        snapshot = Path(image.source.path)
        assert snapshot.parent == session_dir / "attachments"
        assert snapshot.read_bytes() == image_bytes
    finally:
        await server.close()

    assert not session_dir.exists()


@pytest.mark.asyncio
async def test_unified_turn_start_injects_mentioned_file_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    import vibe.app_server._unified_harness_backend_adapter as adapter_module

    mentioned_block = ResourceContentBlock(
        resource=UserTextResource(uri="file:///workspace/notes.md", text="hello world")
    )
    calls: list[tuple[str, Path]] = []

    async def fake_mentioned_file_blocks(
        text: str, *, base_dir: Path, workspace_roots: Sequence[Path] = ()
    ) -> list[ResourceContentBlock]:
        calls.append((text, base_dir))
        return [mentioned_block]

    monkeypatch.setattr(
        adapter_module,
        "mentioned_file_content_blocks_async",
        fake_mentioned_file_blocks,
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-1", ephemeral=True, cwd=None),
        str(tmp_path),
        str(tmp_path),
    )

    params = await adapter._with_mentioned_file_blocks(
        TurnStartParams(
            session_id="session-1", message=[TextContentBlock(text="read @notes.md")]
        )
    )

    assert calls == [("read @notes.md", tmp_path.resolve())]
    assert params.message == [TextContentBlock(text="read @notes.md"), mentioned_block]


@pytest.mark.asyncio
async def test_unified_context_inject_injects_mentioned_file_context(
    tmp_path: Path,
) -> None:
    (tmp_path / "notes.md").write_text("hello world")
    client, server = _connect_harness_host()

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request(
                "session/start",
                SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path))),
            )
        )
        response = ContextInjectResponse.model_validate(
            await client.request(
                "session/context/inject",
                ContextInjectParams(
                    session_id=started.state.session.id,
                    input=[TextContentBlock(text="read @notes.md")],
                    as_message=True,
                    client_user_message_id="context-1",
                ),
            )
        )
        read = SessionReadResponse.model_validate(
            await client.request(
                "session/read",
                SessionReadParams(
                    session_id=started.state.session.id, history=PageRequest(limit=10)
                ),
            )
        )
    finally:
        await server.close()

    assert len(response.entries) == 1
    entry = response.entries[0]
    assert isinstance(entry, PublicMessageEntry)
    assert entry.id == "context-1"
    assert entry.text == "read @notes.md"
    assert any(isinstance(block, ResourceContentBlock) for block in entry.content)
    assert read.state.history == response.entries


@pytest.mark.asyncio
async def test_unified_prompt_prepare_does_not_read_mentioned_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    import vibe.app_server._unified_harness_backend_adapter as adapter_module

    def fail_read(*_args: object, **_kwargs: object) -> list[object]:
        raise AssertionError("prepare should not read mentioned files")

    monkeypatch.setattr(
        adapter_module, "mentioned_file_content_blocks_async", fail_read
    )
    client, server = _connect_harness_host()

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        response = WorkspacePromptPrepareResponse.model_validate(
            await client.request(
                "workspace/prompt/prepare",
                WorkspacePromptPrepareParams(
                    session_id=started.state.session.id, message="read @notes.md"
                ),
            )
        )
    finally:
        await server.close()

    assert response.prompt.prompt_text == "read @notes.md"


@pytest.mark.asyncio
async def test_unified_harness_disables_feedback_prompt() -> None:
    client, server = _connect_harness_host()

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        response = FeedbackShouldShowResponse.model_validate(
            await client.request(
                "feedback/shouldShow",
                FeedbackShouldShowParams(
                    session_id=started.state.session.id, pending_user_messages=1
                ),
            )
        )
    finally:
        await server.close()

    assert not response.show


@pytest.mark.asyncio
async def test_unified_host_reads_the_live_public_event_cursor(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    host = _harness_backend_host(config)
    try:
        started = await host.start(SessionStartParams())
        backend = started.backend
        params = SessionReadParams(session_id=backend.session_id)
        subscription = await backend.subscribe(params)
        forwarded = []
        completed = asyncio.Event()

        async def consume() -> None:
            async for event in subscription.events:
                forwarded.append(event)
                if event.method == "turn/completed":
                    completed.set()

        consumer = asyncio.create_task(consume())
        try:
            turn = await backend.start_turn(
                TurnStartParams(
                    session_id=backend.session_id,
                    message=[TextContentBlock(text="hello")],
                )
            )
            if turn.after_response is not None:
                turn.after_response()
            accepted = await host.read(params)
            assert accepted.state.turns
            assert accepted.state.turns[-1].id == turn.response.turn.id
            await asyncio.wait_for(completed.wait(), timeout=3)
            read = await host.read(params)
            last_event_id = max(event.event_id or 0 for event in forwarded)
            assert read.last_event_id == last_event_id
            assert read.state.event_id == last_event_id
            assert read.state.turns
            assert read.state.turns[-1].status != "in_progress"
        finally:
            consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await consumer
    finally:
        await host.shutdown()


@pytest.mark.asyncio
async def test_unified_read_preserves_open_callbacks_outside_history(
    tmp_path: Path,
) -> None:
    from mistralai_vibe_local_harness.vibe._session import _approval_callback

    session = _RecordingSession()
    snapshot = (await session.read(None)).snapshot
    callback = _approval_callback(
        session_id=session.session_id,
        callback_id="approval-call-1",
        action=_approval_action("turn-1"),
        created_at=1,
        required_permissions=(),
        input_entry_id=None,
    )
    snapshot = snapshot.model_copy(
        update={
            "state": snapshot.state.model_copy(update={"active_callbacks": [callback]})
        }
    )

    async def read_snapshot(_params: object) -> object:
        return SimpleNamespace(snapshot=snapshot)

    session.read = read_snapshot
    adapter = _inert_adapter(session, str(tmp_path), str(tmp_path))
    response = await adapter.read(SessionReadParams(session_id=session.session_id))

    callbacks = [
        entry
        for entry in response.state.history or []
        if isinstance(entry, PublicCallbackEntry)
    ]
    assert len(callbacks) == 1
    assert callbacks[0].callback_id == "approval-call-1"
    assert callbacks[0].state.status == "open"
    assert [callback.callback_id for callback in response.state.active_callbacks] == [
        "approval-call-1"
    ]


def test_unified_state_update_carries_the_open_callbacks(tmp_path: Path) -> None:
    """*Prepare*: A Harness state event reporting one open approval callback.
    *Do*: Translate the event into the public session state.
    *Assert*: The public state lists the callback as active, so a snapshot sent
    while the callback is open does not tell the client it is gone.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        BlockedSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._session import _approval_callback

    callback = _approval_callback(
        session_id="session-root",
        callback_id="approval-call-1",
        action=_approval_action("turn-1"),
        created_at=1,
        required_permissions=(),
        input_entry_id=None,
    )
    state = HarnessPublicSessionState(
        session=HarnessPublicSession(
            id="session-root",
            status=BlockedSessionStatus(
                active_turn_id="turn-1",
                callback_id="approval-call-1",
                callback_kind="approval",
            ),
            created_at=1,
            updated_at=1,
        ),
        active_callbacks=[callback],
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None), str(tmp_path), str(tmp_path)
    )

    current, _watermark = adapter._updated_state(
        {
            "type": "session_state_updated",
            "sessionId": "session-root",
            "eventId": 1,
            "state": state.model_dump(mode="json", by_alias=True),
        },
        20,
        cwd=str(tmp_path),
        metadata=None,
        projections=adapter._history_projections,
    )

    assert [callback.callback_id for callback in current.active_callbacks] == [
        "approval-call-1"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("watermark", [2, 20])
async def test_unified_read_keeps_the_cursor_of_its_snapshot(
    tmp_path: Path, watermark: int
) -> None:
    """A read waits for its snapshot, without adopting later notifications' cursor."""
    session = _RecordingSession()
    snapshot = (await session.read(None)).snapshot.model_copy(
        update={"watermark": watermark}
    )
    read_started = asyncio.Event()

    async def read_snapshot(_params: object) -> object:
        read_started.set()
        return SimpleNamespace(snapshot=snapshot)

    session.read = read_snapshot
    adapter = _inert_adapter(session, str(tmp_path), str(tmp_path))
    adapter._events_subscribed = True
    adapter._event_id = 5
    adapter._observed_harness_watermark = watermark - 1
    reading = asyncio.create_task(
        adapter.read(SessionReadParams(session_id="session-1"))
    )
    await read_started.wait()
    assert not reading.done()

    # One Harness event fans out into three public notifications. Deliver the
    # following event as well, before the waiting read gets scheduled again.
    adapter._event_id = 8
    await adapter._mark_harness_event_observed(watermark)
    adapter._event_id = 11
    await adapter._mark_harness_event_observed(watermark + 1)

    response = await asyncio.wait_for(reading, timeout=1)
    assert response.state.event_id == response.last_event_id == 8
    assert adapter._event_id == 11
    assert not adapter._pending_reads


@pytest.mark.asyncio
@pytest.mark.parametrize("close_stream", [False, True])
async def test_unified_abandoned_read_releases_its_watermark(
    tmp_path: Path, close_stream: bool
) -> None:
    session = _RecordingSession()
    snapshot = (await session.read(None)).snapshot.model_copy(update={"watermark": 2})
    read_started = asyncio.Event()

    async def read_snapshot(_params: object) -> object:
        read_started.set()
        return SimpleNamespace(snapshot=snapshot)

    session.read = read_snapshot
    adapter = _inert_adapter(session, str(tmp_path), str(tmp_path))
    adapter._events_subscribed = True
    reading = asyncio.create_task(
        adapter.read(SessionReadParams(session_id="session-1"))
    )
    await read_started.wait()
    if close_stream:
        await adapter._finish_event_stream()
        with pytest.raises(SessionBackendError) as raised:
            await reading
        assert raised.value.code is ProtocolErrorCode.STALE_CURSOR
    else:
        reading.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reading
    await adapter._mark_harness_event_observed(2)
    assert not adapter._pending_reads


@pytest.mark.asyncio
async def test_unified_flush_events_does_not_wait_before_event_stream_starts(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._session import HarnessSessionSubscription

    class FakeHarnessSession:
        session_id = "session-1"
        cwd: str | None = None

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        def pin(self, _pin: SessionPin) -> str | None:
            return None

        async def read(self, _params: object) -> object:
            return type("ReadResult", (), {"snapshot": self._snapshot(1)})()

        async def subscribe(self, _params: object) -> HarnessSessionSubscription:
            async def events():
                if False:
                    yield {}

            return HarnessSessionSubscription(
                snapshot=self._snapshot(0), events=events()
            )

        def _snapshot(self, watermark: int) -> HarnessSessionSnapshot:
            return HarnessSessionSnapshot(
                state=HarnessPublicSessionState(
                    session=HarnessPublicSession(
                        id=self.session_id,
                        status=IdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                    ),
                    turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
                ),
                history_limit=1,
                watermark=watermark,
            )

    adapter = _inert_adapter(FakeHarnessSession(), str(tmp_path), str(tmp_path))

    subscription = await adapter.subscribe(SessionReadParams(session_id="session-1"))
    await asyncio.wait_for(adapter.flush_events(), timeout=0.1)
    await cast(Any, subscription.events).aclose()


@pytest.mark.asyncio
async def test_unified_flush_events_tracks_queue_event_watermarks(
    tmp_path: Path,
) -> None:
    """*Prepare*: A queue event whose watermark is newer than the subscription snapshot.
    *Do*: Consume the event while `flush_events` waits for the Harness watermark.
    *Assert*: The queue event advances the observed watermark before the stream closes.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        Event as HarnessEvent,
        IdleSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
        TurnQueueUpdatedEvent as HarnessTurnQueueUpdatedEvent,
    )
    from mistralai_vibe_local_harness.vibe._session import HarnessSessionSubscription

    class FakeHarnessSession:
        session_id = "session-1"
        cwd: str | None = None

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        def __init__(self) -> None:
            self.events_started = asyncio.Event()
            self.release_events = asyncio.Event()
            self.turn_queue = HarnessTurnQueue(items=[], paused=False, max_items=32)

        def pin(self, _pin: SessionPin) -> str | None:
            return None

        async def read(self, _params: object) -> object:
            return type("ReadResult", (), {"snapshot": self._snapshot(1)})()

        async def subscribe(self, _params: object) -> HarnessSessionSubscription:
            async def events() -> AsyncIterator[dict[str, Any]]:
                self.events_started.set()
                yield HarnessEvent(
                    event_id="1",
                    emitted_at=1,
                    session_id=self.session_id,
                    payload=HarnessTurnQueueUpdatedEvent(queue=self.turn_queue),
                ).model_dump(mode="json", by_alias=True)
                await self.release_events.wait()

            return HarnessSessionSubscription(
                snapshot=self._snapshot(0), events=events()
            )

        def _snapshot(self, watermark: int) -> HarnessSessionSnapshot:
            return HarnessSessionSnapshot(
                state=HarnessPublicSessionState(
                    session=HarnessPublicSession(
                        id=self.session_id,
                        status=IdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                    ),
                    turn_queue=self.turn_queue,
                ),
                history_limit=1,
                watermark=watermark,
            )

    fake_session = FakeHarnessSession()
    adapter = _inert_adapter(fake_session, str(tmp_path), str(tmp_path))
    subscription = await adapter.subscribe(SessionReadParams(session_id="session-1"))

    async def consume_events() -> None:
        async for _event in subscription.events:
            pass

    consumer = asyncio.create_task(consume_events())
    await asyncio.wait_for(fake_session.events_started.wait(), timeout=1)

    # Do
    await asyncio.wait_for(adapter.flush_events(), timeout=1)

    # Assert
    assert not consumer.done()
    fake_session.release_events.set()
    await asyncio.wait_for(consumer, timeout=1)


@pytest.mark.asyncio
async def test_unified_adapter_preserves_canonical_queue_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A fake Unified session exposing the canonical queue contract.
    *Do*: Enqueue and read through the Vibe adapter, then consume its queue event.
    *Assert*: The adapter preserves queue IDs and complete canonical entries.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    from mistralai_vibe_local_harness.session_protocol import (
        Event as HarnessEvent,
        IdleSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        QueuedTurn as HarnessQueuedTurn,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnContextInputEntry as HarnessTurnContextInputEntry,
        TurnEnqueueResponse as HarnessTurnEnqueueResponse,
        TurnQueue as HarnessTurnQueue,
        TurnQueueReadResponse as HarnessTurnQueueReadResponse,
        TurnQueueReplaceParams as HarnessTurnQueueReplaceParams,
        TurnQueueReplaceResponse as HarnessTurnQueueReplaceResponse,
        TurnQueueUpdatedEvent as HarnessTurnQueueUpdatedEvent,
    )
    from mistralai_vibe_local_harness.vibe._session import HarnessSessionSubscription

    class Result:
        def __init__(self, response: object) -> None:
            self.response = response
            self.after_response = None

    class FakeHarnessSession:
        session_id = "session-1"
        cwd: str | None = None

        def __init__(self) -> None:
            self._pins: dict[SessionPin, str | None] = {}
            self.received: object | None = None
            self.replaced: Any | None = None
            self.turn_queue = HarnessTurnQueue(items=[], paused=False, max_items=32)

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        def pin(self, pin: SessionPin) -> str | None:
            return self._pins.get(pin)

        async def persist_pin(self, pin: SessionPin, value: str) -> bool:
            if self._pins.get(pin) == value:
                return False
            self._pins[pin] = value
            return True

        async def enqueue_turn(self, params: Any) -> Result:
            self.received = params
            self.turn_queue = HarnessTurnQueue(
                items=[
                    HarnessQueuedTurn(
                        id="queue-1", created_at=2, entries=[*params.entries]
                    )
                ],
                paused=False,
                max_items=32,
            )
            return Result(HarnessTurnEnqueueResponse(queue_item_id="queue-1"))

        async def replace_queued_turn(self, params: Any) -> Result:
            self.replaced = params
            current = self.turn_queue.items[0]
            self.turn_queue = HarnessTurnQueue(
                items=[
                    current.model_copy(update={"entries": [*params.entries]}, deep=True)
                ],
                paused=False,
                max_items=32,
            )
            return Result(
                HarnessTurnQueueReplaceResponse(queue_item_id=params.queue_item_id)
            )

        async def read_turn_queue(self, _params: object) -> Result:
            return Result(HarnessTurnQueueReadResponse(queue=self.turn_queue))

        async def subscribe(self, _params: object) -> HarnessSessionSubscription:
            async def events():
                yield HarnessEvent(
                    event_id="1",
                    emitted_at=1,
                    session_id=self.session_id,
                    payload=HarnessTurnQueueUpdatedEvent(queue=self.turn_queue),
                ).model_dump(mode="json", by_alias=True)

            snapshot = HarnessSessionSnapshot(
                state=HarnessPublicSessionState(
                    session=HarnessPublicSession(
                        id=self.session_id,
                        status=IdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                    ),
                    turn_queue=self.turn_queue,
                ),
                history_limit=50,
                watermark=0,
            )
            return HarnessSessionSubscription(snapshot=snapshot, events=events())

    fake_session = FakeHarnessSession()
    adapter, _ = await _unified_adapter_with_real_context(tmp_path, fake_session)
    subscription = await adapter.subscribe(SessionReadParams(session_id="session-1"))

    # Do
    display = UserDisplayContent(
        version="1", host="vibe", content=[{"type": "text", "text": "shown prompt"}]
    )
    enqueued = await adapter.enqueue_turn(
        TurnEnqueueParams(
            session_id="session-1",
            entries=[
                TurnContextInputEntry(
                    entry_id="context-1",
                    content=[SessionTextContentBlock(text="injected context")],
                ),
                TurnUserInputEntry(
                    entry_id="message-1",
                    content=[SessionTextContentBlock(text="hello")],
                    annotations=MessageAnnotations.model_validate({
                        "vibe.userDisplayContent": display
                    }),
                ),
            ],
        )
    )
    replaced = await adapter.replace_queued_turn(
        TurnQueueReplaceParams(
            idempotency_key="edit-1",
            session_id="session-1",
            queue_item_id="queue-1",
            entries=[
                TurnContextInputEntry(
                    entry_id="context-2",
                    content=[SessionTextContentBlock(text="edited context")],
                )
            ],
        )
    )
    read = await adapter.read_turn_queue(TurnQueueReadParams(session_id="session-1"))
    event = await anext(subscription.events)

    # Assert
    assert enqueued.response == TurnEnqueueResponse(queue_item_id="queue-1")
    assert replaced.response == TurnQueueReplaceResponse(queue_item_id="queue-1")
    received = cast(Any, fake_session.received)
    assert isinstance(received.entries[0], HarnessTurnContextInputEntry)
    assert received.entries[0].entry_id == "context-1"
    assert received.entries[0].content[0].text == "injected context"
    assert received.entries[1].entry_id == "message-1"
    assert received.entries[1].content[0].text == "hello"
    assert received.entries[1].annotations.vibe_user_display_content.model_dump(
        mode="json"
    ) == display.model_dump(mode="json")
    assert fake_session.replaced is not None
    replaced_params = fake_session.replaced
    assert isinstance(replaced_params, HarnessTurnQueueReplaceParams)
    assert replaced_params.queue_item_id == "queue-1"
    assert cast(Any, replaced_params).entries[0].content[0].text == "edited context"
    assert read.response.queue.items[0].model_dump(mode="json") == {
        "id": "queue-1",
        "createdAt": 2,
        "entries": [
            {
                "role": "context",
                "entryId": "context-2",
                "content": [{"type": "text", "text": "edited context"}],
                "annotations": {},
            }
        ],
    }
    assert event.method == "turn/queueUpdated"
    assert cast(Any, event.params).queue == read.response.queue
    await cast(Any, subscription.events).aclose()


@pytest.mark.asyncio
async def test_unified_state_updates_preserve_the_subscription_history_window(
    tmp_path: Path,
) -> None:
    """A full-state update must not replay history omitted from the subscription."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )

    def message(index: int) -> JsonObject:
        return {
            "type": "message",
            "id": f"entry-{index}",
            "sessionId": "session-root",
            "createdAt": index,
            "updatedAt": index,
            "generationStatus": "completed",
            "role": "user",
            "content": [{"type": "text", "text": f"message {index}"}],
        }

    def state(entries: list[JsonObject], updated_at: int) -> HarnessPublicSessionState:
        return HarnessPublicSessionState(
            session=HarnessPublicSession(
                id="session-root",
                status=IdleSessionStatus(),
                created_at=1,
                updated_at=updated_at,
            ),
            history=LatestPublicHistoryPage(entries=entries),
            turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
        )

    entries = [message(index) for index in range(3)]
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
    )
    previous = adapter._read_response(
        HarnessSessionSnapshot(
            state=state(entries[-2:], 1), history_limit=2, watermark=1
        )
    ).state

    replayed, current, _ = await adapter._state_update_events(
        {
            "type": "session_state_updated",
            "sessionId": "session-root",
            "eventId": 2,
            "state": state(entries, 2).model_dump(mode="json", by_alias=True),
        },
        previous,
        2,
    )

    assert not [
        event for event in replayed if isinstance(event.event, HistoryEntryAdded)
    ]
    assert [entry.id for entry in current.history or []] == ["entry-1", "entry-2"]

    added, current, _ = await adapter._state_update_events(
        {
            "type": "session_state_updated",
            "sessionId": "session-root",
            "eventId": 3,
            "state": state([*entries, message(3)], 3).model_dump(
                mode="json", by_alias=True
            ),
        },
        current,
        2,
    )

    added_entries = [
        event.event.entry
        for event in added
        if isinstance(event.event, HistoryEntryAdded)
    ]
    assert [entry.id for entry in added_entries] == ["entry-3"]
    assert [entry.id for entry in current.history or []] == ["entry-2", "entry-3"]


@pytest.mark.asyncio
async def test_unified_state_updates_reproject_only_changed_entries(
    tmp_path: Path,
) -> None:
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )

    def message(entry_id: str, role: str, text: str, status: str) -> JsonObject:
        return {
            "type": "message",
            "id": entry_id,
            "sessionId": "session-root",
            "createdAt": 1,
            "updatedAt": 1,
            "generationStatus": status,
            "role": role,
            "content": [{"type": "text", "text": text}],
        }

    def state(entries: list[JsonObject], updated_at: int) -> HarnessPublicSessionState:
        return HarnessPublicSessionState(
            session=HarnessPublicSession(
                id="session-root",
                status=IdleSessionStatus(),
                created_at=1,
                updated_at=updated_at,
            ),
            history=LatestPublicHistoryPage(entries=entries),
            turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
        )

    def update(event_id: int, entries: list[JsonObject]) -> dict[str, object]:
        return {
            "type": "session_state_updated",
            "sessionId": "session-root",
            "eventId": event_id,
            "state": state(entries, event_id).model_dump(mode="json", by_alias=True),
        }

    question = message("question", "user", "Stream please", "completed")
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
    )
    previous = adapter._read_response(
        HarnessSessionSnapshot(
            state=state([question], 1), history_limit=10, watermark=1
        )
    ).state

    # Do
    _, first, _ = await adapter._state_update_events(
        update(2, [question, message("answer", "assistant", "Hello", "in_progress")]),
        previous,
        10,
    )
    answer = message("answer", "assistant", "Hello there", "in_progress")
    events, second, _ = await adapter._state_update_events(
        update(3, [question, answer]), first, 10
    )

    # Assert
    assert (second.history or [])[0] is (first.history or [])[0]
    assert [
        event.event.entry.id
        for event in events
        if isinstance(event.event, HistoryEntryUpdated)
    ] == ["answer"]
    full_read = adapter._read_response(
        HarnessSessionSnapshot(
            state=state([question, answer], 3), history_limit=10, watermark=3
        )
    ).state
    assert second.history == full_read.history


def test_history_projection_recognises_a_rebuilt_entry_by_identity() -> None:
    # Prepare
    from vibe.app_server._unified_harness_backend_adapter import _HistoryProjections

    comparisons = 0

    class CountingEntry(dict[str, object]):
        def __eq__(self, other: object) -> bool:
            nonlocal comparisons
            comparisons += 1
            return super().__eq__(other)

    entry: JsonObject = {
        "type": "message",
        "id": "question",
        "sessionId": "session-root",
        "createdAt": 1,
        "updatedAt": 1,
        "generationStatus": "completed",
        "role": "user",
        "content": [{"type": "text", "text": "Stream please"}],
    }
    projections = _HistoryProjections()
    first = projections.project([entry], ())
    rebuilt = CountingEntry(entry)

    # Do
    second = projections.project([rebuilt], ())
    third = projections.project([rebuilt], ())

    # Assert
    assert comparisons == 1
    assert third[0] is second[0] is first[0]


def test_a_large_output_receipt_counts_as_a_degraded_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Prepare
    from vibe.app_server import _unified_harness_backend_adapter as adapter

    outcomes: list[tuple[str, str]] = []
    monkeypatch.setattr(
        adapter,
        "add_unified_tool_projection",
        lambda *, category, outcome: outcomes.append((category, outcome)),
    )
    receipt = {
        "type": "success",
        "content": [
            {"type": "text", "text": "<truncated-output>\n{…\n</truncated-output>"}
        ],
    }
    entry: JsonObject = {
        "type": "effect",
        "id": "effect-1",
        "sessionId": "session-root",
        "createdAt": 1,
        "updatedAt": 1,
        "generationStatus": "completed",
        "relatedEntryId": None,
        "title": "file_system.read_file",
        "detail": {
            "kind": "tool",
            "toolName": "file_system.read_file",
            "input": {"path": "README.md"},
            "display": {"summary": "file_system.read_file", "statusText": "Running"},
        },
        "state": {
            "status": "completed",
            "output": receipt,
            "display": {"success": True, "message": "file_system.read_file"},
        },
    }

    # Do
    projected = adapter._project_history_entry(entry, ())

    # Assert
    assert isinstance(projected, PublicEffectEntry)
    assert projected.detail.kind == "file_read"
    assert outcomes == [("file_read", "degraded")]


def test_history_projection_reprojects_when_the_display_roots_change(
    tmp_path: Path,
) -> None:
    # Prepare
    from vibe.app_server._unified_harness_backend_adapter import _HistoryProjections

    path = tmp_path / "repo" / "README.md"
    entry: JsonObject = {
        "type": "effect",
        "id": "effect-1",
        "sessionId": "session-root",
        "createdAt": 1,
        "updatedAt": 1,
        "generationStatus": "in_progress",
        "relatedEntryId": None,
        "title": "file_system.read_file",
        "detail": {
            "kind": "tool",
            "toolName": "file_system.read_file",
            "input": {"path": str(path)},
            "display": {"summary": "file_system.read_file", "statusText": "Running"},
        },
        "state": {"status": "running", "outputText": ""},
    }
    projections = _HistoryProjections()

    # Do
    under_repo = projections.project([entry], (tmp_path / "repo",))
    under_tmp = projections.project([entry], (tmp_path,))

    # Assert
    messages = [
        entry.detail.display.message
        for entry in (*under_repo, *under_tmp)
        if isinstance(entry, PublicEffectEntry)
    ]
    assert messages == ["README.md", str(Path("repo", "README.md"))]


def test_unified_read_projects_provider_retry_and_accepts_older_harness_state(
    tmp_path: Path,
) -> None:
    """*Prepare*: New and pre-retry-field Harness session snapshots.
    *Do*: Translate both snapshots through the Vibe backend adapter.
    *Assert*: Retry state is projected when present and absent state remains compatible.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        TurnQueue as HarnessTurnQueue,
    )
    from vibe.app_server._unified_harness_backend_adapter import _read_response

    state = {
        "session": HarnessPublicSession(
            id="session-root", status=IdleSessionStatus(), created_at=1, updated_at=1
        ),
        "history": LatestPublicHistoryPage(entries=[]),
        "active_callbacks": [],
        "latest_turn": None,
        "turn_queue": HarnessTurnQueue(items=[], paused=False, max_items=32),
    }

    # Do
    retrying = _read_response(
        cast(
            Any,
            SimpleNamespace(
                state=SimpleNamespace(
                    **state,
                    retrying=SimpleNamespace(
                        turn_id="turn-1",
                        category="rate_limited",
                        detail="HTTP 429",
                        retry_at=1_700_000_000_000,
                        retry_attempt=3,
                    ),
                ),
                watermark=1,
            ),
        ),
        str(tmp_path),
    )
    older_harness = _read_response(
        cast(Any, SimpleNamespace(state=SimpleNamespace(**state), watermark=2)),
        str(tmp_path),
    )

    # Assert
    assert retrying.state.retrying == PublicRetryState(
        turn_id="turn-1",
        category=PublicRetryCategory.RATE_LIMITED,
        detail="HTTP 429",
        retry_at=1_700_000_000_000,
        retry_attempt=3,
    )
    assert older_harness.state.retrying is None


def test_unified_read_ignores_new_background_process_fields(tmp_path: Path) -> None:
    """*Prepare*: A Harness process summary with a field unknown to Vibe.
    *Do*: Translate its session snapshot through the backend adapter.
    *Assert*: Known process fields survive without rejecting the newer payload.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        TurnQueue as HarnessTurnQueue,
    )
    from vibe.app_server._unified_harness_backend_adapter import _read_response

    process = SimpleNamespace(
        model_dump=lambda **_kwargs: {
            "processId": "process-1",
            "command": "sleep 600",
            "status": "running",
            "exitCode": None,
            "createdAt": "2026-09-23T10:00:00Z",
            "pid": 1234,
        }
    )
    state = SimpleNamespace(
        session=HarnessPublicSession(
            id="session-root", status=IdleSessionStatus(), created_at=1, updated_at=1
        ),
        history=LatestPublicHistoryPage(entries=[]),
        active_callbacks=[],
        latest_turn=None,
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
        background_processes=[process],
    )

    # Do
    response = _read_response(
        cast(Any, SimpleNamespace(state=state, watermark=1)), str(tmp_path)
    )

    # Assert
    assert response.state.background_processes == [
        PublicBackgroundProcess(
            process_id="process-1",
            command="sleep 600",
            status="running",
            exit_code=None,
            created_at="2026-09-23T10:00:00Z",
        )
    ]


def test_unified_read_displays_paths_relative_to_the_cwd_and_workspace_roots(
    tmp_path: Path,
) -> None:
    """*Prepare*: Reads under the cwd, under an extra workspace root, and elsewhere.
    *Do*: Translate the session snapshot through the backend adapter.
    *Assert*: Each header names its file relative to the first root holding it.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        TurnQueue as HarnessTurnQueue,
    )
    from vibe.app_server._unified_harness_backend_adapter import _read_response

    cwd, extra = tmp_path / "repo", tmp_path / "docs"
    paths = [cwd / "README.md", extra / "guide.md", tmp_path / "other.md"]
    entries = [
        {
            "type": "effect",
            "id": f"effect-{index}",
            "sessionId": "session-root",
            "turnId": "turn-1",
            "createdAt": 1,
            "updatedAt": 1,
            "generationStatus": "in_progress",
            "relatedEntryId": None,
            "title": "file_system.read_file",
            "detail": {
                "kind": "tool",
                "toolName": "file_system.read_file",
                "input": {"path": str(path)},
                "display": {
                    "summary": "file_system.read_file",
                    "statusText": "Running file_system.read_file",
                },
            },
            "state": {"status": "running", "outputText": ""},
        }
        for index, path in enumerate(paths)
    ]
    state = SimpleNamespace(
        session=HarnessPublicSession(
            id="session-root", status=IdleSessionStatus(), created_at=1, updated_at=1
        ),
        history=LatestPublicHistoryPage(entries=entries),
        active_callbacks=[],
        latest_turn=None,
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )

    # Do
    response = _read_response(
        cast(Any, SimpleNamespace(state=state, watermark=1)),
        str(cwd),
        workspace_roots=(extra,),
    )

    # Assert
    history = response.state.history
    assert history is not None
    assert [
        entry.detail.display.message
        for entry in history
        if isinstance(entry, PublicEffectEntry)
    ] == ["README.md", "guide.md", str(tmp_path / "other.md")]


@pytest.mark.asyncio
async def test_unified_root_subscription_translates_child_session_events(
    tmp_path: Path,
) -> None:
    """*Prepare*: A root Harness subscription registers a child before its first update.
    *Do*: Consume the child update through the root adapter event stream.
    *Assert*: Root-owned summary events retain child identity and lifecycle state.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        InProgressPublicTurn,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        RunningSessionStatus,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._session import HarnessSessionSubscription

    root_id = "session-root"
    child_id = "session-child"
    root_state = HarnessPublicSessionState(
        session=HarnessPublicSession(
            id=root_id, status=IdleSessionStatus(), created_at=1, updated_at=1
        ),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    child_state = HarnessPublicSessionState(
        session=HarnessPublicSession(
            id=child_id,
            root_session_id=root_id,
            parent_session_id=root_id,
            status=IdleSessionStatus(),
            created_at=1,
            updated_at=1,
        ),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    running_child = child_state.model_copy(
        update={
            "session": child_state.session.model_copy(
                update={
                    "status": RunningSessionStatus(active_turn_id="child-turn-1"),
                    "updated_at": 2,
                }
            ),
            "latest_turn": InProgressPublicTurn(
                id="child-turn-1", session_id=child_id, started_at=2
            ),
        }
    )

    class FakeHarnessSession:
        session_id = root_id
        parent_session_id = None

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        def pin(self, _pin: SessionPin) -> str | None:
            return None

        async def subscribe(self, _params: object) -> HarnessSessionSubscription:
            async def events():
                yield {
                    "type": "child_session_registered",
                    "sessionId": child_id,
                    "eventId": 1,
                    "snapshot": HarnessSessionSnapshot(
                        state=child_state, history_limit=20, watermark=0
                    ).model_dump(mode="json", by_alias=True),
                }
                yield {
                    "type": "child_session_event",
                    "sessionId": child_id,
                    "eventId": 2,
                    "event": {
                        "type": "session_state_updated",
                        "sessionId": child_id,
                        "eventId": 1,
                        "state": running_child.model_dump(mode="json", by_alias=True),
                    },
                }

            return HarnessSessionSubscription(
                snapshot=HarnessSessionSnapshot(
                    state=root_state, history_limit=20, watermark=0
                ),
                events=events(),
            )

    adapter = _inert_adapter(FakeHarnessSession(), str(tmp_path), str(tmp_path))

    # Do
    subscription = await adapter.subscribe(
        SessionReadParams(session_id=root_id, history=PageRequest(limit=20))
    )
    registration = await anext(subscription.events)
    event = await anext(subscription.events)

    # Assert
    assert isinstance(registration.event, ChildSessionUpdated)
    assert registration.event.child_session.id == child_id
    assert registration.session_id == root_id
    assert registration.event_id == 1
    assert isinstance(event.event, ChildSessionUpdated)
    assert event.event.child_session.id == child_id
    assert event.event.child_session.status.type == "running"
    assert event.session_id == root_id
    assert event.event_id == 2
    assert event.params is not None
    assert event.params.model_dump(by_alias=True)["sessionId"] == root_id
    assert subscription.snapshot.state.child_sessions == []
    await cast(Any, subscription.events).aclose()

    resumed = await adapter.subscribe(
        SessionReadParams(session_id=root_id, history=PageRequest(limit=20))
    )
    assert len(resumed.snapshot.state.child_sessions) == 1
    assert resumed.snapshot.state.child_sessions[0].id == child_id
    assert resumed.snapshot.state.child_sessions[0].status.type == "running"
    await cast(Any, resumed.events).aclose()


def _persisted_child_record(
    agent_name: str,
    child_session_id: str,
    state: Any,
    *,
    agent_type: str | None = "explore",
) -> Any:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._subagents._models import ChildSessionRecord

    return ChildSessionRecord(
        agent_name=agent_name,
        agent_type=agent_type,
        child_session_id=child_session_id,
        spawn_action_id=f"{agent_name}-spawn",
        spawn_request_digest=f"{agent_name}-request",
        template_digest=f"{agent_name}-template",
        policy_ceiling_digest=f"{agent_name}-ceiling",
        state=state,
    )


def _persisted_child_states() -> dict[str, Any]:
    """One child record per lifecycle type, keyed by its ``type`` discriminator."""
    from mistralai_vibe_local_harness.vibe._subagents._models import (
        ChildGenerationRef,
        ChildTombstone,
        CompletedChildTurnOutcome,
        CreationFailedChild,
        DeletingIdleChild,
        FailedChildTurnOutcome,
        IdleChild,
        RunningChild,
        SubagentFailure,
        TurnFailedChild,
    )

    completed = IdleChild(
        last_outcome=CompletedChildTurnOutcome(
            generation=1,
            turn_id="turn-1",
            completed_at_unix_ms=1,
            output=[],
            final_answer="done",
        )
    )
    failure = SubagentFailure(code="spawn_failed", message="no store", retryable=False)
    return {
        "idle": completed,
        "running": RunningChild(
            active=ChildGenerationRef(generation=1, turn_id="turn-1")
        ),
        "turn_failed": TurnFailedChild(
            outcome=FailedChildTurnOutcome(
                generation=1, turn_id="turn-1", completed_at_unix_ms=1, failure=failure
            ),
            reusable=True,
        ),
        "tombstone": ChildTombstone(),
        "deleting_idle": DeletingIdleChild(),
        "creation_failed": CreationFailedChild(failure=failure),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("state_type", ["idle", "running", "turn_failed"])
async def test_unified_adapter_seeds_persisted_children_into_the_resume_state(
    tmp_path: Path, state_type: str
) -> None:
    """*Prepare*: A root adapter whose Host holds one persisted child record.
    *Do*: Seed the persisted child states, then read the root.
    *Assert*: The read state lists the child with the record's identity.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    child = _RecordingSession()
    child.session_id = "session-child"
    child.sent.append(SimpleNamespace(message=[TextContentBlock(text="child")]))

    class FakeHost:
        def child_session_records(self, root_session_id: str) -> tuple[Any, ...]:
            assert root_session_id == "session-1"
            return (
                _persisted_child_record(
                    "test-audit", "session-child", _persisted_child_states()[state_type]
                ),
            )

        def references_child(self, root_session_id: str, child_session_id: str) -> bool:
            return (
                root_session_id == "session-1" and child_session_id == "session-child"
            )

        async def read(self, params: Any) -> object:
            result = await child.read(params)
            return SimpleNamespace(snapshot=result.snapshot, cwd=str(tmp_path))

    adapter = _inert_adapter(
        _RecordingSession(), str(tmp_path), str(tmp_path), host=FakeHost()
    )

    # Do
    await adapter.seed_persisted_child_states()
    read = await adapter.read(
        SessionReadParams(session_id="session-1", history=PageRequest(limit=20))
    )

    # Assert
    assert [summary.id for summary in read.state.child_sessions] == ["session-child"]
    seeded = read.state.child_sessions[0]
    assert seeded.name == "test-audit"
    assert seeded.agent_type == "explore"
    assert seeded.status.type == "idle"
    assert adapter.references_child("session-child")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state_type", ["tombstone", "deleting_idle", "creation_failed"]
)
async def test_unified_adapter_does_not_seed_deleted_or_failed_children(
    tmp_path: Path, state_type: str
) -> None:
    """*Prepare*: A root adapter whose Host holds one unseedable child record.
    *Do*: Seed the persisted child states.
    *Assert*: The record is skipped; only live-comparable children are listed.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    class FakeHost:
        def child_session_records(self, root_session_id: str) -> tuple[Any, ...]:
            assert root_session_id == "session-1"
            return (
                _persisted_child_record(
                    "test-audit", "session-child", _persisted_child_states()[state_type]
                ),
            )

    adapter = _inert_adapter(
        _RecordingSession(), str(tmp_path), str(tmp_path), host=FakeHost()
    )

    # Do
    await adapter.seed_persisted_child_states()

    # Assert
    read = await adapter.read(
        SessionReadParams(session_id="session-1", history=PageRequest(limit=20))
    )
    assert read.state.child_sessions == []


@pytest.mark.asyncio
async def test_unified_adapter_resume_survives_an_unreadable_child_store(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """*Prepare*: A root adapter whose Host holds a child with no readable store.
    *Do*: Seed the persisted child states.
    *Assert*: The child is skipped with a warning instead of failing the resume.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    class FakeHost:
        def child_session_records(self, root_session_id: str) -> tuple[Any, ...]:
            assert root_session_id == "session-1"
            return (
                _persisted_child_record(
                    "test-audit", "session-child", _persisted_child_states()["idle"]
                ),
            )

        async def read(self, _params: Any) -> object:
            raise RuntimeError("child store is gone")

    adapter = _inert_adapter(
        _RecordingSession(), str(tmp_path), str(tmp_path), host=FakeHost()
    )

    # Do
    with caplog.at_level(logging.WARNING, logger="vibe"):
        await adapter.seed_persisted_child_states()

    # Assert
    assert "session-child" in caplog.text
    read = await adapter.read(
        SessionReadParams(session_id="session-1", history=PageRequest(limit=20))
    )
    assert read.state.child_sessions == []


@pytest.mark.asyncio
async def test_unified_adapter_resume_survives_a_host_error_enumerating_children(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """*Prepare*: A root adapter whose Host fails to enumerate child records.
    *Do*: Seed the persisted child states.
    *Assert*: The enumeration warning is logged and the resume stays empty.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    class FakeHost:
        def child_session_records(self, root_session_id: str) -> tuple[Any, ...]:
            assert root_session_id == "session-1"
            raise RuntimeError("cannot enumerate children")

    adapter = _inert_adapter(
        _RecordingSession(), str(tmp_path), str(tmp_path), host=FakeHost()
    )

    # Do
    with caplog.at_level(logging.WARNING, logger="vibe"):
        await adapter.seed_persisted_child_states()

    # Assert
    assert "Skipping persisted child summaries" in caplog.text
    read = await adapter.read(
        SessionReadParams(session_id="session-1", history=PageRequest(limit=20))
    )
    assert read.state.child_sessions == []


@pytest.mark.asyncio
async def test_unified_adapter_live_registration_overwrites_a_seeded_child(
    tmp_path: Path,
) -> None:
    """*Prepare*: A root adapter seeded with a persisted idle child.
    *Do*: Subscribe, then let the Host register the live child again.
    *Assert*: The registration overwrites the seeded row with the live state.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._session import HarnessSessionSubscription

    root_id = "session-root"
    child_id = "session-child"
    seeded_state = HarnessPublicSessionState(
        session=HarnessPublicSession(
            id=child_id,
            root_session_id=root_id,
            parent_session_id=root_id,
            status=IdleSessionStatus(),
            created_at=1,
            updated_at=1,
        ),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    live_state = seeded_state.model_copy(
        update={"session": seeded_state.session.model_copy(update={"updated_at": 2})}
    )
    seeded_snapshot = HarnessSessionSnapshot(
        state=seeded_state, history_limit=20, watermark=0
    )

    class FakeHarnessSession:
        session_id = root_id
        parent_session_id = None

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        def pin(self, _pin: SessionPin) -> str | None:
            return None

        async def subscribe(self, _params: object) -> HarnessSessionSubscription:
            async def events():
                yield {
                    "type": "child_session_registered",
                    "sessionId": child_id,
                    "eventId": 1,
                    "snapshot": HarnessSessionSnapshot(
                        state=live_state, history_limit=20, watermark=0
                    ).model_dump(mode="json", by_alias=True),
                }

            return HarnessSessionSubscription(
                snapshot=HarnessSessionSnapshot(
                    state=HarnessPublicSessionState(
                        session=HarnessPublicSession(
                            id=root_id,
                            status=IdleSessionStatus(),
                            created_at=1,
                            updated_at=1,
                        ),
                        turn_queue=HarnessTurnQueue(
                            items=[], paused=False, max_items=32
                        ),
                    ),
                    history_limit=20,
                    watermark=0,
                ),
                events=events(),
            )

    class FakeHost:
        def child_session_records(self, root_session_id: str) -> tuple[Any, ...]:
            assert root_session_id == root_id
            return (
                _persisted_child_record(
                    "test-audit", child_id, _persisted_child_states()["idle"]
                ),
            )

        def references_child(self, root_session_id: str, child_session_id: str) -> bool:
            return root_session_id == root_id and child_session_id == child_id

        async def read(self, _params: Any) -> object:
            return SimpleNamespace(snapshot=seeded_snapshot, cwd=str(tmp_path))

    adapter = _inert_adapter(
        FakeHarnessSession(), str(tmp_path), str(tmp_path), host=FakeHost()
    )
    await adapter.seed_persisted_child_states()
    assert adapter._child_states[child_id].session.updated_at == 1

    # Do
    subscription = await adapter.subscribe(
        SessionReadParams(session_id=root_id, history=PageRequest(limit=20))
    )
    registration = await anext(subscription.events)

    # Assert
    assert isinstance(registration.event, ChildSessionUpdated)
    assert registration.event.child_session.id == child_id
    assert adapter._child_states[child_id].session.updated_at == 2
    await cast(Any, subscription.events).aclose()


def test_child_summaries_derive_identity_and_stop_without_name_reuse_races(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import _HistoryProjections
    from vibe.app_server.models import (
        ArchivedSessionStatus,
        SubagentEffectDetail,
        SubagentEffectInput,
    )
    from vibe.utils.tool_presentation import EffectCallDisplay, EffectResultDisplay

    def effect(
        effect_id: str,
        created_at: int,
        detail: GenericEffectDetail | SubagentEffectDetail,
    ) -> PublicEffectEntry:
        return PublicEffectEntry(
            id=effect_id,
            session_id="session-1",
            turn_id="turn-1",
            created_at=created_at,
            updated_at=created_at,
            generation_status=PublicEntryGenerationStatus.COMPLETED,
            title=detail.tool_name,
            detail=detail,
            state=CompletedEffectState(
                display=EffectResultDisplay(success=True, message="done")
            ),
        )

    def spawn(effect_id: str, child_id: str, created_at: int) -> PublicEffectEntry:
        return effect(
            effect_id,
            created_at,
            SubagentEffectDetail(
                tool_name="subagent.spawn",
                input=SubagentEffectInput(task="Audit tests", agent="explore"),
                child_session_id=child_id,
                agent_name="test-audit",
                display=EffectCallDisplay(
                    summary="Starting test-audit",
                    message="test-audit",
                    status_text="Starting test-audit",
                ),
            ),
        )

    adapter = _inert_adapter(_RecordingSession(), str(tmp_path), str(tmp_path))
    root_session = PublicSession(
        id="session-1", status=IdleSessionStatus(), created_at=1, updated_at=1
    )
    first_child = PublicSession(
        id="child-1",
        status=IdleSessionStatus(),
        created_at=1,
        updated_at=2,
        context_usage=TokenUsage(
            input_tokens=1200, output_tokens=34, total_tokens=1234
        ),
    )
    second_child = PublicSession(
        id="child-2", status=IdleSessionStatus(), created_at=3, updated_at=3
    )
    adapter._child_states = {
        first_child.id: PublicSessionState(event_id=0, session=first_child),
        second_child.id: PublicSessionState(event_id=0, session=second_child),
    }
    adapter._child_history_projections = {
        first_child.id: _HistoryProjections(),
        second_child.id: _HistoryProjections(),
    }
    stop = effect(
        "effect-stop",
        3,
        GenericEffectDetail(
            tool_name="subagent.stop",
            input={"agentName": "test-audit"},
            display=EffectCallDisplay(
                summary="Stopping test-audit", status_text="Stopping test-audit"
            ),
        ),
    )
    root_state = PublicSessionState(
        event_id=0,
        session=root_session,
        history=[
            spawn("effect-spawn-1", first_child.id, 1),
            stop,
            spawn("effect-spawn-2", second_child.id, 3),
        ],
    )
    in_progress_stop = stop.model_copy(
        update={
            "generation_status": PublicEntryGenerationStatus.IN_PROGRESS,
            "state": RunningEffectState(),
        }
    )
    adapter._state_with_child_summaries(
        root_state.model_copy(
            update={
                "history": [
                    spawn("effect-spawn-1", first_child.id, 1),
                    in_progress_stop,
                    spawn("effect-spawn-2", second_child.id, 3),
                ]
            }
        )
    )

    summaries = adapter._state_with_child_summaries(root_state).child_sessions

    assert [(child.name, child.agent_type) for child in summaries] == [
        ("test-audit", "explore"),
        ("test-audit", "explore"),
    ]
    assert isinstance(summaries[0].status, ArchivedSessionStatus)
    assert isinstance(summaries[1].status, IdleSessionStatus)
    assert summaries[0].context_usage == first_child.context_usage
    assert list(adapter._child_history_projections) == [second_child.id]


@pytest.mark.asyncio
async def test_unified_subagent_analytics_emits_one_content_free_terminal_event(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    """*Prepare*: A Unified wait effect transitions from running to a timed-out result.
    *Do*: Reconcile the terminal Harness snapshot twice through the Vibe adapter.
    *Assert*: One existing tool-finished event exposes only approved dimensions.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )

    effect = {
        "type": "effect",
        "id": "effect-wait-1",
        "sessionId": "session-root",
        "turnId": "turn-root",
        "createdAt": 1,
        "updatedAt": 1,
        "generationStatus": "in_progress",
        "relatedEntryId": None,
        "title": "subagent.wait",
        "detail": {
            "kind": "tool",
            "toolName": "subagent.wait",
            "input": {
                "agentName": "secret-agent-name",
                "timeoutMs": 10,
                "prompt": "secret prompt",
            },
            "display": {
                "summary": "Waiting for secret-agent-name",
                "statusText": "Waiting",
            },
        },
        "state": {"status": "running", "outputText": ""},
    }
    session = HarnessPublicSession(
        id="session-root", status=IdleSessionStatus(), created_at=1, updated_at=1
    )
    running = HarnessPublicSessionState(
        session=session,
        history=LatestPublicHistoryPage(entries=[effect]),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    terminal_effect = {
        **effect,
        "updatedAt": 2,
        "generationStatus": "completed",
        "state": {
            "status": "completed",
            "output": {
                "type": "success",
                "content": [],
                "structured_content": {
                    "type": "error",
                    "error": "subagent_wait_timeout: secret timeout detail",
                },
            },
            "outputText": "",
            "durationMs": 1,
            "display": {"success": True, "message": "Wait completed"},
        },
    }
    terminal = HarnessPublicSessionState(
        session=session.model_copy(update={"updated_at": 2}),
        history=LatestPublicHistoryPage(entries=[terminal_effect]),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    telemetry = TelemetryClient(
        config_getter=lambda: build_test_vibe_config(enable_telemetry=True),
        harness_backend=ExperimentSurface.UNIFIED,
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
        telemetry_client=telemetry,
    )
    previous = adapter._read_response(
        HarnessSessionSnapshot(state=running, history_limit=20, watermark=0)
    ).state
    event = {
        "type": "session_state_updated",
        "sessionId": "session-root",
        "eventId": 1,
        "state": terminal.model_dump(mode="json", by_alias=True),
    }

    # Do
    _, current, _ = await adapter._state_update_events(event, previous, 20)
    await adapter._state_update_events({**event, "eventId": 2}, current, 20)

    # Assert
    assert len(telemetry_events) == 1
    assert telemetry_events[0]["event_name"] == "vibe.tool_call_finished"
    properties = telemetry_events[0]["properties"]
    assert properties["harness_backend"] == "unified"
    assert properties["tool_name"] == "subagent.wait"
    assert properties["status"] == "failure"
    assert properties["subagent_operation"] == "wait"
    assert properties["subagent_outcome"] == "timeout"
    assert properties["subagent_depth"] == 1
    serialized = repr(properties)
    assert "secret-agent-name" not in serialized
    assert "secret prompt" not in serialized
    assert "secret timeout detail" not in serialized


def test_request_sent_forwarding_maps_call_type_and_drains(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    """*Prepare*: The runtime buffers five completions (first turn, follow-up,
    compaction, classify gate, background title).
    *Do*: Drain the buffer through the adapter twice.
    *Assert*: One ``vibe.request_sent`` per payload, call-type mapped, then empty.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe import RequestSentTelemetry

    telemetry = TelemetryClient(
        config_getter=lambda: build_test_vibe_config(enable_telemetry=True),
        harness_backend=ExperimentSurface.UNIFIED,
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
        telemetry_client=telemetry,
    )
    queue = adapter._context.request_sent
    queue.record(
        RequestSentTelemetry(
            model="m1",
            purpose="agent",
            iteration=0,
            nb_context_chars=10,
            nb_context_messages=3,
            nb_prompt_chars=4,
        )
    )
    queue.record(
        RequestSentTelemetry(
            model="m1",
            purpose="agent",
            iteration=1,
            nb_context_chars=12,
            nb_context_messages=4,
            nb_prompt_chars=0,
        )
    )
    queue.record(
        RequestSentTelemetry(
            model="mc",
            purpose="compaction",
            iteration=0,
            nb_context_chars=8,
            nb_context_messages=2,
            nb_prompt_chars=0,
        )
    )
    queue.record(
        RequestSentTelemetry(
            model="ms",
            purpose="classify",
            iteration=0,
            nb_context_chars=6,
            nb_context_messages=2,
            nb_prompt_chars=2,
        )
    )
    queue.record(
        RequestSentTelemetry(
            model="mt",
            purpose="title",
            iteration=0,
            nb_context_chars=5,
            nb_context_messages=2,
            nb_prompt_chars=1,
        )
    )

    adapter._forward_request_sent()

    events = [e for e in telemetry_events if e["event_name"] == "vibe.request_sent"]
    assert [e["properties"]["call_type"] for e in events] == [
        "main_call",
        "secondary_call",
        "secondary_call",
        "smart_approve",
        "title_generation",
    ]
    first = events[0]["properties"]
    assert first["model"] == "m1"
    assert first["nb_context_chars"] == 10
    assert first["nb_context_messages"] == 3
    assert first["nb_prompt_chars"] == 4
    assert first["call_source"] == "vibe_code"
    assert first["harness_backend"] == "unified"

    # A second drain forwards nothing: the buffer emptied on the first pass.
    adapter._forward_request_sent()
    assert (
        len([e for e in telemetry_events if e["event_name"] == "vibe.request_sent"])
        == 5
    )


def test_request_sent_carries_client_message_id(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    """*Prepare*: A user entry and a buffered request-sent payload.
    *Do*: Reconcile the state (learns the message id), then drain the queue.
    *Assert*: The ``vibe.request_sent`` event carries the user entry's message id.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe import RequestSentTelemetry

    session = HarnessPublicSession(
        id="session-root", status=IdleSessionStatus(), created_at=1, updated_at=1
    )
    user_entry: JsonObject = {
        "type": "message",
        "id": "msg-xyz",
        "sessionId": "session-root",
        "turnId": "turn-1",
        "createdAt": 1,
        "updatedAt": 1,
        "generationStatus": "completed",
        "relatedEntryId": None,
        "role": "user",
        "content": [{"type": "text", "text": "hello"}],
    }
    state = HarnessPublicSessionState(
        session=session,
        history=LatestPublicHistoryPage(entries=[user_entry]),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    telemetry = TelemetryClient(
        config_getter=lambda: build_test_vibe_config(enable_telemetry=True),
        harness_backend=ExperimentSurface.UNIFIED,
    )
    adapter = _inert_adapter(
        SimpleNamespace(
            session_id="session-root", configure_turn_settlement=lambda settle: None
        ),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
        telemetry_client=telemetry,
    )
    # Simulate the runtime buffering a request-sent payload.
    adapter._context.request_sent.record(
        RequestSentTelemetry(
            model="m1",
            purpose="agent",
            iteration=0,
            nb_context_chars=10,
            nb_context_messages=3,
            nb_prompt_chars=5,
        )
    )
    # _updated_state calls _record_tool_telemetry first (which learns the
    # message id), then _forward_request_sent. Mirror that order.
    adapter._record_tool_telemetry(state)
    adapter._forward_request_sent()

    events = [e for e in telemetry_events if e["event_name"] == "vibe.request_sent"]
    assert len(events) == 1
    assert events[0]["properties"]["message_id"] == "msg-xyz"


def _vision_image_block() -> Any:
    from vibe.app_server.models import (
        ImageAttachment,
        ImageContentBlock as ModelsImageContentBlock,
        InlineImageSource,
    )

    return ModelsImageContentBlock(
        attachment=ImageAttachment(
            source=InlineImageSource(data="aW1hZ2U="),
            alias="image",
            mime_type="image/png",
        )
    )


@pytest.mark.parametrize(
    ("supports_images", "expected_counts"),
    [(True, {"image": 2}), (False, {})],
    ids=["vision_model", "text_model"],
)
def test_request_sent_carries_turn_attachment_counts(
    tmp_path: Path,
    telemetry_events: list[dict[str, Any]],
    supports_images: bool,
    expected_counts: dict[str, int],
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe import RequestSentTelemetry
    from vibe.core.config import ModelConfig

    telemetry = TelemetryClient(
        config_getter=lambda: build_test_vibe_config(enable_telemetry=True),
        harness_backend=ExperimentSurface.UNIFIED,
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        telemetry_client=telemetry,
    )
    adapter._context = replace(
        adapter._context,
        config_orchestrator=FakeConfigOrchestrator(
            build_test_vibe_config(
                models=[
                    ModelConfig(
                        name="vision" if supports_images else "text",
                        provider="mistral",
                        alias="active",
                        supports_images=supports_images,
                    )
                ],
                active_model="active",
            )
        ),
    )

    adapter._track_turn_attachments([
        TextContentBlock(text="look at this"),
        _vision_image_block(),
        _vision_image_block(),
    ])
    adapter._context.request_sent.record(
        RequestSentTelemetry(
            model="active",
            purpose="agent",
            iteration=0,
            nb_context_chars=10,
            nb_context_messages=3,
            nb_prompt_chars=5,
        )
    )

    adapter._forward_request_sent()

    events = [e for e in telemetry_events if e["event_name"] == "vibe.request_sent"]
    assert len(events) == 1
    assert events[0]["properties"]["attachment_counts"] == expected_counts


def test_request_sent_tracks_queued_entry_attachments(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe import RequestSentTelemetry
    from vibe.app_server.models import SessionImageContentBlock
    from vibe.core.config import ModelConfig

    telemetry = TelemetryClient(
        config_getter=lambda: build_test_vibe_config(enable_telemetry=True),
        harness_backend=ExperimentSurface.UNIFIED,
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        telemetry_client=telemetry,
    )
    adapter._context = replace(
        adapter._context,
        config_orchestrator=FakeConfigOrchestrator(
            build_test_vibe_config(
                models=[
                    ModelConfig(
                        name="vision",
                        provider="mistral",
                        alias="active",
                        supports_images=True,
                    )
                ],
                active_model="active",
            )
        ),
    )

    adapter._track_queue_attachments(
        TurnEnqueueParams(
            session_id="session-root",
            entries=[
                TurnUserInputEntry(
                    content=[
                        SessionTextContentBlock(text="queued"),
                        SessionImageContentBlock(uri="file:///tmp/img.png"),
                    ]
                )
            ],
        )
    )
    adapter._context.request_sent.record(
        RequestSentTelemetry(
            model="active",
            purpose="agent",
            iteration=0,
            nb_context_chars=10,
            nb_context_messages=3,
            nb_prompt_chars=5,
        )
    )

    adapter._forward_request_sent()

    events = [e for e in telemetry_events if e["event_name"] == "vibe.request_sent"]
    assert len(events) == 1
    assert events[0]["properties"]["attachment_counts"] == {"image": 1}


@pytest.mark.asyncio
async def test_model_calls_count_only_the_sessions_own_agent_calls(
    tmp_path: Path,
) -> None:
    """*Prepare*: A session in a turn, and the runtime reporting two agent calls
    for that turn, one for a subagent's turn and a compaction.
    *Do*: Reconcile the session's next state.
    *Assert*: The stats count the session's own two agent calls only.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        InProgressPublicTurn,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe import RequestSentTelemetry

    # Prepare
    session = HarnessPublicSession(
        id="session-root", status=IdleSessionStatus(), created_at=1, updated_at=1
    )
    in_turn = HarnessPublicSessionState(
        session=session,
        latest_turn=InProgressPublicTurn(
            id="turn-root", session_id="session-root", started_at=1
        ),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
    )
    previous = adapter._read_response(
        HarnessSessionSnapshot(state=in_turn, history_limit=20, watermark=0)
    ).state
    event = {
        "type": "session_state_updated",
        "sessionId": "session-root",
        "eventId": 1,
        "state": in_turn.model_dump(mode="json", by_alias=True),
    }
    _, previous, _ = await adapter._state_update_events(event, previous, 20)
    reported: list[tuple[Literal["agent", "compaction"], str]] = [
        ("agent", "turn-root"),
        ("agent", "child-turn"),
        ("compaction", "turn-root"),
        ("agent", "turn-root"),
    ]
    for purpose, turn_id in reported:
        adapter._context.request_sent.record(
            RequestSentTelemetry(
                model="m1",
                purpose=purpose,
                iteration=0,
                nb_context_chars=1,
                nb_context_messages=1,
                nb_prompt_chars=1,
                turn_id=turn_id,
            )
        )

    # Do
    await adapter._state_update_events({**event, "eventId": 2}, previous, 20)

    # Assert
    assert adapter._runtime.stats.model_calls == 2


def test_start_turn_sets_client_message_id_before_runtime(tmp_path: Path) -> None:
    """*Prepare*: An inert adapter with a message id holder.
    *Do*: Call ``_set_client_message_id`` (the method ``start_turn`` calls
    before scheduling the runtime task).
    *Assert*: Both ``_last_client_message_id`` and the holder are updated, so
    the provider request metadata reads the id on the first completion.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _inert_adapter(_RecordingSession(), str(tmp_path), str(tmp_path))
    adapter._message_id_holder = [None]

    adapter._set_client_message_id("msg-start-turn")

    assert adapter._last_client_message_id == "msg-start-turn"
    assert adapter._message_id_holder[0] == "msg-start-turn"


def _tool_effect(
    effect_id: str,
    tool_name: str,
    status: str,
    tool_input: JsonObject | None = None,
    *,
    decision: str | None = None,
    approval_type: str | None = None,
    approval_source: str | None = None,
) -> JsonObject:
    state: JsonObject = {"status": status}
    if status == "failed":
        state["error"] = {"message": "boom", "code": "tool_failed"}
    if decision is not None:
        state["decision"] = decision
    if approval_type is not None:
        state["approvalType"] = approval_type
    if approval_source is not None:
        state["approvalSource"] = approval_source
    return {
        "type": "effect",
        "id": effect_id,
        "detail": {"kind": "tool", "toolName": tool_name, "input": tool_input or {}},
        "state": state,
    }


def test_terminal_tool_effect_maps_names_status_and_file_metrics() -> None:
    """*Prepare*: raw history effects for write/edit/read/failed/subagent/non-tool.
    *Do*: Run each through ``_terminal_tool_effect``.
    *Assert*: legacy tool name + status + file metrics; non-ordinary tools drop out.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import _terminal_tool_effect

    write = _terminal_tool_effect(
        _tool_effect("e1", "file_system.write_file", "completed", {"path": "a/b.PY"})
    )
    assert write is not None
    assert (write.tool_name, write.status) == ("write_file", "success")
    assert (write.nb_files_created, write.nb_files_modified) == (1, 0)
    assert write.file_extension == ".py"

    edit = _terminal_tool_effect(
        _tool_effect(
            "e2", "file_system.search_replace", "completed", {"file_path": "x.ts"}
        )
    )
    assert edit is not None
    assert (edit.tool_name, edit.nb_files_modified, edit.file_extension) == (
        "edit",
        1,
        ".ts",
    )

    read = _terminal_tool_effect(
        _tool_effect("e3", "file_system.read_file", "completed", {"path": "y.md"})
    )
    assert read is not None
    assert (read.tool_name, read.nb_files_created, read.file_extension) == (
        "read_file",
        0,
        ".md",
    )

    failed = _terminal_tool_effect(
        _tool_effect("e4", "file_system.write_file", "failed", {"path": "z.txt"})
    )
    assert failed is not None
    # A failed write reports failure and no file was created.
    assert (failed.status, failed.nb_files_created, failed.file_extension) == (
        "failure",
        0,
        None,
    )

    bash = _terminal_tool_effect(
        _tool_effect("e5", "file_system.bash", "completed", {"command": "ls"})
    )
    assert bash is not None and bash.tool_name == "bash"

    # Subagent tools have their own event; non-effect entries and running effects drop.
    assert (
        _terminal_tool_effect(_tool_effect("e6", "subagent.wait", "completed")) is None
    )
    assert (
        _terminal_tool_effect(_tool_effect("e7", "file_system.read_file", "running"))
        is None
    )
    assert _terminal_tool_effect({"type": "message"}) is None


def test_ordinary_tool_call_finished_emits_once_with_file_metrics(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    """*Prepare*: A completed ``write_file`` effect in the harness history.
    *Do*: Reconcile the same terminal state twice.
    *Assert*: One ``tool_call_finished`` with the legacy name and file metrics.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )

    session = HarnessPublicSession(
        id="session-root", status=IdleSessionStatus(), created_at=1, updated_at=1
    )
    state = HarnessPublicSessionState(
        session=session,
        history=LatestPublicHistoryPage(
            entries=[
                _tool_effect(
                    "effect-write-1",
                    "file_system.write_file",
                    "completed",
                    {"path": "src/main.py", "content": "x"},
                )
            ]
        ),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    telemetry = TelemetryClient(
        config_getter=lambda: build_test_vibe_config(enable_telemetry=True),
        harness_backend=ExperimentSurface.UNIFIED,
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
        telemetry_client=telemetry,
    )

    adapter._record_tool_telemetry(state)
    adapter._record_tool_telemetry(state)

    events = [
        e for e in telemetry_events if e["event_name"] == "vibe.tool_call_finished"
    ]
    assert len(events) == 1
    props = events[0]["properties"]
    assert props["tool_name"] == "write_file"
    assert props["status"] == "success"
    assert props["nb_files_created"] == 1
    assert props["nb_files_modified"] == 0
    assert props["file_extension"] == ".py"
    assert props["decision"] is None
    assert props["approval_type"] is None
    assert props["approval_source"] is None
    assert props["harness_backend"] == "unified"
    # No harness background-process flag, so the field is never set.
    assert "bash_background" not in props


def test_ordinary_tool_call_finished_carries_decision_and_approval_type(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    """*Prepare*: A completed effect with decision=execute, approval_type=ask.
    *Do*: Reconcile the terminal state.
    *Assert*: ``tool_call_finished`` carries the decision and approval_type.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )

    session = HarnessPublicSession(
        id="session-root", status=IdleSessionStatus(), created_at=1, updated_at=1
    )
    state = HarnessPublicSessionState(
        session=session,
        history=LatestPublicHistoryPage(
            entries=[
                _tool_effect(
                    "effect-1",
                    "file_system.write_file",
                    "completed",
                    {"path": "a.py", "content": "x"},
                    decision="execute",
                    approval_type="ask",
                    approval_source="user",
                )
            ]
        ),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    telemetry = TelemetryClient(
        config_getter=lambda: build_test_vibe_config(enable_telemetry=True),
        harness_backend=ExperimentSurface.UNIFIED,
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
        telemetry_client=telemetry,
    )

    adapter._record_tool_telemetry(state)

    events = [
        e for e in telemetry_events if e["event_name"] == "vibe.tool_call_finished"
    ]
    assert len(events) == 1
    props = events[0]["properties"]
    assert props["decision"] == "execute"
    assert props["approval_type"] == "ask"
    assert props["approval_source"] == "user"


def test_terminal_tool_effect_extracts_decision_and_approval_type() -> None:
    """*Prepare*: A completed effect with decision/approval_type in its state.
    *Do*: Run through ``_terminal_tool_effect``.
    *Assert*: The _ToolCallTelemetry carries the decision and approval_type.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import _terminal_tool_effect

    result = _terminal_tool_effect(
        _tool_effect(
            "e1",
            "file_system.write_file",
            "completed",
            {"path": "a.py"},
            decision="skip",
            approval_type="never",
            approval_source="never",
        )
    )
    assert result is not None
    assert result.decision == "skip"
    assert result.approval_type == "never"
    assert result.approval_source == "never"

    # Absent fields default to None.
    result_none = _terminal_tool_effect(
        _tool_effect("e2", "file_system.write_file", "completed", {"path": "b.py"})
    )
    assert result_none is not None
    assert result_none.decision is None
    assert result_none.approval_type is None
    assert result_none.approval_source is None


def test_ordinary_tool_call_finished_carries_turn_client_message_id(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    """*Prepare*: A user entry and a terminal tool effect sharing a turn id.
    *Do*: Reconcile the state, then a follow-up page whose user entry is gone.
    *Assert*: Both tool-finished events carry the user entry's client message id.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )

    session = HarnessPublicSession(
        id="session-root", status=IdleSessionStatus(), created_at=1, updated_at=1
    )
    # The projection strips content-block meta and writes the client message
    # id into the user entry's id, so the fixture mirrors that wire shape.
    user_entry: JsonObject = {
        "type": "message",
        "id": "msg-abc",
        "sessionId": "session-root",
        "turnId": "turn-1",
        "createdAt": 1,
        "updatedAt": 1,
        "generationStatus": "completed",
        "relatedEntryId": None,
        "role": "user",
        "content": [{"type": "text", "text": "fix the bug"}],
    }
    effect = {
        **_tool_effect(
            "effect-write-1",
            "file_system.write_file",
            "completed",
            {"path": "src/main.py", "content": "x"},
        ),
        "sessionId": "session-root",
        "turnId": "turn-1",
        "createdAt": 2,
        "updatedAt": 2,
        "generationStatus": "completed",
        "relatedEntryId": None,
    }
    state = HarnessPublicSessionState(
        session=session,
        history=LatestPublicHistoryPage(entries=[user_entry, effect]),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    telemetry = TelemetryClient(
        config_getter=lambda: build_test_vibe_config(enable_telemetry=True),
        harness_backend=ExperimentSurface.UNIFIED,
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-root"),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
        telemetry_client=telemetry,
    )

    adapter._record_tool_telemetry(state)
    # A later snapshot without the user entry still attributes through the turn.
    follow_up = HarnessPublicSessionState(
        session=session,
        history=LatestPublicHistoryPage(
            entries=[
                {
                    **_tool_effect(
                        "effect-edit-2",
                        "file_system.search_replace",
                        "completed",
                        {"file_path": "src/main.py"},
                    ),
                    "sessionId": "session-root",
                    "turnId": "turn-1",
                    "createdAt": 3,
                    "updatedAt": 3,
                    "generationStatus": "completed",
                    "relatedEntryId": None,
                }
            ]
        ),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    adapter._record_tool_telemetry(follow_up)
    adapter._record_tool_telemetry(state)

    events = [
        e for e in telemetry_events if e["event_name"] == "vibe.tool_call_finished"
    ]
    assert len(events) == 2
    assert events[0]["properties"]["tool_name"] == "write_file"
    assert events[0]["properties"]["message_id"] == "msg-abc"
    assert events[1]["properties"]["tool_name"] == "edit"
    assert events[1]["properties"]["message_id"] == "msg-abc"


def _compaction_checkpoint(
    checkpoint_id: str, trigger: str, *, succeeded: bool, reason: str | None = None
) -> JsonObject:
    details: JsonObject = {"trigger": trigger, "attempt": 1}
    if succeeded:
        details["summaryLength"] = 10
        message = "Context compacted"
    else:
        details["error"] = {"code": "invalid_compaction_summary"}
        details["reason"] = reason
        message = "Context compaction failed"
    return {
        "type": "checkpoint",
        "id": f"checkpoint-compaction-{checkpoint_id}",
        "sessionId": "session-root",
        "turnId": "turn-1",
        "createdAt": 1,
        "updatedAt": 2,
        "generationStatus": "completed",
        "relatedEntryId": None,
        "kind": "compaction",
        "message": message,
        "details": details,
    }


def test_terminal_compaction_effect_maps_trigger_status_and_reason() -> None:
    """*Prepare*: success/failed/manual/in-progress compaction checkpoints.
    *Do*: Run each through ``_terminal_compaction_effect``.
    *Assert*: trigger, success flag, and legacy reason; the start entry drops.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        _terminal_compaction_effect,
    )

    ok = _terminal_compaction_effect(
        _compaction_checkpoint("c1", "automatic", succeeded=True)
    )
    assert ok is not None and ok.trigger == "automatic" and ok.succeeded

    failed = _terminal_compaction_effect(
        _compaction_checkpoint("c2", "automatic", succeeded=False, reason="tool_call")
    )
    assert failed is not None
    assert (failed.succeeded, failed.reason) == (False, "tool_call")

    manual = _terminal_compaction_effect(
        _compaction_checkpoint("c3", "manual", succeeded=True)
    )
    assert manual is not None and manual.trigger == "manual"

    # An in-progress start checkpoint is not terminal.
    started = {**_compaction_checkpoint("c4", "automatic", succeeded=True)}
    started["generationStatus"] = "in_progress"
    assert _terminal_compaction_effect(started) is None
    assert _terminal_compaction_effect({"type": "message"}) is None


def test_compaction_telemetry_emits_auto_compact_and_failed(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    """*Prepare*: an automatic failed compaction (reason ``tool_call``) in history.
    *Do*: Reconcile the state twice with a known pre-compaction context size.
    *Assert*: One ``auto_compact_triggered`` (failure) and one ``compaction_failed``.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )

    session = HarnessPublicSession(
        id="session-root", status=IdleSessionStatus(), created_at=1, updated_at=1
    )
    state = HarnessPublicSessionState(
        session=session,
        history=LatestPublicHistoryPage(
            entries=[
                _compaction_checkpoint(
                    "auto-1", "automatic", succeeded=False, reason="tool_call"
                )
            ]
        ),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    telemetry = TelemetryClient(
        config_getter=lambda: build_test_vibe_config(enable_telemetry=True),
        harness_backend=ExperimentSurface.UNIFIED,
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
        telemetry_client=telemetry,
    )
    adapter._context_tokens_before = 4321

    adapter._record_compaction_telemetry(state)
    adapter._record_compaction_telemetry(state)

    triggered = [
        e for e in telemetry_events if e["event_name"] == "vibe.auto_compact_triggered"
    ]
    failed = [
        e for e in telemetry_events if e["event_name"] == "vibe.compaction_failed"
    ]
    assert len(triggered) == 1
    assert triggered[0]["properties"]["status"] == "failure"
    assert triggered[0]["properties"]["nb_context_tokens_before"] == 4321
    assert len(failed) == 1
    assert failed[0]["properties"]["reason"] == "tool_call"


def test_manual_compaction_skips_auto_compact_triggered(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    """A user-initiated compaction is not the auto path, so no auto event fires."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )

    session = HarnessPublicSession(
        id="session-root", status=IdleSessionStatus(), created_at=1, updated_at=1
    )
    state = HarnessPublicSessionState(
        session=session,
        history=LatestPublicHistoryPage(
            entries=[_compaction_checkpoint("manual-1", "manual", succeeded=True)]
        ),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    telemetry = TelemetryClient(
        config_getter=lambda: build_test_vibe_config(enable_telemetry=True),
        harness_backend=ExperimentSurface.UNIFIED,
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
        telemetry_client=telemetry,
    )

    adapter._record_compaction_telemetry(state)

    assert not [
        e for e in telemetry_events if e["event_name"] == "vibe.auto_compact_triggered"
    ]


def test_context_gauge_retained_across_a_usageless_snapshot(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    """*Prepare*: a known pre-compaction size, then a snapshot with no usage reading.
    *Do*: Reconcile the usage-less snapshot, then an automatic compaction.
    *Assert*: the size is retained (not zeroed), so the auto event reports it.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )

    def _event(entries: list[JsonObject], event_id: int) -> JsonObject:
        # A session with no context_usage set is the usage-less case.
        state = HarnessPublicSessionState(
            session=HarnessPublicSession(
                id="session-root",
                status=IdleSessionStatus(),
                created_at=1,
                updated_at=1,
            ),
            history=LatestPublicHistoryPage(entries=entries),
            turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
        )
        return {
            "type": "session_state_updated",
            "sessionId": "session-root",
            "eventId": event_id,
            "state": state.model_dump(mode="json", by_alias=True),
        }

    telemetry = TelemetryClient(
        config_getter=lambda: build_test_vibe_config(enable_telemetry=True),
        harness_backend=ExperimentSurface.UNIFIED,
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
        telemetry_client=telemetry,
    )
    adapter._context_tokens_before = 4321

    adapter._updated_state(
        _event([], 1),
        20,
        cwd=str(tmp_path),
        metadata=None,
        projections=adapter._history_projections,
    )
    # The usage-less snapshot must not wipe the last reading.
    assert adapter._context_tokens_before == 4321

    adapter._updated_state(
        _event([_compaction_checkpoint("auto-ret", "automatic", succeeded=True)], 2),
        20,
        cwd=str(tmp_path),
        metadata=None,
        projections=adapter._history_projections,
    )
    triggered = [
        e for e in telemetry_events if e["event_name"] == "vibe.auto_compact_triggered"
    ]
    assert len(triggered) == 1
    assert triggered[0]["properties"]["nb_context_tokens_before"] == 4321


@pytest.mark.asyncio
async def test_unified_flush_events_returns_after_an_event_carrying_a_signal(
    tmp_path: Path,
) -> None:
    """A notice spends an event id, so the flush after it must see that id.

    ``plugin/reload`` publishes one and is answered through the same flush, so
    a signal the forwarder does not record hangs the request that published it.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._session import HarnessSessionSubscription

    stream_open = asyncio.Event()

    class FakeHarnessSession:
        session_id = "session-1"
        cwd: str | None = None

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        def pin(self, _pin: SessionPin) -> str | None:
            return None

        async def read(self, _params: object) -> object:
            return type("ReadResult", (), {"snapshot": self._snapshot(2)})()

        async def subscribe(self, _params: object) -> HarnessSessionSubscription:
            async def events():
                yield {
                    "type": "notice",
                    "level": "warning",
                    "message": "a plugin source is gone",
                    "eventId": 2,
                }
                await stream_open.wait()

            return HarnessSessionSubscription(
                snapshot=self._snapshot(1), events=events()
            )

        def _snapshot(self, watermark: int) -> HarnessSessionSnapshot:
            return HarnessSessionSnapshot(
                state=HarnessPublicSessionState(
                    session=HarnessPublicSession(
                        id=self.session_id,
                        status=IdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                    ),
                    turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
                ),
                history_limit=1,
                watermark=watermark,
            )

    adapter = _inert_adapter(FakeHarnessSession(), str(tmp_path), str(tmp_path))

    subscription = await adapter.subscribe(SessionReadParams(session_id="session-1"))
    forwarded: list[Any] = []
    delivered = asyncio.Event()

    async def forward() -> None:
        async for event in subscription.events:
            forwarded.append(event)
            delivered.set()

    forwarder = asyncio.create_task(forward())
    try:
        await asyncio.wait_for(delivered.wait(), timeout=1)
        await asyncio.wait_for(adapter.flush_events(), timeout=1)
    finally:
        stream_open.set()
        await forwarder

    assert [event.method for event in forwarded] == ["warning"]


@pytest.mark.asyncio
async def test_unified_harness_starts_a_text_turn() -> None:
    client, server = _connect_harness_host()

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        response = TurnStartResponse.model_validate(
            await client.request(
                "turn/start",
                TurnStartParams(
                    session_id=started.state.session.id,
                    message=[TextContentBlock(text="hello")],
                ),
            )
        )
    finally:
        await server.close()

    assert response.turn.session_id == started.state.session.id
    assert response.turn.status == "in_progress"
    assert response.last_event_id >= started.last_event_id


@pytest.mark.asyncio
async def test_unified_host_deletes_an_attached_root_through_the_selected_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An App Server has an attached root owned by the Unified Host.
    *Do*: Delete that root through the public session route.
    *Assert*: The selected Host removes it and a later read reports not found.
    """
    # Prepare
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path / "sessions"), session_prefix="session"
        )
    )
    closed_telemetry: list[TelemetryClient] = []
    original_close = TelemetryClient.aclose

    async def record_close(telemetry: TelemetryClient) -> None:
        closed_telemetry.append(telemetry)
        await original_close(telemetry)

    monkeypatch.setattr(TelemetryClient, "aclose", record_close)
    client, server = _connect_harness_host(config)
    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        host = cast(Any, server._session_backend_host)
        adapter = host._adapters[started.state.session.id]
        telemetry = adapter._telemetry

        # Do
        deleted = EmptyResponse.model_validate(
            await client.request(
                "session/delete",
                SessionDeleteParams(session_id=started.state.session.id),
            )
        )

        # Assert
        assert isinstance(deleted, EmptyResponse)
        assert started.state.session.id not in host._adapters
        assert telemetry not in host._telemetry_clients
        assert closed_telemetry == [telemetry]
        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                "session/read", SessionReadParams(session_id=started.state.session.id)
            )
        assert exc_info.value.error.code is ProtocolErrorCode.NOT_FOUND
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_unified_harness_rejects_idle_steer_with_conflict() -> None:
    client, server = _connect_harness_host()

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                "turn/steer",
                TurnSteerParams(
                    session_id=started.state.session.id,
                    expected_turn_id="turn-1",
                    message=[TextContentBlock(text="hello")],
                ),
            )
    finally:
        await server.close()

    assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
    assert exc_info.value.error.message == "No active turn"


@pytest.mark.asyncio
async def test_unified_harness_rejects_idle_interrupt_with_conflict() -> None:
    client, server = _connect_harness_host()

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                "turn/interrupt",
                TurnInterruptParams(
                    session_id=started.state.session.id, expected_turn_id="turn-1"
                ),
            )
    finally:
        await server.close()

    assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
    assert exc_info.value.error.message == "No active turn"


@pytest.mark.asyncio
async def test_unified_harness_fork_family_shares_root_affinity() -> None:
    host = _harness_backend_host()
    started = await host.start(SessionStartParams())
    root_id = started.backend.session_id

    forked = await host.fork(SessionForkParams(source_session_id=root_id, attach=True))
    assert forked.backend is not None
    nested = await host.fork(
        SessionForkParams(source_session_id=forked.backend.session_id, attach=True)
    )
    assert nested.backend is not None
    unrelated = await host.start(SessionStartParams())
    root_affinity = cast(Any, started.backend)._adapter_config.request_headers()[
        "x-affinity"
    ]
    fork_affinity = cast(Any, forked.backend)._adapter_config.request_headers()[
        "x-affinity"
    ]
    nested_affinity = cast(Any, nested.backend)._adapter_config.request_headers()[
        "x-affinity"
    ]
    unrelated_affinity = cast(Any, unrelated.backend)._adapter_config.request_headers()[
        "x-affinity"
    ]
    await host.shutdown()

    assert forked.backend.session_id != root_id
    assert forked.response.source_session_id == root_id
    assert forked.response.state.session.parent_session_id == root_id
    assert forked.response.state.session.root_session_id == root_id
    assert nested.backend.session_id != forked.backend.session_id
    assert nested.response.state.session.parent_session_id == forked.backend.session_id
    assert nested.response.state.session.root_session_id == root_id
    assert root_affinity == fork_affinity == nested_affinity == root_id
    assert unrelated_affinity == unrelated.backend.session_id
    assert unrelated_affinity != root_affinity


@pytest.mark.asyncio
async def test_unified_fork_emits_branch_telemetry_once(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    config = build_test_vibe_config(
        enable_telemetry=True,
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path)),
    )
    host = _harness_backend_host(config)
    started = await host.start(SessionStartParams())
    root_id = started.backend.session_id
    telemetry_events.clear()

    forked = await host.fork(SessionForkParams(source_session_id=root_id, attach=True))
    assert forked.backend is not None
    branch_events = [
        event
        for event in telemetry_events
        if event["event_name"] == "vibe.session_branched"
    ]

    assert len(branch_events) == 1
    properties = branch_events[0]["properties"]
    assert properties["session_id"] == forked.backend.session_id
    assert properties["parent_session_id"] == root_id
    assert properties["harness_backend"] == "unified"
    assert properties["source_session_id"] == root_id
    assert properties["new_session_id"] == forked.backend.session_id
    assert properties["root_session_id"] == root_id

    telemetry_events.clear()
    detached = await host.fork(
        SessionForkParams(source_session_id=forked.backend.session_id, attach=False)
    )
    detached_events = [
        event
        for event in telemetry_events
        if event["event_name"] == "vibe.session_branched"
    ]
    assert len(detached_events) == 1
    detached_properties = detached_events[0]["properties"]
    assert detached_properties["parent_session_id"] == forked.backend.session_id
    assert detached_properties["source_session_id"] == forked.backend.session_id
    assert detached_properties["new_session_id"] == detached.response.state.session.id
    assert detached_properties["root_session_id"] == root_id
    await host.shutdown()


@pytest.mark.asyncio
async def test_resumed_unified_fork_keeps_root_affinity(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    first_host = _harness_backend_host(config)
    started = await first_host.start(SessionStartParams())
    root_id = started.backend.session_id
    root_turn = await started.backend.start_turn(
        TurnStartParams(
            session_id=root_id, message=[TextContentBlock(text="persist root")]
        )
    )
    assert root_turn.after_response is not None
    root_turn.after_response()
    await cast(Any, started.backend)._session._wait_for_pending_turns()
    forked = await first_host.fork(
        SessionForkParams(source_session_id=root_id, attach=True)
    )
    assert forked.backend is not None
    fork_id = forked.backend.session_id
    fork_turn = await forked.backend.start_turn(
        TurnStartParams(
            session_id=fork_id, message=[TextContentBlock(text="persist fork")]
        )
    )
    assert fork_turn.after_response is not None
    fork_turn.after_response()
    await first_host.shutdown()

    second_host = _harness_backend_host(config)
    resumed = await second_host.resume(SessionResumeParams(session_id=fork_id))
    resumed_affinity = cast(Any, resumed.backend)._adapter_config.request_headers()[
        "x-affinity"
    ]
    await second_host.shutdown()

    assert resumed.backend.session_id == fork_id
    assert resumed_affinity == root_id


@pytest.mark.asyncio
async def test_unified_fork_isolates_a_managed_source_worktree(tmp_path: Path) -> None:
    """*Prepare*: A managed live Session with committed and uncommitted work.
    *Do*: Fork it, write independently in both checkouts, then reap only the fork.
    *Assert*: The committed state is isolated and omitted work produces a warning.
    """
    # Prepare
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repo = Repo.init(repo_root, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (repo_root / "file.txt").write_text("initial\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path / "sessions")
        )
    )
    host = _harness_backend_host(config)
    started = await host.start(
        SessionStartParams(
            agent_config=SessionOptions(
                cwd=str(repo_root),
                workspace_roots=[str(repo_root)],
                worktree=NewWorktreeInput(name="source", branch="vibe/source"),
            )
        )
    )
    source = cast(Any, started.backend)
    await source._await_deferred_setup()
    await source.update_settings(
        SessionSettingsUpdateParams(
            session_id=source.session_id, max_turns=7, max_tokens=4096
        )
    )
    assert source._settings.max_tokens == 4096
    from mistralai_vibe_local_harness.vibe import SessionConfig

    source_local = source._session.adapter_config
    assert source_local is not None
    source_core = source._session.configuration_over(_stub_core_config()).model_copy(
        update={"system_instructions": "live source instructions"}, deep=True
    )
    await source._session.apply_config(
        SessionConfig(
            core=source_core,
            local=replace(
                source_local,
                tool_modes={"file_system.bash": "deny"},
                provided_tool_mode="allow",
            ),
        )
    )
    source_cwd = Path(source.cwd)
    (source_cwd / "source-commit.txt").write_text("source head\n")
    with Repo(source_cwd) as source_repo:
        source_repo.index.add(["source-commit.txt"])
        source_repo.index.commit("source head")
    (source_cwd / "source-uncommitted.txt").write_text("source dirty\n")

    # Do
    forked = await host.fork(
        SessionForkParams(
            source_session_id=source.session_id,
            attach=True,
            agent_config=SessionOptions(
                cwd=str(repo_root), workspace_roots=[str(repo_root)]
            ),
        )
    )
    fork = cast(Any, forked.backend)
    fork_cwd = Path(fork.cwd)
    subscription = await fork.subscribe(
        SessionReadParams(session_id=fork.session_id, history=PageRequest(limit=10))
    )
    assert forked.after_response is not None
    forked.after_response()
    warning = await anext(subscription.events)
    (source_cwd / "source-only.txt").write_text("source\n")
    (fork_cwd / "fork-only.txt").write_text("fork\n")

    # Assert
    assert fork_cwd != source_cwd
    assert (fork_cwd / "source-commit.txt").read_text() == "source head\n"
    assert not (fork_cwd / "source-uncommitted.txt").exists()
    assert not (fork_cwd / "source-only.txt").exists()
    assert not (source_cwd / "fork-only.txt").exists()
    assert source._session.workspace is source._adapter_config.workspace
    assert source._session.workspace.cwd == source_cwd
    assert fork._session.workspace is fork._adapter_config.workspace
    assert fork._session.workspace.cwd == fork_cwd
    assert fork._session.workspace.roots == (fork_cwd,)
    assert fork._adapter_config.tool_modes["file_system.bash"] == "deny"
    assert fork._adapter_config.provided_tool_mode == "allow"
    assert (
        fork._session.configuration_over(_stub_core_config()).system_instructions
        == "live source instructions"
    )
    assert fork._adapter_config.credentials is not source_local.credentials
    assert fork._adapter_config.completion_metadata is not None
    assert source_local.completion_metadata is not None
    assert (
        fork._adapter_config.completion_metadata("agent", 0)["session_id"]
        == fork.session_id
    )
    assert (
        source_local.completion_metadata("agent", 0)["session_id"] == source.session_id
    )
    assert fork._settings == source._settings
    assert warning.method == "warning"
    assert isinstance(warning.params, ServerWarningParams)
    assert "Uncommitted and untracked changes" in warning.params.warning.message

    source_managed = ManagedWorktree.at(source_cwd)
    fork_managed = ManagedWorktree.at(fork_cwd)
    assert source_managed is not None
    assert fork_managed is not None
    assert source_managed.holders() == frozenset({source.session_id})
    assert fork_managed.holders() == frozenset({fork.session_id})

    release = fork_managed.reap(requester_id=fork.session_id, request_id="archive-fork")
    assert release.outcome is WorktreeReleaseOutcome.KEPT_IN_USE
    await fork.shutdown()
    assert fork_managed.holders() == frozenset()
    assert fork_managed.root.exists()
    completed = fork_managed.reap(
        requester_id=fork.session_id, request_id="archive-fork"
    )
    assert completed.outcome is WorktreeReleaseOutcome.REMOVED
    assert not fork_managed.root.exists()
    assert source_managed.root.exists()
    assert source_managed.holders() == frozenset({source.session_id})
    await host.shutdown()


@pytest.mark.asyncio
async def test_live_fork_blocks_new_turn_without_blocking_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A reserved source Turn and a paused Harness history copy.
    *Do*: Interrupt that Turn, then start another while the copy is paused.
    *Assert*: The interrupt finishes immediately, but the new Turn waits.
    """
    # Prepare
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    host = _harness_backend_host(config)
    started = await host.start(SessionStartParams())
    source = cast(Any, started.backend)
    reserved = await source.start_turn(
        TurnStartParams(
            session_id=source.session_id,
            message=[TextContentBlock(text="before snapshot")],
        )
    )
    raw_host = cast(Any, host)
    harness_fork = raw_host._host.fork
    copy_started = asyncio.Event()
    allow_copy = asyncio.Event()

    async def pause_history_copy(*args: Any, **kwargs: Any) -> Any:
        copy_started.set()
        await allow_copy.wait()
        return await harness_fork(*args, **kwargs)

    monkeypatch.setattr(raw_host._host, "fork", pause_history_copy)

    fork_task = asyncio.create_task(
        host.fork(SessionForkParams(source_session_id=source.session_id, attach=True))
    )
    interrupt_task: asyncio.Task[Any] | None = None
    source_turn_task: asyncio.Task[Any] | None = None
    try:
        # Do
        await asyncio.wait_for(copy_started.wait(), timeout=2)
        interrupt_task = asyncio.create_task(
            source.interrupt_turn(
                TurnInterruptParams(
                    session_id=source.session_id,
                    expected_turn_id=reserved.response.turn.id,
                )
            )
        )

        # Assert
        interrupted = await asyncio.wait_for(asyncio.shield(interrupt_task), timeout=1)
        assert interrupted.after_response is not None
        interrupted.after_response()

        source_turn_task = asyncio.create_task(
            source.start_turn(
                TurnStartParams(
                    session_id=source.session_id,
                    message=[TextContentBlock(text="after snapshot")],
                )
            )
        )
        await asyncio.sleep(0)
        assert not source_turn_task.done()

        allow_copy.set()
        forked = await asyncio.wait_for(fork_task, timeout=2)
        source_turn = await asyncio.wait_for(source_turn_task, timeout=2)
        assert source_turn.after_response is not None
        source_turn.after_response()
        assert forked.backend is not None
    finally:
        allow_copy.set()
        tasks = [
            task
            for task in (fork_task, interrupt_task, source_turn_task)
            if task is not None and not task.done()
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await host.shutdown()


@pytest.mark.asyncio
async def test_shutdown_during_worktree_adoption_releases_both_holders(
    tmp_path: Path,
) -> None:
    """*Prepare*: A session starts in one managed worktree and adopts another.
    *Do*: Shut its Host down after adoption but before the move returns.
    *Assert*: Neither the source nor target keeps the stopped session's holder.
    """
    # Prepare
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repo = Repo.init(repo_root, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (repo_root / "file.txt").write_text("initial\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    host = cast(Any, _harness_backend_host())
    source_result = await host.start(
        SessionStartParams(
            agent_config=SessionOptions(
                cwd=str(repo_root),
                workspace_roots=[str(repo_root)],
                worktree=NewWorktreeInput(name="source", branch="vibe/source"),
            )
        )
    )
    source = cast(Any, source_result.backend)
    await source._await_deferred_setup()
    source_cwd = Path(source.cwd)
    source_managed = ManagedWorktree.at(source_cwd)
    assert source_managed is not None
    announcing = asyncio.Event()

    async def block_after_adoption(_session_id: str, _cwd: str) -> None:
        announcing.set()
        await asyncio.Event().wait()

    host._announce_cwd = block_after_adoption
    moved_result = await host.start(
        SessionStartParams(
            agent_config=SessionOptions(
                cwd=str(source_cwd),
                workspace_roots=[str(source_cwd)],
                worktree=NewWorktreeInput(name="target", branch="vibe/target"),
            )
        )
    )
    moved = cast(Any, moved_result.backend)
    await asyncio.wait_for(announcing.wait(), timeout=5)
    target_managed = ManagedWorktree.at(Path(moved.cwd))
    assert target_managed is not None
    assert source_managed.holders() == frozenset({source.session_id})
    assert moved.session_id in target_managed.holders()

    # Do
    await host.shutdown()

    # Assert
    assert not source_managed.holders()
    assert not target_managed.holders()


@pytest.mark.asyncio
async def test_unified_fork_isolation_does_not_extend_trust_to_the_new_worktree(
    tmp_path: Path,
) -> None:
    """*Prepare*: A trusted live session in a managed source worktree.
    *Do*: Fork while the caller is already in that source checkout.
    *Assert*: The isolated fork is untrusted and the source keeps its grant.
    """
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repo = Repo.init(repo_root, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (repo_root / "file.txt").write_text("initial\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path / "sessions")
        )
    )
    inner = _test_session_runtime_builder(config)

    async def recording(
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: Any = "cli",
    ) -> Any:
        context = await inner(
            options, require_api_key=require_api_key, entrypoint=entrypoint
        )
        if options.trust_workspace and options.cwd is not None:
            context.harness_files.trust_store.trust_for_session(Path(options.cwd))
        return context

    host = adapt_harness_host(vibe_runtime.create_harness_host(), recording)
    started = await host.start(
        SessionStartParams(
            agent_config=SessionOptions(
                cwd=str(repo_root),
                workspace_roots=[str(repo_root)],
                worktree=NewWorktreeInput(name="source", branch="vibe/source"),
                trust_workspace=True,
            )
        )
    )
    source = cast(Any, started.backend)
    await source._await_deferred_setup()
    source_cwd = Path(source.cwd)
    assert trusted_folders_manager.is_trusted(source_cwd) is True

    forked = await host.fork(
        SessionForkParams(
            source_session_id=source.session_id,
            attach=True,
            agent_config=SessionOptions(
                cwd=str(source_cwd),
                workspace_roots=[str(source_cwd)],
                trust_workspace=True,
            ),
        )
    )
    fork = cast(Any, forked.backend)
    fork_cwd = Path(fork.cwd)

    assert fork_cwd != source_cwd
    assert trusted_folders_manager.is_trusted(fork_cwd) is not True
    assert trusted_folders_manager.is_trusted(source_cwd) is True
    await host.shutdown()


@pytest.mark.asyncio
async def test_unified_fork_copies_in_memory_loops_from_a_live_session(
    tmp_path: Path,
) -> None:
    """*Prepare*: An unsaved live session with a loop that exists only in memory.
    *Do*: Fork the session before its first turn promotes it to durable storage.
    *Assert*: The fork receives the live schedule instead of reading an absent file.
    """
    # Prepare
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    host = _harness_backend_host(config)
    started = await host.start(SessionStartParams())
    source = cast(Any, started.backend)
    loop = ScheduledLoop(
        id="abcd1234",
        interval_seconds=30,
        prompt="keep me",
        next_fire_at=100.0,
        created_at=70.0,
    )
    await source.replace_scheduled_loops([loop])
    schedule_path = tmp_path / "unified" / source.session_id / "scheduled-loops.json"

    # Do
    forked = await host.fork(
        SessionForkParams(source_session_id=source.session_id, attach=True)
    )

    # Assert
    assert not schedule_path.exists()
    assert forked.backend is not None
    assert [
        item.model_dump(exclude_none=True)
        for item in await cast(Any, forked.backend).scheduled_loops()
    ] == [loop.model_dump()]
    await host.shutdown()


@pytest.mark.asyncio
async def test_unified_fork_quarantines_a_corrupt_cold_source_loop_store(
    tmp_path: Path,
) -> None:
    """*Prepare*: A closed Unified session with a corrupt scheduled-loop file.
    *Do*: Fork it through a fresh backend Host.
    *Assert*: The history forks without loops and the invalid file is preserved.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    # Prepare
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    first_host = _harness_backend_host(config)
    started = await first_host.start(SessionStartParams())
    source_session_id = started.backend.session_id
    await started.backend.start_turn(
        TurnStartParams(
            session_id=source_session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await first_host.shutdown()
    schedule_path = tmp_path / "unified" / source_session_id / "scheduled-loops.json"
    schedule_path.write_text("not-json", encoding="utf-8")
    second_host = _harness_backend_host(config)

    # Do
    forked = await second_host.fork(
        SessionForkParams(source_session_id=source_session_id, attach=True)
    )
    assert forked.backend is not None
    subscription = await forked.backend.subscribe(
        SessionReadParams(session_id=forked.backend.session_id)
    )
    warning = await anext(subscription.events)

    # Assert
    assert await cast(Any, forked.backend).scheduled_loops() == []
    assert warning.method == "warning"
    assert isinstance(warning.params, ServerWarningParams)
    assert "could not be copied" in warning.params.warning.message
    quarantine_paths = list(schedule_path.parent.glob("scheduled-loops.corrupt-*.json"))
    assert len(quarantine_paths) == 1
    assert quarantine_paths[0].read_text(encoding="utf-8") == "not-json"
    assert not schedule_path.exists()
    await second_host.shutdown()


@pytest.mark.asyncio
async def test_unified_failed_scheduled_turn_advances_to_the_next_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An overdue loop whose scheduled turn fails permanently.
    *Do*: Let the scheduler process the failed attempt.
    *Assert*: The loop advances instead of retrying every scheduler tick.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    # Prepare
    session = cast(Any, _RecordingSession())
    notices: list[tuple[str, str]] = []
    session.publish_notice = lambda message, level="warning": notices.append((
        message,
        level,
    ))
    adapter = _inert_adapter(session, str(tmp_path), str(tmp_path))
    loop = ScheduledLoop(
        id="failed-loop",
        interval_seconds=30,
        prompt="cannot start",
        next_fire_at=0,
        created_at=0,
    )
    await adapter.replace_scheduled_loops([loop])
    attempted = asyncio.Event()
    original_mark_fired = adapter._scheduled_loops.mark_fired

    async def fail_start(_scheduled: ScheduledLoop) -> None:
        raise SessionBackendError(ProtocolErrorCode.INTERNAL_ERROR, "bad prompt")

    async def record_mark_fired(loop_id: str) -> ScheduledLoop | None:
        result = await original_mark_fired(loop_id)
        attempted.set()
        return result

    monkeypatch.setattr(adapter, "_start_scheduled_loop", fail_start)
    monkeypatch.setattr(adapter._scheduled_loops, "mark_fired", record_mark_fired)

    # Do
    task = asyncio.create_task(adapter._run_scheduled_loops())
    try:
        async with asyncio.timeout(1):
            await attempted.wait()
        advanced = await adapter.scheduled_loops()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    # Assert
    assert advanced[0].next_fire_at > time.time()
    assert notices == [("Scheduled loop failed: bad prompt", "error")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("schedule", "next_fire_at"),
    [
        ({"interval_seconds": 30}, 30),
        # 1970-01-01 00:00:00 UTC to the next 09:00 slot.
        ({"cron": "0 9 * * *"}, 9 * 3600),
    ],
)
async def test_unified_schedule_write_failure_defers_without_stalling_others(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    schedule: dict[str, object],
    next_fire_at: float,
) -> None:
    """*Prepare*: A fired loop whose next schedule cannot be persisted, and another due loop.
    *Do*: Handle the failed reschedule.
    *Assert*: The loop waits for its next occurrence without blocking the other one.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    # Prepare
    monkeypatch.setenv("TZ", "UTC")
    session = cast(Any, _RecordingSession())
    notices: list[tuple[str, str]] = []
    session.publish_notice = lambda message, level="warning": notices.append((
        message,
        level,
    ))
    adapter = _inert_adapter(session, str(tmp_path), str(tmp_path))
    loop = ScheduledPrompt.model_validate({
        "id": "unpersisted-loop",
        "prompt": "already ran",
        "next_fire_at": 0,
        "created_at": 0,
        **schedule,
    })
    other = ScheduledPrompt(
        id="other-loop",
        interval_seconds=60,
        prompt="also due",
        next_fire_at=0,
        created_at=0,
    )
    await adapter._scheduled_loops.replace([loop, other])
    sleeps: list[float] = []

    async def fail_mark_fired(_loop_id: str) -> None:
        raise ScheduledLoopStoreError("disk unavailable")

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(adapter._scheduled_loops, "mark_fired", fail_mark_fired)
    monkeypatch.setattr(asyncio, "sleep", record_sleep)
    monkeypatch.setattr(time, "time", lambda: 0.0)

    # Do
    await adapter._mark_scheduled_loop_attempted(loop)

    # Assert
    loops = {item.id: item for item in await adapter._scheduled_loops.list()}
    assert loops[loop.id].next_fire_at == next_fire_at
    due = await adapter._scheduled_loops.due(now=0)
    assert due is not None and due.id == other.id
    assert sleeps == []
    assert notices == [
        ("Scheduled loop could not be rescheduled: disk unavailable", "error")
    ]


@pytest.mark.asyncio
async def test_unified_turn_survives_a_schedule_flush_failure_after_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An ephemeral session with an in-memory loop and a failing store.
    *Do*: Start the first turn, which promotes the Harness session.
    *Assert*: The accepted turn is returned and the persistence failure is reported.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    # Prepare
    session = cast(Any, _RecordingSession())
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session.ephemeral = True
    notices: list[tuple[str, str]] = []
    session.publish_notice = lambda message, level="warning": notices.append((
        message,
        level,
    ))
    original_start_turn = session.start_turn

    async def promote_and_start(params: Any) -> Any:
        result = await original_start_turn(params)
        session.ephemeral = False
        return result

    session.start_turn = promote_and_start
    adapter, _ = await _unified_adapter_with_real_context(tmp_path, session)
    await adapter._scheduled_loops.create("30s", "keep me")

    async def fail_persist() -> None:
        raise ScheduledLoopStoreError("disk unavailable")

    monkeypatch.setattr(adapter._scheduled_loops, "persist", fail_persist)

    # Do
    result = await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id,
            message=[TextContentBlock(text="persist the session")],
        )
    )

    # Assert
    assert result.response.turn.id == "turn-1"
    assert notices == [
        (
            "The session was saved, but its scheduled loops could not be saved. "
            "They remain active until this session closes.",
            "warning",
        )
    ]


@pytest.mark.asyncio
async def test_unified_harness_start_returns_distinct_session_identities() -> None:
    host = _harness_backend_host()

    first = await host.start(SessionStartParams())
    second = await host.start(SessionStartParams())
    await host.shutdown()

    assert host.harness_kind == "unified"
    assert first.backend.session_id != second.backend.session_id


@pytest.mark.asyncio
async def test_unified_host_shutdown_releases_worktree_holders_before_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: Two live sessions in separate directories.
    *Do*: Shut the Unified Host down.
    *Assert*: Every worktree holder is released before the host stops.
    """
    # Prepare
    host = cast(Any, _harness_backend_host())
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()
    await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(first_cwd)))
    )
    await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(second_cwd)))
    )
    order: list[str] = []
    underlying_shutdown = host._host.shutdown

    def release(_cwd: Path, _session_id: str) -> None:
        order.append("release")

    async def shutdown() -> None:
        order.append("shutdown")
        await underlying_shutdown()

    monkeypatch.setattr(host._worktrees, "release", release)
    monkeypatch.setattr(host._host, "shutdown", shutdown)

    # Do
    await host.shutdown()

    # Assert
    assert order == ["release", "release", "shutdown"]


@pytest.mark.asyncio
async def test_unified_host_shutdown_leaves_a_requested_worktree_reap_to_the_reap_request(
    tmp_path: Path,
) -> None:
    """*Prepare*: A live session in its own managed worktree, archived with a
    reap request that the session still blocks.
    *Do*: Shut the Unified Host down, then request the reap again.
    *Assert*: Shutdown releases the worktree without removing it, and the
    repeated request removes it.
    """
    # Prepare
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repo = Repo.init(repo_root, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (repo_root / "file.txt").write_text("initial\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    host = cast(Any, _harness_backend_host())
    started = await host.start(
        SessionStartParams(
            agent_config=SessionOptions(
                cwd=str(repo_root),
                workspace_roots=[str(repo_root)],
                worktree=NewWorktreeInput(name="archived", branch="vibe/archived"),
            )
        )
    )
    session = cast(Any, started.backend)
    await session._await_deferred_setup()
    worktree_root = Path(session.cwd)
    managed = ManagedWorktree.at(worktree_root)
    assert managed is not None
    assert managed.reap().outcome is WorktreeReleaseOutcome.KEPT_IN_USE

    # Do
    await host.shutdown()

    # Assert
    # Removing a large worktree takes minutes, and a client waiting on the
    # close cannot tell that from a stuck close.
    assert worktree_root.is_dir()

    # Do
    completed = managed.reap()

    # Assert
    assert completed.outcome is WorktreeReleaseOutcome.REMOVED
    assert not worktree_root.exists()


@pytest.mark.asyncio
async def test_unified_host_shutdown_releases_the_archived_sessions_moved_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A live session moved to a worktree, then archived.
    *Do*: Shut down the Unified Host after an archive reap was requested.
    *Assert*: The release targets the session's adopted worktree.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    host = cast(Any, _harness_backend_host(config))
    original_cwd = tmp_path / "original"
    moved_cwd = tmp_path / "moved"
    original_cwd.mkdir()
    moved_cwd.mkdir()
    started = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(original_cwd)))
    )
    backend = cast(Any, started.backend)
    moved_session = _RecordingSession()
    moved_backend, _ = await _unified_adapter_with_real_context(
        moved_cwd, moved_session
    )
    host._lifecycle_context = AsyncMock(return_value=(moved_backend._context, object()))
    await host._move_session(backend, SessionOptions(cwd=str(moved_cwd)))
    archived = await host.archive(
        SessionArchiveParams(session_id=backend.session_id, archived=True)
    )
    released: list[tuple[Path, str]] = []

    def release(cwd: Path, session_id: str) -> None:
        released.append((cwd, session_id))

    monkeypatch.setattr(host._worktrees, "release", release)

    # Do
    await host.shutdown()

    # Assert
    adopted_cwd = moved_cwd.resolve()
    assert archived.archived_at is not None
    assert backend.cwd == str(adopted_cwd)
    assert released == [(adopted_cwd, backend.session_id)]


@pytest.mark.asyncio
async def test_unified_harness_start_projects_mcp_servers() -> None:
    config = build_test_vibe_config(
        mcp_servers=[
            MCPStdio(name="local", transport="stdio", command="fake-mcp", disabled=True)
        ]
    )
    host = _harness_backend_host(config)

    try:
        started = await host.start(SessionStartParams())

        assert isinstance(started.backend, SessionBackendRuntimeView)
        sources = started.backend.runtime_updated_params().runtime.mcp.sources
        assert [(source.name, source.status) for source in sources] == [
            ("local", MCPSourceStatus.DISABLED)
        ]
    finally:
        await host.shutdown()


@pytest.mark.asyncio
async def test_clear_preserves_compiled_hook_bindings_and_handlers(
    tmp_path: Path,
) -> None:
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe import (
        ForeignHookDefinition,
        compile_foreign_hooks,
    )
    from mistralai_vibe_local_harness.vibe._storage import UnifiedSessionStore
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    compiled = compile_foreign_hooks(
        [
            ForeignHookDefinition(
                name="block", point="pre_tool", command="true", source="project"
            )
        ],
        tool_catalog=lambda: [],
    )
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    host = adapt_harness_host(
        vibe_runtime.create_harness_host(),
        _test_session_runtime_builder(config, hooks=compiled),
    )
    started = await host.start(SessionStartParams())
    cleared = await host.clear_history(
        started.backend,
        SessionHistoryClearParams(session_id=started.backend.session_id),
    )
    replacement = cast(Any, cleared.backend)
    action_adapter = replacement._session._action_adapter
    assert action_adapter is not None
    assert set(compiled.handlers.pre_tool_call).issubset(
        action_adapter._hook_handlers.pre_tool_call
    )
    await replacement.start_turn(
        TurnStartParams(
            session_id=replacement.session_id, message=[TextContentBlock(text="hello")]
        )
    )
    replacement_id = replacement.session_id
    await host.shutdown()

    stored = UnifiedSessionStore(tmp_path, replacement_id).load()
    assert [
        binding.id for binding in stored.runtime_state.session_metadata.hook_bindings
    ] == [binding.id for binding in compiled.bindings]


@pytest.mark.asyncio
async def test_clear_preserves_resolved_narration_metadata() -> None:
    host = cast(Any, _harness_backend_host())
    started = await host.start(SessionStartParams())
    source = cast(Any, started.backend)
    launch_context = LaunchContext(
        agent_entrypoint="cli",
        agent_version="0",
        client_name="test-client",
        client_version="0",
    )
    source._launch_context = launch_context
    source._user_plan = "Pro"

    cleared = await host.clear_history(
        source, SessionHistoryClearParams(session_id=source.session_id)
    )

    replacement = cast(Any, cleared.backend)
    assert replacement._launch_context is launch_context
    assert replacement._user_plan == "Pro"
    await host.shutdown()


@pytest.mark.asyncio
async def test_clear_starts_an_unarchived_session(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    host = _harness_backend_host(config)
    started = await host.start(SessionStartParams())
    assert isinstance(host, SessionBackendHostArchive)
    assert isinstance(host, SessionBackendHistoryClearHost)
    archived = await host.archive(
        SessionArchiveParams(session_id=started.backend.session_id, archived=True)
    )

    cleared = await host.clear_history(
        started.backend,
        SessionHistoryClearParams(session_id=started.backend.session_id),
    )

    assert archived.archived_at is not None
    assert cleared.state.session.archived_at is None
    await host.shutdown()


def _shell_effect_entry(output: Any, output_text: str) -> Any:
    from vibe.app_server.models import (
        CompletedEffectState,
        EffectCallDisplay,
        EffectResultDisplay,
        PublicEffectEntry,
        PublicEntryGenerationStatus,
        ShellEffectDetail,
        ShellEffectInput,
    )

    return PublicEffectEntry(
        id="effect-1",
        session_id="s",
        turn_id="t",
        created_at=0,
        updated_at=0,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        title="bash",
        detail=ShellEffectDetail(
            tool_name="bash",
            display=EffectCallDisplay(
                summary="bash: echo plop", status_text="Running echo plop"
            ),
            input=ShellEffectInput(command="echo plop"),
        ),
        state=CompletedEffectState(
            output=output,
            output_text=output_text,
            display=EffectResultDisplay(success=True, message="echo plop"),
        ),
    )


def test_normalize_effect_output_degrades_a_hook_replaced_result() -> None:
    # A post_tool deny replaces a bash result with content-only output (no stdout/stderr).
    # The Harness snapshot carries that as the raw RustToolResult wire shape, which no
    # typed client can parse. _normalize_effect_output must re-project it to None ("no
    # structured output"); the reason survives in output_text.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        _normalize_effect_output,
    )

    entry = _shell_effect_entry(
        output={
            "type": "success",
            "content": [{"type": "text", "text": "Output blocked by deny-plop."}],
        },
        output_text="Output blocked by deny-plop.",
    )

    normalized = _normalize_effect_output(entry)

    assert isinstance(normalized, PublicEffectEntry)
    assert isinstance(normalized.state, CompletedEffectState)
    assert normalized.state.output is None
    assert normalized.state.output_text == "Output blocked by deny-plop."


def test_normalize_effect_output_leaves_a_native_shell_output_unchanged() -> None:
    # A normal bash result already matches ShellEffectOutput; re-projection is idempotent.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        _normalize_effect_output,
    )

    entry = _shell_effect_entry(
        output={"stdout": "hi\n", "stderr": "", "output": "", "truncated": False},
        output_text="hi\n",
    )

    normalized = _normalize_effect_output(entry)

    assert isinstance(normalized, PublicEffectEntry)
    assert isinstance(normalized.state, CompletedEffectState)
    assert normalized.state.output == {
        "stdout": "hi\n",
        "stderr": "",
        "output": "",
        "truncated": False,
    }


@pytest.mark.asyncio
async def test_resume_compiles_hooks_against_the_session_cwd(tmp_path: Path) -> None:
    # A resumed session runs in its stored cwd, so its hooks must be discovered and
    # compiled against that cwd -- not the caller's invocation cwd. Regression guard for
    # binding-id/handler mismatch (crash-resume/fork skip every hook) and clean-resume
    # binding the Core to the wrong project's hooks.
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    inner = _test_session_runtime_builder(config)
    seen_cwds: list[str | None] = []

    async def recording(
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: Any = "cli",
    ) -> Any:
        seen_cwds.append(options.cwd)
        return await inner(
            options, require_api_key=require_api_key, entrypoint=entrypoint
        )

    project = tmp_path / "project"
    project.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    host = adapt_harness_host(vibe_runtime.create_harness_host(), recording)
    started = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(project)))
    )
    session_id = started.backend.session_id

    await host.resume(
        SessionResumeParams(
            session_id=session_id, agent_config=SessionOptions(cwd=str(elsewhere))
        )
    )
    await host.shutdown()

    # Resume built its hook context against the session's stored (resolved project) cwd,
    # never the caller's `elsewhere`.
    assert seen_cwds[-1] == str(project.resolve())
    assert seen_cwds[-1] != str(elsewhere)


@pytest.mark.asyncio
async def test_pin_releases_pending_worktree_hold_when_context_rebuild_fails(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendHostAdapter,
    )
    from vibe.app_server._worktree_session import WorktreeResolution

    stored_cwd = str(tmp_path / "stored")

    harness = Mock()
    harness.session_cwd = AsyncMock(return_value=stored_cwd)
    host = UnifiedHarnessBackendHostAdapter(
        cast(Any, harness), AsyncMock(side_effect=RuntimeError("context failed"))
    )
    pending_hold = Mock()
    resolution = WorktreeResolution(
        options=SessionOptions(cwd=stored_cwd), pending_hold=cast(Any, pending_hold)
    )
    cast(Any, host._worktrees).resolve_for_start = AsyncMock(return_value=resolution)

    with pytest.raises(RuntimeError, match="context failed"):
        await host._pin_for_resume(
            SessionOptions(cwd=str(tmp_path / "requested")),
            "session-1",
            cast(Any, object()),
            cast(Any, object()),
            require_api_key=True,
        )

    pending_hold.release.assert_called_once_with()


@pytest.mark.asyncio
async def test_fork_releases_pending_worktree_hold_when_open_fails() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendHostAdapter,
    )
    from vibe.app_server._worktree_session import WorktreeResolution

    harness = Mock()
    harness.fork = AsyncMock(side_effect=RuntimeError("fork failed"))
    host = UnifiedHarnessBackendHostAdapter(cast(Any, harness), AsyncMock())
    context = Mock()
    context.hooks.handlers = ()
    derivation = Mock()
    pending_hold = Mock()
    resolution = WorktreeResolution(
        options=SessionOptions(), pending_hold=cast(Any, pending_hold)
    )
    raw_host = cast(Any, host)
    raw_host._lifecycle_context = AsyncMock(return_value=(context, derivation))
    raw_host._pin_for_fork = AsyncMock(return_value=(context, derivation, resolution))

    with pytest.raises(RuntimeError, match="fork failed"):
        await host.fork(SessionForkParams(source_session_id="session-1"))

    pending_hold.release.assert_called_once_with()


@pytest.mark.asyncio
async def test_fork_rejects_a_new_worktree_request() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendHostAdapter,
    )

    harness = Mock()
    host = UnifiedHarnessBackendHostAdapter(cast(Any, harness), AsyncMock())

    with pytest.raises(RequestFailure, match="only supported when starting"):
        await host.fork(
            SessionForkParams(
                source_session_id="session-1",
                agent_config=SessionOptions(
                    worktree=AutoWorktreeInput(prompt="create another checkout")
                ),
            )
        )

    harness.fork.assert_not_called()


@pytest.mark.asyncio
async def test_resume_restores_the_stored_worktree_before_building_its_context(
    tmp_path: Path,
) -> None:
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    project = tmp_path / "project"
    project.mkdir()
    events: list[tuple[str, Path]] = []

    inner = _test_session_runtime_builder(config)

    async def recording(
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: Any = "cli",
    ) -> Any:
        events.append(("build", Path(options.cwd or Path.cwd()).resolve()))
        return await inner(
            options, require_api_key=require_api_key, entrypoint=entrypoint
        )

    host = adapt_harness_host(vibe_runtime.create_harness_host(), recording)
    started = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(project)))
    )
    events.clear()

    async def restore(cwd: Path) -> bool:
        events.append(("restore", cwd))
        return True

    cast(Any, host)._worktrees.restore = restore

    await host.resume(
        SessionResumeParams(
            session_id=started.backend.session_id,
            agent_config=SessionOptions(cwd=str(project)),
        )
    )
    await host.shutdown()

    assert events == [
        ("build", project.resolve()),
        ("restore", project.resolve()),
        ("build", project.resolve()),
    ]


def test_foreign_hook_definitions_preserve_a_zero_timeout(tmp_path: Path) -> None:
    # A configured timeout of 0 is an explicit fast-fail; it must not be coerced to the
    # 60s default (the `or 60.0` footgun).
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import _foreign_hook_definitions
    from vibe.core.config.harness_files import HarnessFilesManager
    from vibe.core.hooks.models import HookConfig, HookConfigResult, HookType

    result = HookConfigResult(
        hooks=[
            HookConfig(
                name="guard", type=HookType.PRE_TOOL, command="true", timeout=0.0
            )
        ],
        issues=[],
    )
    harness_files = HarnessFilesManager(sources=("project",)).for_session(tmp_path)
    definitions = _foreign_hook_definitions(
        result, harness_files=harness_files, cwd=tmp_path
    )
    assert definitions[0].timeout_s == 0.0


def test_user_hooks_are_labelled_user_not_the_session_cwd(tmp_path: Path) -> None:
    # A ~/.vibe hook's binding id is scoped to "user", not the session cwd, so it stays
    # distinct from project bindings.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import _foreign_hook_definitions
    from vibe.core.config.harness_files import HarnessFilesManager
    from vibe.core.hooks.config import load_hooks_from_fs
    from vibe.core.paths import VIBE_HOME

    (tmp_path / ".vibe").mkdir()
    (tmp_path / ".vibe" / "hooks.toml").write_text(
        '[[hooks]]\nname = "proj-only"\ntype = "pre_tool"\ncommand = "true"\n'
    )
    VIBE_HOME.path.mkdir(parents=True, exist_ok=True)
    (VIBE_HOME.path / "hooks.toml").write_text(
        '[[hooks]]\nname = "user-only"\ntype = "pre_tool"\ncommand = "true"\n'
    )
    harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
        tmp_path
    )
    harness_files.trust_store.trust_for_session(tmp_path)

    result = load_hooks_from_fs(harness_files=harness_files)
    sources = {
        d.name: d.source
        for d in _foreign_hook_definitions(
            result, harness_files=harness_files, cwd=tmp_path
        )
    }
    assert sources == {"proj-only": str(tmp_path), "user-only": "user"}


def test_user_hook_keeps_user_source_when_project_trust_is_lost(tmp_path: Path) -> None:
    # Regression: on a resume where project trust is gone, only the user hook survives.
    # It must stay "user"-scoped so it cannot reuse a persisted project binding id and run
    # for a different project's hook.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import _foreign_hook_definitions
    from vibe.core.config.harness_files import HarnessFilesManager
    from vibe.core.hooks.config import load_hooks_from_fs
    from vibe.core.paths import VIBE_HOME

    (tmp_path / ".vibe").mkdir()
    (tmp_path / ".vibe" / "hooks.toml").write_text(
        '[[hooks]]\nname = "guard"\ntype = "pre_tool"\ncommand = "project"\n'
    )
    VIBE_HOME.path.mkdir(parents=True, exist_ok=True)
    (VIBE_HOME.path / "hooks.toml").write_text(
        '[[hooks]]\nname = "guard"\ntype = "pre_tool"\ncommand = "user"\n'
    )
    # No trust_for_session: the project cwd is untrusted, so only the user hook survives.
    harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
        tmp_path
    )

    result = load_hooks_from_fs(harness_files=harness_files)
    assert [hook.command for hook in result.hooks] == ["user"]

    definitions = _foreign_hook_definitions(
        result, harness_files=harness_files, cwd=tmp_path
    )
    assert definitions[0].source == "user"


@pytest.mark.asyncio
async def test_unified_runtime_counts_the_hooks_the_session_compiled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a trusted workspace declaring two hooks.

    Do derive the runtime the client observes.

    Assert it reports both. The banner reads ``hooks_count`` off this snapshot,
    so a hard-coded zero tells the user nothing is intercepting their tools
    while the bindings compiled from these same files are doing exactly that.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    (tmp_path / ".vibe").mkdir()
    (tmp_path / ".vibe" / "hooks.toml").write_text(
        '[[hooks]]\nname = "guard"\ntype = "pre_tool"\ncommand = "true"\n'
        '[[hooks]]\nname = "audit"\ntype = "post_tool"\ncommand = "true"\n'
    )
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)

    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), trust_workspace=True)
    )
    derivation = context.derive(UnifiedSessionSettings())

    assert derivation.runtime.hooks_count == 2
    # The count stands for hooks that actually bound, not files that parsed —
    # and it counts the *user's* hooks only. The builtin lazy AGENTS.md
    # injection binds too, but its handler rides the Host-global registry.
    assert [binding.id.rsplit(":", 1)[-1] for binding in context.hooks.bindings] == [
        "guard",
        "audit",
        "agents_md",
    ]


@pytest.mark.asyncio
async def test_resume_trust_does_not_leak_to_a_descendant_session_cwd(
    tmp_path: Path,
) -> None:
    # --trust is scoped to the caller's invocation cwd. On a cross-dir resume the caller
    # cwd is an ancestor of the stored session cwd, and the trust store's ancestor walk
    # would otherwise auto-trust (and auto-run the hooks.toml of) that descendant project.
    # The ephemeral grant the first build recorded for the caller cwd must be revoked.
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host
    from vibe.core.trusted_folders import trusted_folders_manager

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path / "store")
        )
    )
    inner = _test_session_runtime_builder(config)

    async def recording(
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: Any = "cli",
    ) -> Any:
        context = await inner(
            options, require_api_key=require_api_key, entrypoint=entrypoint
        )
        # Mirror _build_session_config, which records the ephemeral --trust grant.
        if options.trust_workspace and options.cwd is not None:
            context.harness_files.trust_store.trust_for_session(Path(options.cwd))
        return context

    project = tmp_path / "parent" / "project"
    project.mkdir(parents=True)
    caller = tmp_path / "parent"  # an ancestor of the stored session cwd

    host = adapt_harness_host(vibe_runtime.create_harness_host(), recording)
    started = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(project)))
    )
    session_id = started.backend.session_id

    await host.resume(
        SessionResumeParams(
            session_id=session_id,
            agent_config=SessionOptions(cwd=str(caller), trust_workspace=True),
        )
    )
    await host.shutdown()

    assert trusted_folders_manager.is_trusted(project) is not True


def test_hooks_toml_on_disk_compiles_to_bindings(tmp_path: Path) -> None:
    # Discovery -> mapping -> compile, the exact chain build_unified_session_context
    # runs. Regression guard for the bug where the mapping read result.runtime_hooks
    # (which the fs loader never populates) instead of result.hooks.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe import compile_foreign_hooks
    from vibe.app_server._runtime import _foreign_hook_definitions
    from vibe.core.config.harness_files import HarnessFilesManager
    from vibe.core.hooks.config import load_hooks_from_fs

    vibe_dir = tmp_path / ".vibe"
    vibe_dir.mkdir()
    (vibe_dir / "hooks.toml").write_text(
        "[[hooks]]\n"
        'name = "pre"\n'
        'type = "pre_tool"\n'
        'command = "true"\n\n'
        "[[hooks]]\n"
        'name = "post"\n'
        'type = "post_tool"\n'
        'command = "true"\n\n'
        "[[hooks]]\n"
        'name = "agent"\n'
        'type = "post_agent"\n'
        'command = "true"\n'
    )
    harness_files = HarnessFilesManager(sources=("project",)).for_session(tmp_path)
    harness_files.trust_store.trust_for_session(tmp_path)

    result = load_hooks_from_fs(harness_files=harness_files)
    assert [hook.name for hook in result.hooks] == ["pre", "post", "agent"]

    definitions = _foreign_hook_definitions(
        result, harness_files=harness_files, cwd=tmp_path
    )
    assert {definition.point for definition in definitions} == {
        "pre_tool",
        "post_tool",
        "post_agent",
    }
    # The binding source is the session cwd, not a shared constant, so two projects
    # declaring a same-named hook compile to distinct binding ids (no clobber).
    assert {definition.source for definition in definitions} == {str(tmp_path)}

    compiled = compile_foreign_hooks(definitions, tool_catalog=lambda: [])
    assert len(compiled.bindings) == 3
    assert len(compiled.handlers.pre_tool_call) == 1
    assert len(compiled.handlers.post_tool_call) == 1
    assert len(compiled.handlers.post_agent_turn) == 1


def test_untrusted_workspace_yields_no_hooks(tmp_path: Path) -> None:
    # Trust boundary: a project hooks.toml is ignored unless the cwd is trusted, so an
    # untrusted workspace compiles to no bindings even though the file exists on disk.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe import compile_foreign_hooks
    from vibe.app_server._runtime import _foreign_hook_definitions
    from vibe.core.config.harness_files import HarnessFilesManager
    from vibe.core.hooks.config import load_hooks_from_fs

    vibe_dir = tmp_path / ".vibe"
    vibe_dir.mkdir()
    (vibe_dir / "hooks.toml").write_text(
        '[[hooks]]\nname = "pre"\ntype = "pre_tool"\ncommand = "true"\n'
    )
    # No trust_for_session: the project source stays untrusted.
    harness_files = HarnessFilesManager(sources=("project",)).for_session(tmp_path)

    result = load_hooks_from_fs(harness_files=harness_files)
    assert result.hooks == []

    compiled = compile_foreign_hooks(
        _foreign_hook_definitions(result, harness_files=harness_files, cwd=tmp_path),
        tool_catalog=lambda: [],
    )
    assert compiled.bindings == ()


def test_invalid_hooks_toml_surfaces_a_config_issue(tmp_path: Path) -> None:
    # A malformed hooks.toml is skipped by the loader, but the diagnostic must not vanish:
    # it is projected onto the session's issues (legacy parity + design failure table),
    # not dropped silently.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    from vibe.app_server._runtime import _hook_config_issues
    from vibe.core.config.harness_files import HarnessFilesManager
    from vibe.core.hooks.config import load_hooks_from_fs

    vibe_dir = tmp_path / ".vibe"
    vibe_dir.mkdir()
    (vibe_dir / "hooks.toml").write_text("this is not valid toml [[[")
    harness_files = HarnessFilesManager(sources=("project",)).for_session(tmp_path)
    harness_files.trust_store.trust_for_session(tmp_path)

    result = load_hooks_from_fs(harness_files=harness_files)
    assert result.hooks == []
    assert result.issues  # the loader recorded a parse diagnostic

    issues = _hook_config_issues(result)
    assert len(issues) == len(result.issues)
    assert all(issue.message for issue in issues)


@pytest.mark.asyncio
async def test_unified_harness_clear_replaces_the_attached_session() -> None:
    """*Prepare*: Start a Unified Harness session without running a turn.
    *Do*: Clear its history to replace the attached session.
    *Assert*: The empty replacement has a new identity but is not persisted yet.
    """
    # Prepare
    client, server = _connect_harness_host()

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )

        # Do
        cleared = SessionHistoryClearResponse.model_validate(
            await client.request(
                "session/history/clear",
                SessionHistoryClearParams(session_id=started.state.session.id),
            )
        )
        await client.request("plugin/reload", {"sessionId": cleared.state.session.id})
        listed = SessionListResponse.model_validate(
            await client.request("session/list", SessionListParams())
        )
    finally:
        await server.close()

    # Assert
    assert cleared.state.session.id != started.state.session.id
    assert len(cleared.state.history or []) == 1
    checkpoint = (cleared.state.history or [])[0]
    assert isinstance(checkpoint, PublicCheckpointEntry)
    assert checkpoint.kind == "clear"
    assert checkpoint.session_id == cleared.state.session.id
    assert cleared.session_log.session_id == cleared.state.session.id
    assert cleared.session_log.persisted is False
    assert cleared.session_log.path is None
    assert all(item.id != cleared.state.session.id for item in listed.items)


@pytest.mark.asyncio
async def test_unified_harness_discards_unused_ephemeral_sessions() -> None:
    host = _harness_backend_host()
    started = await host.start(SessionStartParams(kind=SessionKind.EPHEMERAL))

    listed_while_open = await host.list(SessionListParams())
    await started.backend.shutdown()
    listed_after_shutdown = await host.list(SessionListParams())
    await host.shutdown()

    assert listed_while_open.items == []
    assert listed_after_shutdown.items == []


@pytest.mark.asyncio
async def test_unified_resume_discards_the_replaced_ephemeral_session(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    host = _harness_backend_host(config)
    persisted = await host.start(SessionStartParams())
    persisted_id = persisted.backend.session_id
    await persisted.backend.start_turn(
        TurnStartParams(
            session_id=persisted_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await host.shutdown()
    client, server = _connect_harness_host(config)

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        ephemeral = SessionReadResponse.model_validate(
            await client.request(
                "session/start", SessionStartParams(kind=SessionKind.EPHEMERAL)
            )
        )
        ephemeral_id = ephemeral.state.session.id
        await client.request(
            "session/resume", SessionResumeParams(session_id=persisted_id)
        )
        with SessionLease(tmp_path, ephemeral_id):
            pass
        assert not (tmp_path / "unified" / ephemeral_id).exists()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_unified_harness_resume_and_list_use_the_persisted_store(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    first_host = _harness_backend_host(config)
    started = await first_host.start(SessionStartParams())
    session_id = started.backend.session_id
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await first_host.shutdown()

    second_host = _harness_backend_host(config)
    resumed = await second_host.resume(SessionResumeParams(session_id=session_id))
    listed = await second_host.list(SessionListParams())
    await second_host.shutdown()

    assert resumed.backend.session_id == session_id
    assert isinstance(resumed.backend, SessionBackendRuntimeView)
    assert resumed.backend.runtime_updated_params().session_id == session_id
    assert [session.id for session in listed.items] == [session_id]
    assert listed.continue_session_id == session_id


@pytest.mark.asyncio
async def test_unified_promotion_writes_vibe_session_metadata(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    project_cwd = str((tmp_path / "project").resolve())
    (tmp_path / "project").mkdir()
    host = _harness_backend_host(config)
    started = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    session_id = started.backend.session_id
    meta_path = tmp_path / "unified" / session_id / "meta.json"

    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await host.shutdown()

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    assert metadata["session_id"] == session_id
    assert metadata["parent_session_id"] is None
    assert metadata["environment"]["working_directory"] == project_cwd
    assert metadata["origin_directory"] == project_cwd
    assert metadata["title_source"] == "auto"
    datetime.fromisoformat(metadata["bumped_at"])
    datetime.fromisoformat(metadata["start_time"])
    datetime.fromisoformat(metadata["end_time"])


@pytest.mark.asyncio
async def test_unified_accepted_turn_persists_and_projects_bumped_at(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    project_cwd = str((tmp_path / "project").resolve())
    (tmp_path / "project").mkdir()
    first_host = _harness_backend_host(config)
    started = await first_host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    session_id = started.backend.session_id
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )

    await first_host.shutdown()

    second_host = _harness_backend_host(config)
    renamed = await second_host.rename(
        SessionTitleUpdateParams(session_id=session_id, title="Manual title")
    )

    meta_path = tmp_path / "unified" / session_id / "meta.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    assert renamed.title == "Manual title"
    assert metadata["title"] == "Manual title"
    assert metadata["title_source"] == "manual"
    bumped_at = int(datetime.fromisoformat(metadata["bumped_at"]).timestamp() * 1000)

    read = await second_host.read(SessionReadParams(session_id=session_id))
    listed = await second_host.list(SessionListParams(cwd=project_cwd))
    await second_host.shutdown()

    assert read.state.session.bumped_at == bumped_at
    rows = {item.id: item for item in listed.items}
    assert rows[session_id].bumped_at == bumped_at


@pytest.mark.asyncio
async def test_unified_pin_is_available_through_the_app_server(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    client, server = _connect_harness_host(config)
    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        session_id = started.state.session.id
        pinned = SessionPinResponse.model_validate(
            await client.request(
                "session/pin", SessionPinParams(session_id=session_id, pinned=True)
            )
        )
        read = SessionReadResponse.model_validate(
            await client.request(
                "session/read", SessionReadParams(session_id=session_id)
            )
        )
        assert pinned.pinned_at is not None
        assert read.state.session.pinned_at == pinned.pinned_at
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_unified_archive_is_available_through_the_app_server(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    client, server = _connect_harness_host(config)
    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        session_id = started.state.session.id
        pinned = SessionPinResponse.model_validate(
            await client.request(
                "session/pin", SessionPinParams(session_id=session_id, pinned=True)
            )
        )
        archived = SessionArchiveResponse.model_validate(
            await client.request(
                "session/archive",
                SessionArchiveParams(session_id=session_id, archived=True),
            )
        )
        read = SessionReadResponse.model_validate(
            await client.request(
                "session/read", SessionReadParams(session_id=session_id)
            )
        )
        unarchived = SessionArchiveResponse.model_validate(
            await client.request(
                "session/archive",
                SessionArchiveParams(session_id=session_id, archived=False),
            )
        )
        reread = SessionReadResponse.model_validate(
            await client.request(
                "session/read", SessionReadParams(session_id=session_id)
            )
        )

        assert pinned.pinned_at is not None
        assert archived.archived_at is not None
        assert read.state.session.archived_at == archived.archived_at
        assert read.state.session.pinned_at is None
        assert unarchived.archived_at is None
        assert reread.state.session.archived_at is None
        assert reread.state.session.pinned_at is None
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_unified_pin_persists_to_the_sidecar_and_survives_a_restart(
    tmp_path: Path,
) -> None:
    """The Harness has no pin, so the whole of it lives in Vibe's sidecar."""
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    project_cwd = str((tmp_path / "project").resolve())
    (tmp_path / "project").mkdir()
    first_host = _harness_backend_host(config)
    started = await first_host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    session_id = started.backend.session_id
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )

    assert isinstance(first_host, SessionBackendHostPin)
    attached = await first_host.pin(
        SessionPinParams(session_id=session_id, pinned=True)
    )
    repeated = await first_host.pin(
        SessionPinParams(session_id=session_id, pinned=True)
    )
    await first_host.shutdown()

    assert attached.pinned_at is not None
    # Re-pinning keeps the first pin, so the pinned shelf keeps its order.
    assert repeated.pinned_at == attached.pinned_at

    meta_path = tmp_path / "unified" / session_id / "meta.json"
    persisted = json.loads(meta_path.read_text(encoding="utf-8"))["pinned_at"]
    assert (
        int(datetime.fromisoformat(persisted).timestamp() * 1000) == attached.pinned_at
    )

    second_host = _harness_backend_host(config)
    read = await second_host.read(SessionReadParams(session_id=session_id))
    pinned_only = await second_host.list(
        SessionListParams(cwd=project_cwd, pinned=True)
    )
    unpinned_only = await second_host.list(
        SessionListParams(cwd=project_cwd, pinned=False)
    )
    # Detached this time: the session is closed, so the host rewrites the
    # sidecar in place rather than going through the session's adapter.
    assert isinstance(second_host, SessionBackendHostPin)
    detached = await second_host.pin(
        SessionPinParams(session_id=session_id, pinned=False)
    )
    after_unpin = await second_host.list(
        SessionListParams(cwd=project_cwd, pinned=True)
    )
    await second_host.shutdown()

    assert read.state.session.pinned_at == attached.pinned_at
    assert [item.id for item in pinned_only.items] == [session_id]
    assert [item.id for item in unpinned_only.items] == []
    assert detached.pinned_at is None
    assert after_unpin.items == []
    assert json.loads(meta_path.read_text(encoding="utf-8"))["pinned_at"] is None


@pytest.mark.asyncio
async def test_unified_pin_after_adapter_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    host = _harness_backend_host(config)
    try:
        started = await host.start(
            SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path)))
        )
        session_id = started.backend.session_id
        await started.backend.start_turn(
            TurnStartParams(
                session_id=session_id, message=[TextContentBlock(text="persist me")]
            )
        )
        await started.backend.shutdown()

        async def unexpected_persist(**_kwargs: object) -> None:
            pytest.fail("pin attempted to persist through a closed adapter")

        monkeypatch.setattr(
            started.backend, "_persist_session_metadata", unexpected_persist
        )
        assert isinstance(host, SessionBackendHostPin)
        pinned = await host.pin(SessionPinParams(session_id=session_id, pinned=True))
        assert pinned.pinned_at is not None
        read = await host.read(SessionReadParams(session_id=session_id))
        assert read.state.session.pinned_at == pinned.pinned_at
        unpinned = await host.pin(SessionPinParams(session_id=session_id, pinned=False))
        assert unpinned.pinned_at is None
        read = await host.read(SessionReadParams(session_id=session_id))
        assert read.state.session.pinned_at is None
    finally:
        await host.shutdown()


async def _stored_unified_session(config: VibeConfigSchema, cwd: Path) -> str:
    owner = _harness_backend_host(config)
    try:
        started = await owner.start(
            SessionStartParams(agent_config=SessionOptions(cwd=str(cwd)))
        )
        session_id = started.backend.session_id
        await started.backend.start_turn(
            TurnStartParams(
                session_id=session_id, message=[TextContentBlock(text="persist me")]
            )
        )
        await started.backend.shutdown()
    finally:
        await owner.shutdown()
    return session_id


@pytest.mark.asyncio
async def test_unified_read_reports_which_process_holds_the_session(
    tmp_path: Path,
) -> None:
    from mistralai_vibe_local_harness.vibe._storage import SessionLease as HarnessLease

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    session_id = await _stored_unified_session(config, tmp_path)
    reader = _harness_backend_host(config)
    try:
        free = await reader.read(SessionReadParams(session_id=session_id))
        with HarnessLease(tmp_path, session_id):
            held = await reader.read(SessionReadParams(session_id=session_id))
    finally:
        await reader.shutdown()

    owner = _harness_backend_host(config)
    try:
        resumed = await owner.resume(
            SessionResumeParams(
                session_id=session_id, agent_config=SessionOptions(cwd=str(tmp_path))
            )
        )
        running = await owner.read(SessionReadParams(session_id=session_id))
        await resumed.backend.shutdown()
    finally:
        await owner.shutdown()

    assert free.lease_holder is None
    assert held.model_dump(mode="json")["leaseHolder"] == {"processId": os.getpid()}
    assert running.lease_holder == SessionLeaseHolder(process_id=os.getpid())


async def _next_notification(
    messages: AsyncIterator[Notification | ServerRequest], method: str
) -> dict[str, Any]:
    async for message in messages:
        if isinstance(message, Notification) and message.method == method:
            return message.params
    raise AssertionError(f"Connection closed before {method}")


async def _next_lease_update(
    messages: AsyncIterator[Notification | ServerRequest],
) -> dict[str, Any]:
    return await _next_notification(messages, "session/lease/updated")


@pytest.mark.asyncio
@pytest.mark.usefixtures("host_platform")
async def test_unified_lease_subscription_reports_held_sessions_then_changes(
    tmp_path: Path,
) -> None:
    from mistralai_vibe_local_harness.vibe._storage import SessionLease as HarnessLease

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    held_id = await _stored_unified_session(config, tmp_path)
    free_id = await _stored_unified_session(config, tmp_path)
    client, server = _connect_harness_host(config)
    await client.initialize(ClientInfo(name="test", version="0"))
    await client.notify("initialized")
    messages = client.incoming()
    try:
        with HarnessLease(tmp_path, held_id):
            subscribed = await client.request("session/lease/subscribe")
            with HarnessLease(tmp_path, free_id):
                taken = await asyncio.wait_for(_next_lease_update(messages), 10)
            released = await asyncio.wait_for(_next_lease_update(messages), 10)
        freed = await asyncio.wait_for(_next_lease_update(messages), 10)
    finally:
        await server.close()

    holder = {"processId": os.getpid()}
    assert subscribed == {"held": [{"sessionId": held_id, "leaseHolder": holder}]}
    assert [taken, released, freed] == [
        {"sessionId": free_id, "leaseHolder": holder},
        {"sessionId": free_id, "leaseHolder": None},
        {"sessionId": held_id, "leaseHolder": None},
    ]


@pytest.mark.asyncio
@pytest.mark.usefixtures("host_platform")
async def test_unified_lease_subscription_ends_with_its_connection(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    client, server = _connect_harness_host(config)
    await client.initialize(ClientInfo(name="test", version="0"))
    await client.notify("initialized")
    await client.request("session/lease/subscribe")
    first = server._leases._task
    await client.request("session/lease/subscribe")
    second = server._leases._task

    await server.close()

    assert first is not None and first.cancelled()
    assert second is not None and second.cancelled()
    assert server._leases._task is None


@pytest.mark.asyncio
async def test_unified_lease_subscription_tells_the_client_when_it_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import vibe.app_server._unified_harness_backend_adapter as adapter_module

    async def failing_watch(_storage_root: Path) -> AsyncIterator[dict[str, Any]]:
        yield {}
        raise RuntimeError("Session lease file watcher stopped")

    monkeypatch.setattr(adapter_module, "watch_session_leases", failing_watch)
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    client, server = _connect_harness_host(config)
    await client.initialize(ClientInfo(name="test", version="0"))
    await client.notify("initialized")
    messages = client.incoming()
    try:
        await client.request("session/lease/subscribe")
        ended = await asyncio.wait_for(
            _next_notification(messages, "session/lease/ended"), 10
        )
    finally:
        await server.close()

    assert ended == {}


@pytest.mark.asyncio
async def test_unified_concurrent_lease_subscriptions_leave_no_watcher_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import vibe.app_server._unified_harness_backend_adapter as adapter_module

    opened = 0
    closed = 0

    async def watch(_storage_root: Path) -> AsyncIterator[dict[str, Any]]:
        nonlocal opened, closed
        opened += 1
        try:
            await asyncio.sleep(0.05)
            yield {}
            await asyncio.Event().wait()
        finally:
            closed += 1

    monkeypatch.setattr(adapter_module, "watch_session_leases", watch)
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    client, server = _connect_harness_host(config)
    await client.initialize(ClientInfo(name="test", version="0"))
    await client.notify("initialized")
    await asyncio.gather(
        client.request("session/lease/subscribe"),
        client.request("session/lease/subscribe"),
    )

    await server.close()
    for _ in range(20):
        if closed == opened:
            break
        await asyncio.sleep(0.01)

    assert opened == 2
    assert closed == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["callback", "queued_steer"])
async def test_unified_activity_receipt_bumps_once_across_concurrent_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from mistralai_vibe_local_harness.vibe import HarnessSessionNotFoundError
    from vibe.app_server.protocol import CallbackResult, CallbackResultParams
    from vibe.core.session.session_logger import SessionLogger

    class Session(_RecordingSession):
        ephemeral = False
        reject = True

        async def respond_to_callback(self, params: object) -> object:
            if self.reject:
                raise HarnessSessionNotFoundError(self.session_id)
            # Harness vibe/_session.py returns accepted=True and no after_response
            # for both the first callback response and an identical replay.
            return SimpleNamespace(
                response={"accepted": True, "last_event_id": 0}, after_response=None
            )

        async def steer_queued_turn(self, params: Any) -> Any:
            if self.reject:
                raise HarnessSessionNotFoundError(self.session_id)
            return await super().steer_queued_turn(params)

    session = Session()
    adapter = _inert_adapter(session, None, str(tmp_path))
    adapter._translated_state = (
        await adapter.read(SessionReadParams(session_id="session-1"))
    ).state
    published = Mock()
    monkeypatch.setattr(adapter, "_publish_local_event", published)
    monkeypatch.setattr(adapter, "flush_events", AsyncMock())

    async def submit(identity: str) -> Any:
        if operation == "callback":
            return await adapter.respond_to_callback(
                CallbackResultParams(
                    session_id="session-1",
                    result=CallbackResult(
                        callback_id=identity,
                        output={"type": "approval", "decision": {"type": "allow_once"}},
                    ),
                )
            )
        # Harness vibe/_session.py also returns the same queued-steer response
        # with no after_response for both the accepted mutation and its replay.
        return await adapter.steer_queued_turn(
            TurnQueueSteerParams(
                session_id="session-1",
                queue_item_id=identity,
                expected_turn_id="turn-1",
            )
        )

    with pytest.raises(SessionBackendError):
        await submit("activity-1")
    assert not (tmp_path / "unified" / "session-1" / "meta.json").exists()
    session.reject = False

    persist = SessionLogger.persist_metadata
    writing = asyncio.Event()
    release = asyncio.Event()
    writes = 0

    async def hold_write(metadata: Any, session_dir: Path) -> None:
        nonlocal writes
        writes += 1
        writing.set()
        await release.wait()
        await persist(metadata, session_dir)

    monkeypatch.setattr(SessionLogger, "persist_metadata", hold_write)
    first = asyncio.create_task(submit("activity-1"))
    try:
        await writing.wait()
        duplicate = await submit("activity-1")
        assert duplicate.after_response is None
        assert writes == 1
        published.assert_not_called()
    finally:
        release.set()
        result = await first

    assert result.after_response is not None
    result.after_response()
    published.assert_called_once()
    metadata = json.loads(
        (tmp_path / "unified" / "session-1" / "meta.json").read_text()
    )
    bumped_at = int(datetime.fromisoformat(metadata["bumped_at"]).timestamp() * 1000)
    event = published.call_args.args[0]
    assert event.patch[0].path == "/bumpedAt"
    assert event.patch[0].value == bumped_at

    assert (await submit("activity-1")).after_response is None
    assert writes == 1
    assert (await submit("activity-2")).after_response is not None
    assert writes == 2


@pytest.mark.asyncio
async def test_unified_queue_retries_do_not_bump_session(tmp_path: Path) -> None:
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
    )

    host = _harness_backend_host(
        build_test_vibe_config(
            session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
        )
    )
    started = await host.start(SessionStartParams())
    backend = started.backend
    assert isinstance(backend, UnifiedHarnessBackendAdapter)
    params = TurnEnqueueParams(
        session_id=backend.session_id,
        idempotency_key="enqueue",
        entries=[TurnUserInputEntry(content=[SessionTextContentBlock(text="queued")])],
    )
    try:
        enqueued = await backend.enqueue_turn(params)
        meta_path = tmp_path / "unified" / backend.session_id / "meta.json"
        initial = json.loads(meta_path.read_text())["bumped_at"]
        assert initial is not None
        # Harness _session.py enqueue_turn/replace_queued_turn omit after_response
        # on duplicate receipts; resume_turn_queue omits it when already resumed.
        retried = await backend.enqueue_turn(params)
        assert retried.after_response is None
        assert json.loads(meta_path.read_text())["bumped_at"] == initial

        replacement = TurnQueueReplaceParams(
            session_id=backend.session_id,
            queue_item_id=enqueued.response.queue_item_id,
            idempotency_key="replace",
            entries=[
                TurnUserInputEntry(content=[SessionTextContentBlock(text="edited")])
            ],
        )
        await backend.replace_queued_turn(replacement)
        replaced_at = json.loads(meta_path.read_text())["bumped_at"]
        retried_replace = await backend.replace_queued_turn(replacement)
        assert retried_replace.after_response is None
        assert json.loads(meta_path.read_text())["bumped_at"] == replaced_at

        resumed = await backend.resume_turn_queue(
            TurnQueueResumeParams(session_id=backend.session_id)
        )
        assert resumed.after_response is None
        assert json.loads(meta_path.read_text())["bumped_at"] == replaced_at
    finally:
        await host.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("second_bump", [None, datetime(2099, 1, 1, tzinfo=UTC)])
async def test_unified_concurrent_metadata_writes_preserve_latest_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, second_bump: datetime | None
) -> None:
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
    )
    from vibe.core.session.session_logger import SessionLogger

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    host = _harness_backend_host(config)
    started = await host.start(SessionStartParams())
    backend = started.backend
    assert isinstance(backend, UnifiedHarnessBackendAdapter)
    await backend.start_turn(
        TurnStartParams(
            session_id=backend.session_id, message=[TextContentBlock(text="persist me")]
        )
    )

    persist = SessionLogger.persist_metadata
    writing = asyncio.Event()
    release = asyncio.Event()
    newest = datetime(2099, 1, 2, tzinfo=UTC)
    writes = 0

    async def hold_first_write(metadata: Any, session_dir: Path) -> None:
        nonlocal writes
        writes += 1
        if writes == 1:
            writing.set()
            await release.wait()
        await persist(metadata, session_dir)

    monkeypatch.setattr(SessionLogger, "persist_metadata", hold_first_write)
    first = asyncio.create_task(backend._persist_session_metadata(bumped_at=newest))
    await writing.wait()
    second = asyncio.create_task(
        backend._persist_session_metadata(bumped_at=second_bump)
    )
    try:
        # Let the competing operation reach its first suspension point.
        await asyncio.sleep(0)
        assert writes == 1
    finally:
        release.set()
        await asyncio.gather(first, second)
        await host.shutdown()

    metadata = json.loads(
        (tmp_path / "unified" / backend.session_id / "meta.json").read_text()
    )
    assert metadata["bumped_at"] == newest.isoformat()


@pytest.mark.asyncio
async def test_unified_resume_quarantines_a_corrupt_loop_store(tmp_path: Path) -> None:
    """*Prepare*: A persisted Unified session whose scheduled-loop file is corrupt.
    *Do*: Resume it through a fresh backend Host.
    *Assert*: Resume succeeds without loops and reports where the invalid file was kept.
    """
    # Prepare
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    first_host = _harness_backend_host(config)
    started = await first_host.start(SessionStartParams())
    session_id = started.backend.session_id
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await first_host.shutdown()
    schedule_path = tmp_path / "unified" / session_id / "scheduled-loops.json"
    schedule_path.write_text("not-json", encoding="utf-8")
    second_host = _harness_backend_host(config)
    harness_host = cast(Any, second_host)._host

    # Do
    resumed = await second_host.resume(SessionResumeParams(session_id=session_id))
    subscription = await resumed.backend.subscribe(
        SessionReadParams(session_id=session_id)
    )
    warning = await anext(subscription.events)

    # Assert
    assert resumed.backend.session_id == session_id
    assert await cast(Any, resumed.backend).scheduled_loops() == []
    assert harness_host._live_session_ids() == frozenset({session_id})
    assert warning.method == "warning"
    assert isinstance(warning.params, ServerWarningParams)
    assert "could not be restored" in warning.params.warning.message
    quarantine_paths = list(schedule_path.parent.glob("scheduled-loops.corrupt-*.json"))
    assert len(quarantine_paths) == 1
    assert quarantine_paths[0].read_text(encoding="utf-8") == "not-json"
    assert not schedule_path.exists()
    await second_host.shutdown()
    # Normal adapter teardown persists the recovered empty schedule while
    # preserving the corrupt original for diagnosis.
    assert json.loads(schedule_path.read_text())["loops"] == []
    assert quarantine_paths[0].read_text(encoding="utf-8") == "not-json"


@pytest.mark.asyncio
async def test_unified_resume_continue_and_cold_read_use_the_stored_cwd(
    tmp_path: Path,
) -> None:
    stored_cwd = str((tmp_path / "stored-project").resolve())
    invocation_cwd = str((tmp_path / "other-project").resolve())
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    first_host = _harness_backend_host(config)
    started = await first_host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=stored_cwd))
    )
    session_id = started.backend.session_id
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await first_host.shutdown()

    second_host = _harness_backend_host(config)
    cold_read = await second_host.read(SessionReadParams(session_id=session_id))
    resumed = await second_host.resume(
        SessionResumeParams(
            session_id=session_id, agent_config=SessionOptions(cwd=invocation_cwd)
        )
    )
    resumed_read = await resumed.backend.read(SessionReadParams(session_id=session_id))
    await second_host.shutdown()

    third_host = _harness_backend_host(config)
    continued = await third_host.continue_latest(
        SessionContinueParams(agent_config=SessionOptions(cwd=invocation_cwd))
    )
    continued_read = await continued.backend.read(
        SessionReadParams(session_id=session_id)
    )
    await third_host.shutdown()

    assert cold_read.state.session.cwd == stored_cwd
    assert resumed_read.state.session.cwd == stored_cwd
    assert continued_read.state.session.cwd == stored_cwd


@pytest.mark.asyncio
async def test_unified_resume_seeds_persisted_children_into_the_read_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A persisted root session whose harness Host holds one idle child.
    *Do*: Resume the root through a fresh host adapter.
    *Assert*: The resumed backend's read state lists the seeded child.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._host import HarnessSessionReadResult

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    first_host = _harness_backend_host(config)
    started = await first_host.start(SessionStartParams())
    session_id = started.backend.session_id
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await first_host.shutdown()

    child_id = f"{session_id}-scout"
    second_host = _harness_backend_host(config)
    harness_host = cast(Any, second_host)._host
    monkeypatch.setattr(
        harness_host,
        "child_session_records",
        lambda root_session_id: (
            (
                _persisted_child_record(
                    "scout", child_id, _persisted_child_states()["idle"]
                ),
            )
            if root_session_id == session_id
            else ()
        ),
    )
    original_read = harness_host.read

    async def read_with_child(params: Any) -> Any:
        if params.session_id != child_id:
            return await original_read(params)
        return HarnessSessionReadResult(
            snapshot=HarnessSessionSnapshot(
                state=HarnessPublicSessionState(
                    session=HarnessPublicSession(
                        id=child_id,
                        root_session_id=session_id,
                        parent_session_id=session_id,
                        status=IdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                    ),
                    turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
                ),
                history_limit=1,
                watermark=0,
            ),
            cwd=str(tmp_path),
        )

    monkeypatch.setattr(harness_host, "read", read_with_child)

    # Do
    resumed = await second_host.resume(SessionResumeParams(session_id=session_id))
    read = await resumed.backend.read(SessionReadParams(session_id=session_id))

    # Assert
    assert [child.id for child in read.state.child_sessions] == [child_id]
    seeded = read.state.child_sessions[0]
    assert seeded.name == "scout"
    assert seeded.agent_type == "explore"
    assert seeded.status.type == "idle"
    assert cast(Any, resumed.backend)._child_states.keys() == {child_id}
    await second_host.shutdown()


@pytest.mark.asyncio
async def test_unified_resume_and_continue_restore_the_running_mode(
    tmp_path: Path,
) -> None:
    """*Prepare*: A promoted session switched to the ``plan`` running mode.
    *Do*: Cold-reopen it through resume and through continue-latest.
    *Assert*: Both put the session back on ``plan`` rather than the default agent.
    """
    from vibe.app_server.protocol import AgentSwitchParams

    # Prepare
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    first_host = _harness_backend_host(config)
    started = await first_host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path), agent="ask"))
    )
    session_id = started.backend.session_id
    await started.backend.switch_agent(
        AgentSwitchParams(session_id=session_id, agent_name="plan")
    )
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await first_host.shutdown()

    # Do
    second_host = _harness_backend_host(config)
    resumed = await second_host.resume(
        SessionResumeParams(
            session_id=session_id, agent_config=SessionOptions(cwd=str(tmp_path))
        )
    )
    assert isinstance(resumed.backend, SessionBackendRuntimeView)
    resumed_agent = resumed.backend.runtime_updated_params().runtime.active_agent.name
    await second_host.shutdown()

    third_host = _harness_backend_host(config)
    continued = await third_host.continue_latest(
        SessionContinueParams(agent_config=SessionOptions(cwd=str(tmp_path)))
    )
    assert isinstance(continued.backend, SessionBackendRuntimeView)
    continued_agent = (
        continued.backend.runtime_updated_params().runtime.active_agent.name
    )
    await third_host.shutdown()

    # Assert
    assert resumed_agent == "plan"
    assert continued_agent == "plan"


@pytest.mark.asyncio
async def test_unified_cold_read_reports_the_session_pins_without_resuming(
    tmp_path: Path,
) -> None:
    """*Prepare*: A promoted session pinned to a non-default model and to ``plan``.
    *Do*: Cold-read it from a fresh host, never resuming it.
    *Assert*: The read names the session's own model and running mode, and no
    session was loaded to answer. Both differ from the host's own values, so a
    read that ignored the pins could not pass.
    """
    from vibe.app_server.protocol import AgentSwitchParams, ModelConfigWriteParams

    # Prepare
    config = build_test_vibe_config(
        models=[
            DEFAULT_ACTIVE_MODEL_CONFIG,
            ModelConfig(name="other-model", provider="mistral", alias="other"),
        ],
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        ),
    )
    host_model = config.get_active_model().alias
    pinned_model = next(alias for alias in config.models if alias != host_model)
    first_host = _harness_backend_host(config)
    started = await first_host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path), agent="ask"))
    )
    session_id = started.backend.session_id
    await started.backend.write_model_config(
        ModelConfigWriteParams(session_id=session_id, model_alias=pinned_model)
    )
    await started.backend.switch_agent(
        AgentSwitchParams(session_id=session_id, agent_name="plan")
    )
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await first_host.shutdown()

    # Do
    second_host = _harness_backend_host(config)
    cold_read = await second_host.read(SessionReadParams(session_id=session_id))
    live_after_read = cast(Any, second_host)._host._live_session_ids()
    await second_host.shutdown()

    # Assert
    assert cold_read.state.session.model == pinned_model
    assert cold_read.state.session.agent is not None
    assert cold_read.state.session.agent.name == "plan"
    assert live_after_read == frozenset()
    assert pinned_model != host_model


@pytest.mark.asyncio
async def test_unified_cold_read_resolves_the_pinned_agent_from_the_read_context(
    tmp_path: Path,
) -> None:
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host
    from vibe.app_server.protocol import AgentSwitchParams

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    first_host = _harness_backend_host(config)
    started = await first_host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path), agent="ask"))
    )
    session_id = started.backend.session_id
    await started.backend.switch_agent(
        AgentSwitchParams(session_id=session_id, agent_name="plan")
    )
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await first_host.shutdown()

    session_contexts: list[str | None] = []
    inner = _test_session_runtime_builder(config)

    async def counting_session_context(
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: Any = "cli",
    ) -> Any:
        session_contexts.append(options.cwd)
        return await inner(
            options, require_api_key=require_api_key, entrypoint=entrypoint
        )

    second_host = adapt_harness_host(
        vibe_runtime.create_harness_host(),
        counting_session_context,
        build_read_context=_read_context_builder(config, str(tmp_path)),
    )
    cold_read = await second_host.read(SessionReadParams(session_id=session_id))
    await second_host.shutdown()

    assert cold_read.state.session.agent is not None
    assert cold_read.state.session.agent.name == "plan"
    assert session_contexts == []


@pytest.mark.asyncio
async def test_unified_cold_read_reports_a_model_picked_back_to_the_default(
    tmp_path: Path,
) -> None:
    """*Prepare*: A session pinned to a model and then switched back to the default.
    *Do*: Cold-read it from a fresh host.
    *Assert*: The model reads as unpinned rather than as the empty string that
    picking the default stores, which a client would show as a blank selection.
    """
    from vibe.app_server.protocol import ModelConfigWriteParams

    # Prepare
    config = build_test_vibe_config(
        models=[
            DEFAULT_ACTIVE_MODEL_CONFIG,
            ModelConfig(name="other-model", provider="mistral", alias="other"),
        ],
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        ),
    )
    host_model = config.get_active_model().alias
    pinned_model = next(alias for alias in config.models if alias != host_model)
    first_host = _harness_backend_host(config)
    started = await first_host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path)))
    )
    session_id = started.backend.session_id
    # The turn comes first: starting one pins whatever model is active, so a
    # pick made before it would be overwritten and prove nothing.
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await started.backend.write_model_config(
        ModelConfigWriteParams(session_id=session_id, model_alias=pinned_model)
    )
    await started.backend.write_model_config(
        ModelConfigWriteParams(session_id=session_id, model_alias="")
    )
    await first_host.shutdown()

    # Do
    second_host = _harness_backend_host(config)
    cold_read = await second_host.read(SessionReadParams(session_id=session_id))
    await second_host.shutdown()

    # Assert
    assert cold_read.state.session.model is None


@pytest.mark.asyncio
async def test_unified_cold_read_reports_the_thinking_level_without_resuming(
    tmp_path: Path,
) -> None:
    """*Prepare*: A promoted session whose user picked a thinking level.
    *Do*: Cold-read it from a fresh host, never resuming it.
    *Assert*: The read names the session's own level, and no session was loaded
    to answer. This is the read a client's picker makes before it attaches.
    """
    from vibe.app_server.protocol import ModelConfigWriteParams

    # Prepare
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    host_level = config.get_active_model().thinking
    picked = next(
        level
        for level in config.get_active_model().thinking_levels
        if level != host_level
    )
    first_host = _harness_backend_host(config)
    started = await first_host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path)))
    )
    session_id = started.backend.session_id
    await started.backend.write_model_config(
        ModelConfigWriteParams(session_id=session_id, reasoning_effort=picked)
    )
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await first_host.shutdown()

    # Do
    second_host = _harness_backend_host(config)
    cold_read = await second_host.read(SessionReadParams(session_id=session_id))
    live_after_read = cast(Any, second_host)._host._live_session_ids()
    await second_host.shutdown()

    # Assert
    assert cold_read.state.session.reasoning_effort == picked
    assert live_after_read == frozenset()
    assert picked != host_level


@pytest.mark.asyncio
async def test_unified_config_read_answers_for_a_session_it_is_not_sitting_on(
    tmp_path: Path,
) -> None:
    """*Prepare*: A promoted session whose user picked a model and a level.
    *Do*: Read the configuration for it from a fresh host, naming the session.
    *Assert*: The answer is the session's, not the host's. A client renders its
    pickers from this read before it attaches anything.
    """
    from vibe.app_server.protocol import ConfigReadParams, ModelConfigWriteParams

    # Prepare
    config = build_test_vibe_config(
        models=[
            DEFAULT_ACTIVE_MODEL_CONFIG,
            ModelConfig(name="other-model", provider="mistral", alias="other"),
        ],
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        ),
    )
    host_model = config.get_active_model()
    picked_model = next(alias for alias in config.models if alias != host_model.alias)
    # A level the picked model offers that differs from the host's, so the
    # session's answer cannot coincide with the host's by accident.
    picked_level = next(
        level
        for level in config.models[picked_model].thinking_levels
        if level != host_model.thinking
    )
    first_host = _harness_backend_host(config)
    started = await first_host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path)))
    )
    session_id = started.backend.session_id
    await started.backend.write_model_config(
        ModelConfigWriteParams(
            session_id=session_id,
            model_alias=picked_model,
            reasoning_effort=picked_level,
        )
    )
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await first_host.shutdown()

    # Do
    second_host = _harness_backend_host(config)
    read = await cast(Any, second_host).read_config(
        ConfigReadParams(session_id=session_id)
    )
    live_after_read = cast(Any, second_host)._host._live_session_ids()
    await second_host.shutdown()

    # Assert
    assert read.config.active_model.alias == picked_model
    assert read.config.active_model.thinking == picked_level
    assert live_after_read == frozenset()
    assert (picked_model, picked_level) != (host_model.alias, host_model.thinking)


@pytest.mark.asyncio
async def test_unified_config_read_answers_for_a_legacy_session(tmp_path: Path) -> None:
    """*Prepare*: A legacy session, listed by the merged list but never imported,
    left on a model that is not the host's.
    *Do*: Read the configuration for it, as a picker does before attaching.
    *Assert*: It answers with that session's model rather than refusing. The
    store resolves a legacy id through the same cwd and pin lookups it uses for
    a unified one, so the read needs no separate branch -- this is what proves
    that, since refusing here would break the picker on every unmigrated session.
    """
    from vibe.app_server.protocol import ConfigReadParams

    # Prepare
    config = build_test_vibe_config(
        models=[
            DEFAULT_ACTIVE_MODEL_CONFIG,
            ModelConfig(name="other-model", provider="mistral", alias="other"),
        ],
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        ),
    )
    host_model = config.get_active_model().alias
    legacy_model = next(alias for alias in config.models if alias != host_model)
    legacy = build_test_agent_loop(config=config)
    legacy.messages.append(LLMMessage(role=Role.user, content="hello"))
    await legacy.session_logger.save_interaction(
        legacy.messages,
        legacy.stats,
        legacy.config,
        legacy.tool_manager,
        legacy.agent_profile,
    )
    await legacy.session_logger.persist_active_model(legacy_model)
    session_id = legacy.session_id
    await legacy.aclose()

    # Do
    host = _harness_backend_host(config)
    read = await cast(Any, host).read_config(ConfigReadParams(session_id=session_id))
    await host.shutdown()

    # Assert
    assert read.config.active_model.alias == legacy_model
    assert legacy_model != host_model


@pytest.mark.asyncio
async def test_unified_resume_restores_the_thinking_level_the_session_ran(
    tmp_path: Path,
) -> None:
    """*Prepare*: A promoted session whose user picked a thinking level.
    *Do*: Cold-reopen it against the unchanged configuration.
    *Assert*: It comes back on the picked level, not the configured one.
    """
    from vibe.app_server.protocol import ModelConfigWriteParams

    # Prepare
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    configured = config.get_active_model().thinking
    picked = next(
        level
        for level in config.get_active_model().thinking_levels
        if level != configured
    )
    first_host = _harness_backend_host(config)
    started = await first_host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path)))
    )
    session_id = started.backend.session_id
    await started.backend.write_model_config(
        ModelConfigWriteParams(session_id=session_id, reasoning_effort=picked)
    )
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await first_host.shutdown()

    # Do
    second_host = _harness_backend_host(config)
    resumed = await second_host.resume(
        SessionResumeParams(
            session_id=session_id, agent_config=SessionOptions(cwd=str(tmp_path))
        )
    )
    assert isinstance(resumed.backend, SessionBackendRuntimeView)
    resumed_thinking = (
        resumed.backend.runtime_updated_params().runtime.config.active_model.thinking
    )
    await second_host.shutdown()

    # Assert
    assert resumed_thinking == picked


@pytest.mark.asyncio
async def test_unified_resume_unpins_an_unavailable_running_mode(
    tmp_path: Path,
) -> None:
    """*Prepare*: A promoted session whose stored running mode no longer exists.
    *Do*: Cold-reopen it through resume.
    *Assert*: The session falls back to the default agent instead of failing.
    """
    # Prepare
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    first_host = _harness_backend_host(config)
    started = await first_host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path), agent="ask"))
    )
    from mistralai_vibe_local_harness.vibe._storage import SessionPin

    session_id = started.backend.session_id
    assert isinstance(started.backend, SessionBackendRuntimeView)
    default_agent = started.backend.runtime_updated_params().runtime.active_agent.name
    await cast(Any, started.backend)._session.persist_pin(
        SessionPin.AGENT_NAME, "nonexistent-agent"
    )
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await first_host.shutdown()

    # Do
    second_host = _harness_backend_host(config)
    resumed = await second_host.resume(
        SessionResumeParams(
            session_id=session_id,
            agent_config=SessionOptions(cwd=str(tmp_path), agent="ask"),
        )
    )
    assert isinstance(resumed.backend, SessionBackendRuntimeView)
    resumed_agent = resumed.backend.runtime_updated_params().runtime.active_agent.name
    await second_host.shutdown()

    # Assert
    assert resumed_agent == default_agent


# The built-in list ships one model, so the pin target on another provider is
# the tests' own; the compaction model stays on the default model's provider.
_PIN_TARGET_MODELS = [
    DEFAULT_ACTIVE_MODEL_CONFIG,
    ModelConfig(name="devstral", provider="llamacpp", alias="local-llamacpp"),
]


@pytest.mark.asyncio
async def test_unified_resume_keeps_a_model_pin_on_another_provider_than_compaction(
    tmp_path: Path,
) -> None:
    """*Prepare*: A promoted session pinned to a model on a different provider
    than the configured compaction model.
    *Do*: Cold-reopen it through resume.
    *Assert*: The session comes back on the pinned model. The pin is the user's
    latest deliberate choice; the compaction override is the setting that cannot
    follow it, so that is the one that yields.
    """
    from mistralai_vibe_local_harness.vibe._storage import SessionPin
    from vibe.core.config import ModelConfig
    from vibe.core.config.vibe_schema import DEFAULT_ACTIVE_MODEL_CONFIG

    # Prepare
    config = build_test_vibe_config(
        models=_PIN_TARGET_MODELS,
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        ),
        compaction_model=ModelConfig(
            name="compact-model",
            provider=DEFAULT_ACTIVE_MODEL_CONFIG.provider,
            alias="compact",
        ),
    )
    default_model = config.get_active_model()
    other_provider_model = next(
        alias
        for alias, model in config.models.items()
        if model.provider != default_model.provider
    )
    first_host = _harness_backend_host(config)
    started = await first_host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path)))
    )
    session_id = started.backend.session_id
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    # After the turn: starting one pins the model the session ran, which would
    # overwrite the pin this covers.
    await cast(Any, started.backend)._session.persist_pin(
        SessionPin.ACTIVE_MODEL, other_provider_model
    )
    await first_host.shutdown()

    # Do
    second_host = _harness_backend_host(config)
    resumed = await second_host.resume(
        SessionResumeParams(
            session_id=session_id, agent_config=SessionOptions(cwd=str(tmp_path))
        )
    )
    assert isinstance(resumed.backend, SessionBackendRuntimeView)
    resumed_model = (
        resumed.backend.runtime_updated_params().runtime.config.active_model.alias
    )
    await second_host.shutdown()

    # Assert
    assert resumed_model == other_provider_model


@pytest.mark.asyncio
async def test_unified_config_read_keeps_a_model_pin_on_another_provider_than_compaction(
    tmp_path: Path,
) -> None:
    """*Prepare*: The same stored session, pinned to a model on another provider
    than the configured compaction model.
    *Do*: Read its configuration the way a picker does, without attaching.
    *Assert*: It answers with the pinned model rather than raising. The read puts
    a context on the same pins a resume does, which is why this conflict also
    stopped new sessions from starting.
    """
    from mistralai_vibe_local_harness.vibe._storage import SessionPin
    from vibe.app_server.protocol import ConfigReadParams
    from vibe.core.config import ModelConfig
    from vibe.core.config.vibe_schema import DEFAULT_ACTIVE_MODEL_CONFIG

    # Prepare
    config = build_test_vibe_config(
        models=_PIN_TARGET_MODELS,
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        ),
        compaction_model=ModelConfig(
            name="compact-model",
            provider=DEFAULT_ACTIVE_MODEL_CONFIG.provider,
            alias="compact",
        ),
    )
    default_model = config.get_active_model()
    other_provider_model = next(
        alias
        for alias, model in config.models.items()
        if model.provider != default_model.provider
    )
    first_host = _harness_backend_host(config)
    started = await first_host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path)))
    )
    session_id = started.backend.session_id
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await cast(Any, started.backend)._session.persist_pin(
        SessionPin.ACTIVE_MODEL, other_provider_model
    )
    await first_host.shutdown()

    # Do
    second_host = _harness_backend_host(config)
    read = await cast(Any, second_host).read_config(
        ConfigReadParams(session_id=session_id)
    )
    await second_host.shutdown()

    # Assert
    assert read.config.active_model.alias == other_provider_model


@pytest.mark.asyncio
async def test_unified_resume_skips_a_stored_pin_the_config_rejects(
    tmp_path: Path,
) -> None:
    """*Prepare*: A promoted session whose stored thinking level is not one the
    configuration accepts.
    *Do*: Cold-reopen it through resume.
    *Assert*: The session opens on the configured level instead of failing. A
    pin the merged config rejects is unreachable from inside the app -- every
    picker writes through the same orchestrator -- so letting it strand the
    resume would leave the user nothing to recover with.
    """
    from mistralai_vibe_local_harness.vibe._storage import SessionPin

    # Prepare
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    configured = config.get_active_model().thinking
    first_host = _harness_backend_host(config)
    started = await first_host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path)))
    )
    session_id = started.backend.session_id
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    # After the turn: starting one pins the level the session ran, which would
    # overwrite the stale pin this covers.
    await cast(Any, started.backend)._session.persist_pin(
        SessionPin.REASONING_EFFORT, "not-a-level"
    )
    await first_host.shutdown()

    # Do
    second_host = _harness_backend_host(config)
    resumed = await second_host.resume(
        SessionResumeParams(
            session_id=session_id, agent_config=SessionOptions(cwd=str(tmp_path))
        )
    )
    assert isinstance(resumed.backend, SessionBackendRuntimeView)
    resumed_thinking = (
        resumed.backend.runtime_updated_params().runtime.config.active_model.thinking
    )
    await second_host.shutdown()

    # Assert
    assert resumed_thinking == configured


@pytest.mark.asyncio
async def test_unified_continue_latest_accepts_current_system_instructions(
    tmp_path: Path,
) -> None:
    """*Prepare*: A pending turn persisted with the previous process's dated instructions.
    *Do*: Continue the latest session after the app-server derives new instructions.
    *Assert*: The real continue path restores the session instead of reporting divergence.
    """
    # Prepare
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    instructions = "Today's date is 2026-08-28 (Friday)."

    def current_instructions() -> str:
        return instructions

    first_host = _harness_backend_host(config, system_instructions=current_instructions)
    started = await first_host.start(SessionStartParams())
    session_id = started.backend.session_id
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await first_host.shutdown()
    instructions = "Today's date is 2026-08-31 (Monday)."
    second_host = _harness_backend_host(
        config, system_instructions=current_instructions
    )

    # Do
    continued = await second_host.continue_latest(SessionContinueParams())

    # Assert
    assert continued.backend.session_id == session_id
    await second_host.shutdown()


@pytest.mark.asyncio
async def test_unified_continue_latest_resumes_the_latest_session_with_its_cwd(
    tmp_path: Path,
) -> None:
    # Guard the continue-latest TOCTOU: the session resolved for cwd/hooks must be the
    # one actually resumed. With two sessions in different projects, continue must resume
    # the latest and use its stored cwd, so hooks compile for the resumed project rather
    # than a session that changed between the two internal listings.
    cwd_a = str((tmp_path / "project-a").resolve())
    cwd_b = str((tmp_path / "project-b").resolve())
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    host = _harness_backend_host(config)
    first = await host.start(SessionStartParams(agent_config=SessionOptions(cwd=cwd_a)))
    second = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=cwd_b))
    )
    await first.backend.start_turn(
        TurnStartParams(
            session_id=first.backend.session_id,
            message=[TextContentBlock(text="persist first")],
        )
    )
    await second.backend.start_turn(
        TurnStartParams(
            session_id=second.backend.session_id,
            message=[TextContentBlock(text="persist second")],
        )
    )
    continued = await host.continue_latest(
        SessionContinueParams(agent_config=SessionOptions(cwd=str(tmp_path)))
    )
    continued_read = await continued.backend.read(
        SessionReadParams(session_id=continued.backend.session_id)
    )
    await host.shutdown()

    assert continued.backend.session_id == second.backend.session_id
    assert continued_read.state.session.cwd == cwd_b


@pytest.mark.asyncio
async def test_unified_continue_latest_lists_the_requested_project_store(
    tmp_path: Path,
) -> None:
    project_cwd = str((tmp_path / "project").resolve())
    Path(project_cwd).mkdir()
    default_config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path / "default-sessions")
        )
    )
    project_config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path / "project-sessions")
        )
    )
    writer = _project_scoped_harness_backend_host(
        default_config, project_config, project_cwd
    )
    started = await writer.start(
        SessionStartParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    session_id = started.backend.session_id
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id,
            message=[TextContentBlock(text="persist in the project store")],
        )
    )
    await writer.shutdown()

    reader = _project_scoped_harness_backend_host(
        default_config, project_config, project_cwd
    )
    continued = await reader.continue_latest(
        SessionContinueParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    await reader.shutdown()

    assert continued.backend.session_id == session_id


@pytest.mark.asyncio
@pytest.mark.parametrize("use_short_id", [False, True])
async def test_unified_resume_imports_quiescent_legacy_history(
    tmp_path: Path, use_short_id: bool
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    active_model = config.get_active_model().alias
    legacy_root = build_test_agent_loop(config=config)
    legacy_root.messages.append(LLMMessage(role=Role.user, content="root"))
    await legacy_root.session_logger.save_interaction(
        legacy_root.messages,
        legacy_root.stats,
        legacy_root.config,
        legacy_root.tool_manager,
        legacy_root.agent_profile,
    )
    legacy_root_id = legacy_root.session_id
    await legacy_root.aclose()

    legacy = build_test_agent_loop(config=config, parent_session_id=legacy_root_id)
    legacy.messages.extend([
        LLMMessage(role=Role.user, content="hello"),
        LLMMessage(role=Role.assistant, content="hi"),
    ])
    await legacy.session_logger.save_interaction(
        legacy.messages,
        legacy.stats,
        legacy.config,
        legacy.tool_manager,
        legacy.agent_profile,
    )
    await legacy.session_logger.persist_active_model(active_model)
    session_id = legacy.session_id
    await legacy.aclose()
    exported = export_legacy_committed_history(session_id, config.session_logging)
    assert exported is not None

    host = _harness_backend_host(config)
    requested_id = session_id[:8] if use_short_id else session_id
    resumed = await host.resume(SessionResumeParams(session_id=requested_id))
    imported_session_id = resumed.backend.session_id
    read = await resumed.backend.read(
        SessionReadParams(session_id=imported_session_id, history=PageRequest(limit=10))
    )
    by_legacy_parent = await host.list(
        SessionListParams(parent_session_id=legacy_root_id)
    )
    await host.shutdown()

    round_tripped = build_test_agent_loop(config=config)
    await AgentRuntimeFactory().resume_root(round_tripped, imported_session_id)
    try:
        round_trip_history = list(round_tripped.messages)
    finally:
        await round_tripped.aclose()

    assert read.state.history is not None
    assert [entry.model_dump()["role"] for entry in read.state.history] == [
        "user",
        "assistant",
    ]
    assert imported_session_id != session_id
    assert read.state.session.root_session_id == imported_session_id
    assert read.state.session.parent_session_id is None
    assert by_legacy_parent.items == []
    assert [
        message.role
        for message in round_trip_history
        if message.role is not Role.system
    ] == [Role.user, Role.assistant]
    from mistralai_vibe_local_harness.vibe._storage import (
        LegacyInteropSourceV1,
        UnifiedSessionStore,
    )

    stored = UnifiedSessionStore(tmp_path, imported_session_id).load()
    assert (
        not (tmp_path / "unified" / requested_id).is_dir() or requested_id == session_id
    )
    provenance = stored.runtime_state.import_provenance
    assert provenance is not None
    assert isinstance(provenance.source, LegacyInteropSourceV1)
    assert provenance.source.session_id == session_id
    assert stored.runtime_state.session_metadata.active_model == active_model
    assert exported.active_model == active_model


@pytest.mark.asyncio
@pytest.mark.parametrize("use_short_id", [False, True])
async def test_unified_resume_imports_legacy_history_over_json_rpc(
    tmp_path: Path, use_short_id: bool
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    legacy = build_test_agent_loop(config=config)
    legacy.messages.append(LLMMessage(role=Role.user, content="hello"))
    await legacy.session_logger.save_interaction(
        legacy.messages,
        legacy.stats,
        legacy.config,
        legacy.tool_manager,
        legacy.agent_profile,
    )
    session_id = legacy.session_id
    await legacy.aclose()
    client, server = _connect_harness_host(config)

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        requested_id = session_id[:8] if use_short_id else session_id
        resumed = SessionReadResponse.model_validate(
            await client.request(
                "session/resume", SessionResumeParams(session_id=requested_id)
            )
        )
    finally:
        await server.close()

    assert resumed.state.session.id != session_id
    assert [entry.model_dump()["role"] for entry in resumed.state.history or []] == [
        "user"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("use_short_id", [False, True])
async def test_legacy_resume_imports_quiescent_unified_history(
    tmp_path: Path, use_short_id: bool
) -> None:
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        SessionStartParams as HarnessSessionStartParams,
    )
    from mistralai_vibe_local_harness.vibe._storage import SessionPin

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    unified = vibe_runtime.UnifiedHarnessSessionBackendHost(tmp_path)
    started = await unified.start(HarnessSessionStartParams(history_limit=10))
    session_id = started.session_id
    active_model = config.get_active_model().alias
    await started.persist_pin(SessionPin.ACTIVE_MODEL, active_model)
    await unified.shutdown()

    source = build_test_agent_loop(config=config)
    requested_id = session_id[:8] if use_short_id else session_id
    await AgentRuntimeFactory().resume_root(source, requested_id)
    try:
        assert source.session_id == session_id
        metadata = source.session_logger.session_metadata
        assert metadata is not None
        assert metadata.session_id == session_id
        assert metadata.import_provenance is not None
        assert metadata.config is not None
        assert metadata.config["active_model"] == active_model
        exported = export_legacy_committed_history(session_id, config.session_logging)
        assert exported is not None
        assert exported.history == []
        assert exported.active_model == active_model
    finally:
        await source.aclose()


@pytest.mark.asyncio
async def test_unified_list_filters_use_stored_cwd_and_fork_lineage(
    tmp_path: Path,
) -> None:
    project_cwd = str((tmp_path / "project").resolve())
    other_cwd = str((tmp_path / "other").resolve())
    host = _harness_backend_host(
        build_test_vibe_config(
            session_logging=SessionLoggingConfig(
                enabled=True, save_dir=str(tmp_path), session_prefix="session"
            )
        )
    )
    root = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    other = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=other_cwd))
    )
    root_turn = await root.backend.start_turn(
        TurnStartParams(
            session_id=root.backend.session_id,
            message=[TextContentBlock(text="persist root")],
        )
    )
    assert root_turn.after_response is not None
    root_turn.after_response()
    other_turn = await other.backend.start_turn(
        TurnStartParams(
            session_id=other.backend.session_id,
            message=[TextContentBlock(text="persist other")],
        )
    )
    assert other_turn.after_response is not None
    other_turn.after_response()
    await asyncio.gather(
        cast(Any, root.backend)._session._wait_for_pending_turns(),
        cast(Any, other.backend)._session._wait_for_pending_turns(),
    )
    forked = await host.fork(
        SessionForkParams(source_session_id=root.backend.session_id, attach=False)
    )

    by_cwd = await host.list(SessionListParams(cwd=project_cwd))
    by_root = await host.list(
        SessionListParams(root_session_id=root.backend.session_id)
    )
    by_parent = await host.list(
        SessionListParams(parent_session_id=root.backend.session_id)
    )
    await host.shutdown()

    forked_id = forked.response.state.session.id
    assert {session.id for session in by_cwd.items} == {
        root.backend.session_id,
        forked_id,
    }
    assert {session.cwd for session in by_cwd.items} == {project_cwd}
    assert {session.id for session in by_root.items} == {
        root.backend.session_id,
        forked_id,
    }
    assert [session.id for session in by_parent.items] == [forked_id]
    assert other.backend.session_id not in {session.id for session in by_cwd.items}


@pytest.mark.asyncio
async def test_unified_list_includes_retained_worktree_sessions(tmp_path: Path) -> None:
    project_cwd = (tmp_path / "project").resolve()
    project_cwd.mkdir()
    worktree_root = WORKTREES_DIR.path.resolve() / "project-test" / "retained-worktree"
    worktree_cwd = worktree_root / "packages" / "app"
    worktree_cwd.mkdir(parents=True)
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path / "sessions")
        )
    )
    legacy = build_test_agent_loop(config=config, cwd=worktree_cwd)
    legacy.messages.append(LLMMessage(role=Role.user, content="legacy retained"))
    await legacy.session_logger.save_interaction(
        legacy.messages,
        legacy.stats,
        legacy.config,
        legacy.tool_manager,
        legacy.agent_profile,
    )
    legacy_id = legacy.session_id
    await legacy.aclose()

    host = _harness_backend_host(config)
    started = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(worktree_cwd)))
    )
    await started.backend.start_turn(
        TurnStartParams(
            session_id=started.backend.session_id,
            message=[TextContentBlock(text="persist retained session")],
        )
    )
    await started.backend.shutdown()
    WorktreeClaim(bucket="project-test", name="retained-worktree").write_recovery(
        WorktreeRecoveryRecord(
            name="retained-worktree",
            branch="vibe/retained-worktree",
            repo_root=project_cwd,
            base_commit="0" * 40,
            snapshot_ref="refs/vibe/reaped/retained-worktree",
            removed_at=datetime.now(UTC),
        )
    )
    shutil.rmtree(worktree_root)

    listed = await host.list(SessionListParams(cwd=str(project_cwd)))
    await host.shutdown()

    harnesses = {session.id: session.harness for session in listed.items}
    assert harnesses[started.backend.session_id] == "unified"
    assert harnesses[legacy_id] == "legacy"


@pytest.mark.asyncio
async def test_unified_list_excludes_retained_nested_repository_sessions(
    tmp_path: Path,
) -> None:
    project_cwd = (tmp_path / "project").resolve()
    nested_repo_cwd = project_cwd / "vendor" / "nested"
    nested_repo_cwd.mkdir(parents=True)
    worktree_root = WORKTREES_DIR.path.resolve() / "nested-test" / "retained"
    worktree_cwd = worktree_root / "packages" / "app"
    worktree_cwd.mkdir(parents=True)
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path / "sessions")
        )
    )
    legacy = build_test_agent_loop(config=config, cwd=worktree_cwd)
    legacy.messages.append(LLMMessage(role=Role.user, content="legacy nested"))
    await legacy.session_logger.save_interaction(
        legacy.messages,
        legacy.stats,
        legacy.config,
        legacy.tool_manager,
        legacy.agent_profile,
    )
    legacy_id = legacy.session_id
    await legacy.aclose()

    host = _harness_backend_host(config)
    started = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(worktree_cwd)))
    )
    await started.backend.start_turn(
        TurnStartParams(
            session_id=started.backend.session_id,
            message=[TextContentBlock(text="persist nested retained session")],
        )
    )
    await started.backend.shutdown()
    WorktreeClaim(bucket="nested-test", name="retained").write_recovery(
        WorktreeRecoveryRecord(
            name="retained",
            branch="vibe/retained",
            repo_root=nested_repo_cwd,
            base_commit="0" * 40,
            snapshot_ref="refs/vibe/reaped/retained",
            removed_at=datetime.now(UTC),
        )
    )
    shutil.rmtree(worktree_root)

    parent_list = await host.list(SessionListParams(cwd=str(project_cwd)))
    nested_list = await host.list(SessionListParams(cwd=str(nested_repo_cwd)))
    await host.shutdown()

    retained_ids = {started.backend.session_id, legacy_id}
    assert retained_ids.isdisjoint(session.id for session in parent_list.items)
    assert parent_list.continue_session_id not in retained_ids
    assert retained_ids <= {session.id for session in nested_list.items}


@pytest.mark.asyncio
@pytest.mark.parametrize("pinned", [None, True, False])
@pytest.mark.parametrize("continue_matches_project", [False, True])
@pytest.mark.parametrize(
    "truncated_preview",
    [
        '/code-review please <skill_content name="code-rev…',
        '/code-review please <skill_content name="Code-Rev…',
        '/code-review please <skill_content name="Code-Review"…',
        '/code-review please <skill_content name="Code-Review">…',
        '/code-review please <skill_content name="code-review">…',
        '/code-review please <skill_content name="code-review"> # Skill: code-rev…',
    ],
)
async def test_unified_list_uses_latest_matching_session_for_continue(
    tmp_path: Path,
    pinned: bool | None,
    continue_matches_project: bool,
    truncated_preview: str,
) -> None:
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus as HarnessIdleSessionStatus,
        PublicSession as HarnessPublicSession,
    )
    from mistralai_vibe_local_harness.vibe._host import (
        HarnessSessionListItem,
        HarnessSessionListResult,
    )
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    project = tmp_path / "project"
    project.mkdir()
    older_id = "019ffb1e-741d-7f90-84df-ef66011876c1"
    newer_id = "019ffb1e-741d-7f90-84df-ef66011876c2"
    outside_id = "019ffb1e-741d-7f90-84df-ef66011876c3"
    user_preview = (
        "/code-review please <skill_content "
        'name="code-review">private </skill_content> body'
    )

    def item(
        session_id: str, cwd: Path, updated_at: int, *, preview: str = ""
    ) -> HarnessSessionListItem:
        return HarnessSessionListItem(
            session=HarnessPublicSession(
                id=session_id,
                preview=preview,
                status=HarnessIdleSessionStatus(),
                created_at=updated_at,
                updated_at=updated_at,
            ),
            cwd=str(cwd),
        )

    real_host = vibe_runtime.create_harness_host()

    class FakeHost:
        def __getattr__(self, name: str) -> Any:
            return getattr(real_host, name)

        async def list(self, **_kwargs: Any) -> HarnessSessionListResult:
            return HarnessSessionListResult(
                items=(
                    item(older_id, project, 1, preview=user_preview),
                    item(newer_id, project, 2, preview=truncated_preview),
                    item(outside_id, tmp_path / "outside", 3),
                ),
                continue_session_id=older_id
                if continue_matches_project
                else outside_id,
            )

    host = adapt_harness_host(
        FakeHost(),
        _test_session_runtime_builder(
            build_test_vibe_config(
                session_logging=SessionLoggingConfig(
                    enabled=True, save_dir=str(tmp_path / "sessions")
                )
            )
        ),
    )

    listed = await host.list(SessionListParams(cwd=str(project), pinned=pinned))

    assert listed.continue_session_id == (
        older_id if continue_matches_project else newer_id
    )
    assert {item.id for item in listed.items} == (
        set() if pinned else {older_id, newer_id}
    )
    if not pinned:
        listed_by_id = {session.id: session for session in listed.items}
        assert listed_by_id[older_id].preview == user_preview
        assert listed_by_id[newer_id].preview == "/code-review please"


def test_legacy_and_unified_hosts_share_the_same_lease_namespace(
    tmp_path: Path,
) -> None:
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._storage import SessionLease as HarnessLease

    session_id = "019ffb1e-741d-7f90-84df-ef66011876ca"
    legacy = SessionLease(tmp_path, session_id).acquire()
    try:
        with pytest.raises(vibe_runtime.HarnessSessionBusyError):
            HarnessLease(tmp_path, session_id).acquire()
    finally:
        legacy.release()

    unified = HarnessLease(tmp_path, session_id).acquire()
    try:
        with pytest.raises(SessionBusyError):
            SessionLease(tmp_path, session_id).acquire()
    finally:
        unified.release()


def test_vibe_token_usage_tolerates_unknown_harness_fields() -> None:
    """A harness that adds a usage field must not break the client.

    The harness runtime upgrades independently of the client (self-hosted skew,
    or a ``--with-editable`` dev harness). When it adds a field such as
    ``cachedInputTokens`` the client must ignore the unknown extra rather than
    reject the whole session read — the failure that hard-crashed resume. See
    ADR 0014.
    """
    session_protocol = pytest.importorskip(
        "mistralai_vibe_local_harness.session_protocol"
    )
    from vibe.app_server._unified_harness_backend_adapter import _vibe_token_usage

    harness_usage = session_protocol.TokenUsage(
        input_tokens=10, output_tokens=5, total_tokens=15, cached_input_tokens=4
    )

    result = _vibe_token_usage(harness_usage)

    assert result is not None
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert result.total_tokens == 15


def test_vibe_token_usage_passes_through_none() -> None:
    pytest.importorskip("mistralai_vibe_local_harness")
    from vibe.app_server._unified_harness_backend_adapter import _vibe_token_usage

    assert _vibe_token_usage(None) is None


def test_public_turn_error_tolerates_unknown_harness_fields() -> None:
    """Reading a harness turn error ignores fields the client does not know.

    Every harness->client read goes through ``validate_backend_wire``; a harness
    that adds a field to its error shape must not crash the read (ADR 0014).
    """
    pytest.importorskip("mistralai_vibe_local_harness")
    from vibe.app_server._unified_harness_backend_adapter import _public_turn_error

    class _HarnessError:
        def model_dump(self, *, mode: str = "json", by_alias: bool = True) -> dict:
            return {"message": "boom", "code": "some_code", "newHarnessField": 1}

    result = _public_turn_error(_HarnessError())

    assert result is not None
    assert result.message == "boom"
    assert result.code == "some_code"


# Reads. None may build a session context.
_READ_ONLY_HOST_OPERATIONS = frozenset({"delete", "list", "pin", "read", "rename"})

# Bind, move or fork a live runtime, so they legitimately build one.
_LIFECYCLE_HOST_OPERATIONS = frozenset({
    "clear_history",
    "continue_latest",
    "fork",
    "resume",
    "rewind_fork",
    "start",
})

# Neither reads nor binds a runtime, so neither classification applies.
_NON_SESSION_HOST_OPERATIONS = frozenset({
    "harness_kind",
    "shutdown",
    "stop_background_tasks",
})


@pytest.mark.asyncio
async def test_listing_several_cwds_returns_their_union_in_one_request(
    tmp_path: Path,
) -> None:
    """`cwds` answers for many checkouts at once, and only those checkouts."""
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    wanted = [(tmp_path / "one").resolve(), (tmp_path / "two").resolve()]
    unwanted = (tmp_path / "three").resolve()
    for directory in [*wanted, unwanted]:
        directory.mkdir()

    host = _harness_backend_host(config)
    ids: dict[Path, str] = {}
    for directory in [*wanted, unwanted]:
        started = await host.start(
            SessionStartParams(agent_config=SessionOptions(cwd=str(directory)))
        )
        # A session with no turn is ephemeral and never listed.
        await started.backend.start_turn(
            TurnStartParams(
                session_id=started.backend.session_id,
                message=[TextContentBlock(text="persist me")],
            )
        )
        ids[directory] = started.backend.session_id
        await started.backend.shutdown()

    listed = await host.list(
        SessionListParams(cwds=[str(directory) for directory in wanted])
    )
    await host.shutdown()

    returned = {item.id for item in listed.items}
    assert returned == {ids[directory] for directory in wanted}
    assert ids[unwanted] not in returned


@pytest.mark.asyncio
async def test_listing_an_empty_cwds_matches_nothing(tmp_path: Path) -> None:
    """An explicit empty checkout set means nothing, not everything."""
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    project = (tmp_path / "project").resolve()
    project.mkdir()

    host = _harness_backend_host(config)
    started = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=str(project)))
    )
    await started.backend.start_turn(
        TurnStartParams(
            session_id=started.backend.session_id,
            message=[TextContentBlock(text="persist me")],
        )
    )
    await started.backend.shutdown()

    populated = await host.list(SessionListParams(cwds=[str(project)]))
    empty = await host.list(SessionListParams(cwds=[]))
    await host.shutdown()

    assert populated.items, "fixture produced nothing to contrast with"
    assert empty.items == []


@pytest.mark.asyncio
async def test_listing_one_cwd_matches_listing_that_cwd_in_cwds(tmp_path: Path) -> None:
    """`cwds=[x]` must mean exactly what `cwd=x` has always meant."""
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    project = (tmp_path / "project").resolve()
    other = (tmp_path / "other").resolve()
    project.mkdir()
    other.mkdir()

    host = _harness_backend_host(config)
    for directory in (project, other):
        started = await host.start(
            SessionStartParams(agent_config=SessionOptions(cwd=str(directory)))
        )
        await started.backend.start_turn(
            TurnStartParams(
                session_id=started.backend.session_id,
                message=[TextContentBlock(text="persist me")],
            )
        )
        await started.backend.shutdown()

    by_cwd = await host.list(SessionListParams(cwd=str(project)))
    by_cwds = await host.list(SessionListParams(cwds=[str(project)]))
    await host.shutdown()

    assert by_cwd.items, "fixture produced nothing to compare"
    assert [item.id for item in by_cwds.items] == [item.id for item in by_cwd.items]


def test_every_host_operation_is_classified_read_or_lifecycle() -> None:
    """Fails closed, so a new read cannot ship unclassified."""
    from vibe.app_server import _session_backend_port as port

    protocols = [
        port.SessionBackendHost,
        port.SessionBackendHostPin,
        port.SessionBackendHostDelete,
        port.SessionBackendHistoryClearHost,
        port.SessionBackendRewindForkHost,
        port.SessionBackendHostBackgroundTasks,
    ]
    declared = {
        name
        for protocol in protocols
        for name in vars(protocol)
        if not name.startswith("_")
    }
    classified = (
        _READ_ONLY_HOST_OPERATIONS
        | _LIFECYCLE_HOST_OPERATIONS
        | _NON_SESSION_HOST_OPERATIONS
    )
    assert declared - classified == set(), (
        "Unclassified Host operation(s). Add each to _READ_ONLY_HOST_OPERATIONS "
        "if it only reads stored session state, else to the lifecycle set."
    )
    assert classified - declared == set(), (
        "Classified operation(s) that no Host protocol declares any more."
    )


@pytest.mark.asyncio
async def test_read_only_host_operations_build_no_session_context(
    tmp_path: Path,
) -> None:
    """None of the read operations may resolve plugins, MCP or connectors."""
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    project_cwd = str((tmp_path / "project").resolve())
    (tmp_path / "project").mkdir()

    session_contexts: list[str | None] = []
    inner = _test_session_runtime_builder(config)

    async def counting_session_context(
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: Any = "cli",
    ) -> Any:
        session_contexts.append(options.cwd)
        return await inner(
            options, require_api_key=require_api_key, entrypoint=entrypoint
        )

    read_contexts: list[str | None] = []
    inner_read_context = _read_context_builder(config, str(tmp_path))

    async def build_read_context(options: SessionOptions) -> Any:
        read_contexts.append(options.cwd)
        return await inner_read_context(options)

    host = adapt_harness_host(
        vibe_runtime.create_harness_host(),
        counting_session_context,
        build_read_context=build_read_context,
    )
    # Starting a session is a lifecycle operation and may build one; the reads
    # below are measured against a real stored session, not an empty store.
    started = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    session_id = started.backend.session_id
    await started.backend.shutdown()
    session_contexts.clear()

    calls = {
        "list": lambda: host.list(SessionListParams(cwd=project_cwd)),
        "rename": lambda: host.rename(
            SessionTitleUpdateParams(session_id=session_id, title="Renamed")
        ),
        "pin": lambda: host.pin(SessionPinParams(session_id=session_id, pinned=True)),
        "read": lambda: host.read(SessionReadParams(session_id=session_id)),
        "delete": lambda: host.delete(SessionDeleteParams(session_id=session_id)),
    }
    assert set(calls) == _READ_ONLY_HOST_OPERATIONS

    # A failed lookup still proves the invariant: the contexts are resolved
    # before the operation can fail.
    for name in ("list", "rename", "pin", "read", "delete"):
        resolved_before = len(read_contexts)
        with contextlib.suppress(SessionBackendError):
            await calls[name]()
        assert len(read_contexts) > resolved_before, (
            f"{name} never resolved a read context"
        )
        assert session_contexts == [], (
            f"{name} built a session context: {session_contexts}"
        )
    await host.shutdown()


def _read_context_builder(config: VibeConfigSchema, storage_root: str) -> Any:
    from mistralai_vibe_local_harness.vibe import LegacyImportSource
    from vibe.app_server._unified_harness_backend_adapter import UnifiedReadContext

    orchestrator = FakeConfigOrchestrator(config)

    def load_legacy_source(session_id: str) -> LegacyImportSource:
        del session_id
        return LegacyImportSource(state="absent")

    def resolve_legacy_source(session_id: str) -> None:
        del session_id
        return None

    def build_agents() -> AgentManager:
        return AgentManager(
            orchestrator,
            orchestrator.config.default_agent,
            harness_files=get_harness_files_manager(),
        )

    async def build(options: SessionOptions) -> UnifiedReadContext:
        del options
        return UnifiedReadContext(
            storage_root=storage_root,
            config_orchestrator=cast(Any, orchestrator),
            legacy_source_loader=load_legacy_source,
            legacy_source_resolver=resolve_legacy_source,
            build_agents=build_agents,
        )

    return build


@pytest.mark.asyncio
async def test_listing_reads_the_narrow_context_not_a_session_context(
    tmp_path: Path,
) -> None:
    """A listing must not build a session context."""
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedReadContext,
        adapt_harness_host,
    )

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    project_cwd = str((tmp_path / "project").resolve())
    (tmp_path / "project").mkdir()

    session_contexts: list[str | None] = []
    inner = _test_session_runtime_builder(config)

    async def counting_session_context(
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: Any = "cli",
    ) -> Any:
        session_contexts.append(options.cwd)
        return await inner(
            options, require_api_key=require_api_key, entrypoint=entrypoint
        )

    listing_contexts: list[str | None] = []
    inner_read_context = _read_context_builder(config, str(tmp_path))

    async def build_read_context(options: SessionOptions) -> UnifiedReadContext:
        listing_contexts.append(options.cwd)
        return await inner_read_context(options)

    host = adapt_harness_host(
        vibe_runtime.create_harness_host(),
        counting_session_context,
        build_read_context=build_read_context,
    )
    await host.list(SessionListParams(cwd=project_cwd))
    await host.shutdown()

    assert listing_contexts == [project_cwd]
    assert session_contexts == []


@pytest.mark.asyncio
async def test_listing_falls_back_to_the_session_context_without_a_narrow_builder(
    tmp_path: Path,
) -> None:
    """A host without the narrow builder still lists correctly, at the old cost."""
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    project_cwd = str((tmp_path / "project").resolve())
    (tmp_path / "project").mkdir()

    session_contexts: list[str | None] = []
    inner = _test_session_runtime_builder(config)

    async def counting_session_context(
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: Any = "cli",
    ) -> Any:
        session_contexts.append(options.cwd)
        return await inner(
            options, require_api_key=require_api_key, entrypoint=entrypoint
        )

    host = adapt_harness_host(
        vibe_runtime.create_harness_host(), counting_session_context
    )
    await host.list(SessionListParams(cwd=project_cwd))
    await host.shutdown()

    assert session_contexts == [project_cwd]


def _harness_backend_host(
    config: VibeConfigSchema | None = None,
    *,
    system_instructions: Callable[[], str] | None = None,
) -> SessionBackendHost:
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    return adapt_harness_host(
        vibe_runtime.create_harness_host(),
        _test_session_runtime_builder(config, system_instructions=system_instructions),
    )


def _test_session_runtime_builder(
    config: VibeConfigSchema | None = None,
    *,
    hooks: Any = None,
    system_instructions: Callable[[], str] | None = None,
    storage_root: str | None = None,
) -> SessionContextBuilder:
    orchestrator = FakeConfigOrchestrator(config or build_test_vibe_config())

    async def build(
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: Any = "cli",
    ) -> Any:
        # This builder never resolves a credential — its orchestrator is a fake
        # — so the flag the real builder uses to decide whether a missing key
        # fails the open has nothing to gate here.
        del require_api_key
        del entrypoint

        from mistralai_vibe_local_harness.vibe import (
            CompiledHooks,
            LegacyImportSource,
            LegacySessionReference as HarnessLegacySessionReference,
            LocalRuntimeAdapterConfig,
            SessionWorkspace,
        )
        from mistralai_vibe_local_harness.vibe._host import _core_config
        from vibe.app_server._plugins import (
            UnifiedPluginProvider,
            installed_plugin_scopes,
            requested_plugin_definitions,
            resolve_session_plugins,
        )
        from vibe.app_server._unified_harness_backend_adapter import (
            UnifiedRuntimeDerivation,
            UnifiedSessionContext,
        )

        def resolve_legacy_source(
            session_id: str,
        ) -> HarnessLegacySessionReference | None:
            reference = resolve_legacy_session_reference(
                session_id, orchestrator.config.session_logging
            )
            if reference is None:
                return None
            return HarnessLegacySessionReference(
                session_id=reference.session_id, cwd=reference.cwd
            )

        def load_legacy_source(session_id: str) -> LegacyImportSource:
            try:
                export = export_legacy_committed_history(
                    session_id, orchestrator.config.session_logging
                )
            except InvalidLegacyInteropSourceError as exc:
                return LegacyImportSource(state="invalid", error=str(exc))
            if export is None:
                return LegacyImportSource(state="absent")
            return LegacyImportSource(
                state="quiescent",
                reference=HarnessLegacySessionReference(
                    session_id=export.reference.session_id, cwd=export.reference.cwd
                ),
                store_revision=export.store_revision,
                history=export.history,
                active_model=export.active_model,
            )

        harness_files = get_harness_files_manager()
        agents = AgentManager(
            orchestrator,
            options.agent or orchestrator.config.default_agent,
            harness_files=harness_files,
        )

        def derive(_settings: Any) -> Any:
            skills = SkillManager(
                lambda: orchestrator.config, harness_files=harness_files
            ).available_skills
            core_config = _core_config("runtime-template")
            if system_instructions is not None:
                core_config = core_config.model_copy(
                    update={"system_instructions": system_instructions()}
                )
            return UnifiedRuntimeDerivation(
                runtime=build_unified_runtime_snapshot(
                    orchestrator, agents, skills=skills.values()
                ),
                core_config=core_config,
                adapter_config=LocalRuntimeAdapterConfig(
                    workspace=SessionWorkspace(cwd=Path(options.cwd or Path.cwd()))
                ),
                skill_payloads={},
            )

        plugins = await resolve_session_plugins(harness_files, builtin_plugin_roots=[])
        return UnifiedSessionContext(
            storage_root=(
                storage_root
                if storage_root is not None
                else orchestrator.config.session_logging.save_dir
            ),
            session_logging_enabled=orchestrator.config.session_logging.enabled,
            legacy_source_loader=load_legacy_source,
            legacy_source_resolver=resolve_legacy_source,
            plugins=plugins,
            plugin_provider=UnifiedPluginProvider(
                storage_root=Path(orchestrator.config.session_logging.save_dir),
                workdir=harness_files.cwd or Path.cwd(),
                installed_roots={
                    plugin.name: plugin.root
                    for plugin in plugins.materialized.resolution.plugins
                },
                installed_scopes=installed_plugin_scopes(plugins),
                config_orchestrator=orchestrator,
                harness_files=harness_files,
                # Match the resolve above, or a rescan discovers shipped
                # built-ins this session never started with.
                builtin_plugin_roots=[],
            ),
            requested_plugins=tuple(requested_plugin_definitions(plugins)),
            config_orchestrator=orchestrator,
            harness_files=harness_files,
            agents=agents,
            derive=derive,
            permissions=cast(Any, None),
            hooks=hooks if hooks is not None else CompiledHooks(),
            mcp_catalog=ResolvedMCPCatalog(revision="test", servers=()),
            mcp_authorization_provider=MCPAuthenticationService(),
            plugin_mcp=_empty_plugin_mcp(),
            mcp_cache_root=str(
                Path(orchestrator.config.session_logging.save_dir) / "mcp-descriptors"
            ),
            mcp_enable_system_trust_store=(
                orchestrator.config.enable_system_trust_store
            ),
        )

    return build


def _project_scoped_harness_backend_host(
    default_config: VibeConfigSchema, project_config: VibeConfigSchema, project_cwd: str
) -> SessionBackendHost:
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    default_session_context = _test_session_runtime_builder(default_config)
    project_session_context = _test_session_runtime_builder(project_config)
    default_read_context = _read_context_builder(
        default_config, default_config.session_logging.save_dir
    )
    project_read_context = _read_context_builder(
        project_config, project_config.session_logging.save_dir
    )

    def is_project(options: SessionOptions) -> bool:
        return (
            options.cwd is not None
            and Path(options.cwd).resolve() == Path(project_cwd).resolve()
        )

    async def build_session_context(
        options: SessionOptions,
        *,
        require_api_key: bool = True,
        entrypoint: Any = "cli",
    ) -> Any:
        builder = (
            project_session_context if is_project(options) else default_session_context
        )
        return await builder(
            options, require_api_key=require_api_key, entrypoint=entrypoint
        )

    async def build_read_context(options: SessionOptions) -> Any:
        builder = project_read_context if is_project(options) else default_read_context
        return await builder(options)

    return adapt_harness_host(
        vibe_runtime.create_harness_host(),
        build_session_context,
        build_read_context=build_read_context,
    )


def _connect_harness_host(
    config: VibeConfigSchema | None = None,
) -> tuple[AppServerClient, AppServer]:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    client_transport, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport,
        session_backend_host_factory=lambda _: _harness_backend_host(config),
    )
    return AppServerClient(client_transport, run_peer=server.serve), server


def _recorded_sessions(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str]]:
    matches = (_SESSION_CREATED.match(record.message) for record in caplog.records)
    return [
        (match.group("harness"), match.group("session_id"))
        for match in matches
        if match is not None
    ]


@pytest.mark.asyncio
async def test_unified_harness_serves_the_plugin_catalogue() -> None:
    client, server = _connect_harness_host()

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        response = PluginInfoResponse.model_validate(
            await client.request(
                "plugin/info", PluginInfoParams(session_id=started.state.session.id)
            )
        )
    finally:
        await server.close()

    # This project installs no plugins, so the catalogue is empty rather than
    # absent: the procedure answers, and answers about this session.
    assert response.info.components == []
    assert response.info.workdir is not None


@pytest.mark.asyncio
async def test_unified_harness_reloads_plugins_and_reports_nothing() -> None:
    """Reload rescans, re-pins through ``config/write``, and allocates nothing.

    Nothing has moved between the start and the reload here, which is the case
    worth pinning down: the rescan finds the same set, the re-pin converges on
    a byte-identical lock, and the command still succeeds. A reload that only
    worked when something had changed would be a diff, not a refresh.
    """
    client, server = _connect_harness_host()

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        reloaded = await client.request(
            "plugin/reload", {"sessionId": started.state.session.id}
        )
        after = PluginInfoResponse.model_validate(
            await client.request(
                "plugin/info", PluginInfoParams(session_id=started.state.session.id)
            )
        )
    finally:
        await server.close()

    # `{}`, because the result is read with `plugin/info` and a Session command
    # returns only an identity it allocated.
    assert reloaded == {}
    assert after.info.components == []


@pytest.mark.asyncio
async def test_unified_harness_will_not_reload_plugins_during_a_turn() -> None:
    """Idle-only, and it says so with the same conflict a second turn gets.

    Reload swaps the Core's tool catalogue and may replace the lock. Either
    under a running Turn would change the tools mid-decision, so the rejection
    has to be one a caller can act on rather than an internal error.
    """
    client, server = _connect_harness_host()

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        await client.request(
            "turn/start",
            TurnStartParams(
                session_id=started.state.session.id,
                message=[TextContentBlock(text="hello")],
            ),
        )
        with pytest.raises(AppServerResponseError) as excinfo:
            await client.request(
                "plugin/reload", {"sessionId": started.state.session.id}
            )
    finally:
        await server.close()

    assert excinfo.value.error.code is ProtocolErrorCode.CONFLICT


@pytest.mark.asyncio
@pytest.mark.parametrize("reload_runtime", [False, True])
async def test_unified_config_reload_refreshes_the_layer_stack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reload_runtime: bool
) -> None:
    """A light reload exists to pick up file-backed layers edited outside the
    session, so it must reload the orchestrator just like a full one does.
    ``reload_runtime`` gates the runtime rebuild, not the layer stack.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server import _unified_harness_backend_adapter as adapter_module
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
    )
    from vibe.app_server.protocol import ConfigReloadParams

    class CountingOrchestrator(FakeConfigOrchestrator[VibeConfigSchema]):
        reloads = 0

        async def reload(self, **_kwargs: object) -> None:
            type(self).reloads += 1

    # Isolated so the assertion measures ``reload_config`` itself: a successful
    # admin merge reloads the orchestrator as a side effect.
    async def no_admin_fetch(_orchestrator: object) -> object:
        return _admin_result(AdminConfigOutcome.DISABLED)

    monkeypatch.setattr(adapter_module, "fetch_admin_toml", no_admin_fetch)

    orchestrator = CountingOrchestrator(build_test_vibe_config())
    context, derivation = _stub_unified_context(tmp_path, orchestrator)
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(cast(Any, session), context, derivation)

    await adapter.reload_config(
        ConfigReloadParams(
            session_id=_RecordingSession.session_id, reload_runtime=reload_runtime
        )
    )

    assert CountingOrchestrator.reloads == 1
    assert session.applied == [derivation.adapter_config]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected", "notified"),
    [
        (
            "FETCH_FAILED",
            ["Admin-managed config not applied outcome=fetch_failed"],
            False,
        ),
        (
            "PARSE_FAILED",
            ["Admin-managed config not applied outcome=parse_failed"],
            True,
        ),
        (
            "APPLY_FAILED",
            ["Admin-managed config not applied outcome=apply_failed"],
            True,
        ),
        ("DISABLED", [], False),
        ("NO_API_KEY", [], False),
        ("APPLIED", [], False),
    ],
)
async def test_unified_config_reload_reports_the_admin_config_outcome(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    outcome: str,
    expected: list[str],
    notified: bool,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server import _unified_harness_backend_adapter as adapter_module
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
    )
    from vibe.app_server.protocol import ConfigReloadParams

    fetch_level = {"FETCH_FAILED", "DISABLED", "NO_API_KEY"}

    async def fetch(_orchestrator: object) -> object:
        if outcome in fetch_level:
            return _admin_result(AdminConfigOutcome[outcome], error="boom")
        return ""

    async def merge(
        _orchestrator: object, _toml_text: str, *, preflight: object = None
    ) -> object:
        return _admin_result(AdminConfigOutcome[outcome], error="boom")

    monkeypatch.setattr(adapter_module, "fetch_admin_toml", fetch)
    monkeypatch.setattr(adapter_module, "load_admin_layer", merge)

    orchestrator = FakeConfigOrchestrator[VibeConfigSchema](build_test_vibe_config())
    context, derivation = _stub_unified_context(tmp_path, orchestrator)
    session = cast(Any, _RecordingSession())
    notices: list[tuple[str, str]] = []
    session.publish_notice = lambda message, level="warning": notices.append((
        message,
        level,
    ))
    adapter = UnifiedHarnessBackendAdapter(session, context, derivation)

    with caplog.at_level(logging.WARNING, logger="vibe"):
        await adapter.reload_config(
            ConfigReloadParams(session_id=_RecordingSession.session_id)
        )

    assert [
        record.getMessage().split(" error=")[0]
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ] == expected
    await adapter.reload_config(
        ConfigReloadParams(session_id=_RecordingSession.session_id)
    )
    if notified:
        assert notices == [
            (
                "Your administrator-managed configuration could not be applied: boom",
                "error",
            )
            for _ in range(2)
        ]
    else:
        assert notices == []


@pytest.mark.asyncio
async def test_unified_config_write_applies_a_partially_failed_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ConfigWriteOpWire, ConfigWriteParams
    from vibe.core.config.patch import PatchOp

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), auto_approve=True)
    )
    orchestrator = context.config_orchestrator
    apply_patch = orchestrator.apply_patch

    async def partially_failing_apply_patch(
        operations: list[PatchOp], reason: str = "No reason", **kwargs: Any
    ) -> list[BaseException]:
        # The patch lands, then one layer is reported as having refused it.
        await apply_patch(operations, reason, **kwargs)
        return [RuntimeError("project layer is read-only")]

    monkeypatch.setattr(orchestrator, "apply_patch", partially_failing_apply_patch)
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )

    result = await adapter.write_config(
        ConfigWriteParams(
            session_id=_RecordingSession.session_id,
            ops=[ConfigWriteOpWire(op="set", path="/disabled_tools", value=["bash"])],
            reason="test",
        )
    )

    assert result.response.failures == ["project layer is read-only"]
    # The write reached the tool the Runtime is about to be asked to execute.
    assert [
        cast(Any, applied).tool_modes["file_system.bash"] for applied in session.applied
    ] == ["deny"]


def test_a_harness_hook_notice_entry_is_a_valid_public_notice() -> None:
    # Contract: the notice entry the Harness runtime appends for a user hook parses as
    # the app-server PublicNoticeEntry + HookNoticeDetail, so a hook run reaches every
    # client (CLI, Le Chat, ACP) as the same "[<hook>] <content>" line the legacy
    # backend shows. Feeds the real runtime builder to the real client validator.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._projection import public_notice_entry
    from vibe.app_server.models import (
        HookNoticeDetail,
        HookScope,
        HookSeverity,
        PublicNoticeEntry,
        validate_history_entry,
    )

    entry = public_notice_entry(
        "session-1",
        "hook-notice-1",
        kind="hook_completed",
        scope="post_tool",
        observed_at=0,
        tool_call_id="call-1",
        hook_name="deny-plop",
        status="warning",
        content="Replaced tool result (56 chars)",
    )

    parsed = validate_history_entry(entry)

    assert isinstance(parsed, PublicNoticeEntry)
    assert parsed.level == "warning"
    detail = parsed.detail
    assert isinstance(detail, HookNoticeDetail)
    assert detail.kind == "hook_completed"
    assert detail.scope is HookScope.POST_TOOL
    assert detail.hook_name == "deny-plop"
    assert detail.tool_call_id == "call-1"
    assert detail.status is HookSeverity.WARNING
    assert detail.content == "Replaced tool result (56 chars)"


@pytest.mark.parametrize(
    (
        "caller_is_source",
        "managed_fork",
        "restored",
        "expected_trust",
        "expected_rebuild",
        "revoke_caller",
    ),
    [
        (True, False, False, True, False, False),
        (False, False, False, False, True, True),
        (True, True, False, False, True, False),
        (False, True, False, False, True, True),
        (True, False, True, True, True, False),
    ],
)
def test_session_context_scope_finalizes_workspace_trust(
    tmp_path: Path,
    *,
    caller_is_source: bool,
    managed_fork: bool,
    restored: bool,
    expected_trust: bool,
    expected_rebuild: bool,
    revoke_caller: bool,
) -> None:
    """*Prepare*: Requested, stored, and resolved cwd combinations.
    *Do*: Finalize the Session options after worktree resolution.
    *Assert*: Trust and revocation stay scoped to the caller's checkout.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        _session_context_scope,
        _with_session_cwd,
    )
    from vibe.app_server.protocol import SessionOptions

    caller = (tmp_path / "caller").resolve()
    source = caller if caller_is_source else (tmp_path / "source").resolve()
    resolved_cwd = (tmp_path / "fork").resolve() if managed_fork else source
    extra = (tmp_path / "extra").resolve()
    requested = SessionOptions(
        cwd=str(caller), workspace_roots=[str(caller), str(extra)], trust_workspace=True
    )
    pinned = _with_session_cwd(requested, str(source))
    resolved = pinned.model_copy(
        update={
            "cwd": str(resolved_cwd),
            "workspace_roots": [str(resolved_cwd), str(extra)],
        }
    )

    # Do
    scope = _session_context_scope(
        requested, resolved, stored_cwd=str(source), restored=restored
    )

    # Assert
    assert scope.options.cwd == str(resolved_cwd)
    assert scope.options.trust_workspace is expected_trust
    assert scope.rebuild_context is expected_rebuild
    assert scope.trust_grants_to_revoke == ((caller,) if revoke_caller else ())


def test_pinning_a_session_cwd_rewrites_only_the_checkout_root(tmp_path: Path) -> None:
    """*Prepare*: Caller options containing its checkout and an extra root.
    *Do*: Pin the options to a stored Session cwd.
    *Assert*: The checkout root moves while trust awaits final resolution.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import _with_session_cwd
    from vibe.app_server.protocol import SessionOptions

    caller = (tmp_path / "caller").resolve()
    stored = (tmp_path / "stored").resolve()
    extra = (tmp_path / "extra").resolve()
    options = SessionOptions(
        cwd=str(caller), workspace_roots=[str(caller), str(extra)], trust_workspace=True
    )

    # Do
    pinned = _with_session_cwd(options, str(stored))

    # Assert
    assert pinned.cwd == str(stored)
    assert pinned.workspace_roots == [str(stored), str(extra)]
    assert pinned.trust_workspace is True


@pytest.mark.asyncio
async def test_unified_account_read_persists_reconciliation_but_defers_the_push(
    tmp_path: Path,
) -> None:
    """*Prepare*: A running turn and an account gateway reporting a drifted tenant.
    *Do*: Read the account, then start the next turn.
    *Assert*: The config heals at once; the derivation reaches the Runtime only
    at the next turn start.

    The Rust Core reads its settings when a turn starts, so pushing a new
    derivation between two iterations would swap the provider underneath the
    turn in flight. Losing the heal is not an option either — hence persist
    always, push when idle.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
    )
    from vibe.app_server.protocol import AccountReadParams

    # Prepare
    healed = "https://tenant.example"
    orchestrator = FakeConfigOrchestrator[VibeConfigSchema](build_test_vibe_config())
    assert orchestrator.config.vibe_base_url != healed
    context, derivation = _stub_unified_context(
        tmp_path,
        orchestrator,
        account_gateway=FakeAccountGateway(
            WhoAmIResult(
                plan_type=AccountPlanKind.CHAT, plan_name="TEAM", vibe_base=healed
            )
        ),
    )
    session = _RecordingSession()
    session.active_turn_id = "turn-1"
    adapter = UnifiedHarnessBackendAdapter(cast(Any, session), context, derivation)

    # Do
    await adapter.dispatch_extension(
        "account/read",
        AccountReadParams(session_id=_RecordingSession.session_id).model_dump(
            mode="json"
        ),
    )
    healed_during_turn = orchestrator.config.vibe_base_url
    pushed_during_turn = list(session.applied)
    session.active_turn_id = None
    await adapter.start_turn(
        TurnStartParams(
            session_id=_RecordingSession.session_id,
            message=[TextContentBlock(text="hello")],
        )
    )

    # Assert
    assert healed_during_turn == healed
    assert pushed_during_turn == []
    assert session.applied == [derivation.adapter_config]


@pytest.mark.asyncio
async def test_unified_provider_auth_read_returns_redacted_view(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``providerAuth/read`` projects the active provider without secrets.

    The response carries the model, provider, and sanitized destination; the
    credential value itself must not appear anywhere in it.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._model import validate_wire
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
    )
    from vibe.app_server.protocol import (
        ProviderAuthReadParams,
        ProviderAuthReadResponse,
    )

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "env-secret")
    orchestrator = FakeConfigOrchestrator(build_test_vibe_config())
    context, derivation = _stub_unified_context(tmp_path, orchestrator)
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, _RecordingSession()), context, derivation
    )

    # Do
    result = await adapter.dispatch_extension(
        "providerAuth/read",
        ProviderAuthReadParams(session_id=_RecordingSession.session_id).model_dump(
            mode="json"
        ),
    )
    response = validate_wire(ProviderAuthReadResponse, result.response)
    view = response.auth

    # Assert
    assert view.model_display_name == "Mistral Medium 3.5"
    assert view.provider_name == "mistral"
    assert view.api_base is not None
    assert "env-secret" not in result.response.model_dump_json()


@pytest.mark.asyncio
async def test_unified_diagnostics_logs_read_returns_vibe_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server import _unified_harness_backend_adapter as adapter_module
    from vibe.app_server.protocol import DiagnosticsLogsReadParams
    from vibe.core.log_reader import LogReader

    log_file = tmp_path / "vibe.log"
    log_file.write_text(
        "2026-08-28T12:00:00.000000+00:00 1 2 DEBUG Unified log entry\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(adapter_module, "LogReader", lambda: LogReader(log_file))
    adapter = _inert_adapter(_RecordingSession(), str(tmp_path), str(tmp_path))

    result = await adapter.dispatch_extension(
        "diagnostics/logs/read",
        DiagnosticsLogsReadParams(session_id=_RecordingSession.session_id).model_dump(
            mode="json"
        ),
    )

    assert result.response.logs.entries[0].message == "Unified log entry"


@pytest.mark.asyncio
async def test_unified_narration_summary_passes_session_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    config = build_test_vibe_config()
    launch_context = LaunchContext(
        agent_entrypoint="cli",
        agent_version="0",
        client_name="test-client",
        client_version="0",
    )
    backend = FakeBackend(mock_llm_chunk(content="Concise summary"))
    monkeypatch.setattr(narration_module, "create_backend", lambda **_: backend)
    harness_session = _RecordingSession()
    adapter = _inert_adapter(harness_session, str(tmp_path), str(tmp_path))
    adapter._context = SimpleNamespace(
        config_orchestrator=SimpleNamespace(config=config)
    )
    harness_session.parent_session_id = "parent-1"
    adapter._user_plan = "pro"
    adapter._launch_context = launch_context
    params = NarrationSummarizeParams(
        session_id=_RecordingSession.session_id,
        user_message="Fix the bug",
        assistant_text="Changed the parser",
    )

    result = await adapter.dispatch_extension(
        "narration/summarize", params.model_dump(mode="json")
    )

    assert result.response == NarrationSummarizeResponse(summary="Concise summary")
    metadata = backend.requests_metadata[0]
    assert metadata is not None
    assert metadata["session_id"] == _RecordingSession.session_id
    assert metadata["parent_session_id"] == "parent-1"
    assert metadata["user_plan"] == "pro"
    assert metadata["client_name"] == "test-client"


class _SubscribingSession(_RecordingSession):
    def __init__(
        self,
        usage: tuple[int, int] | None,
        context_usage: tuple[int, int] | None = None,
    ) -> None:
        super().__init__()
        self._usage = usage
        self._context_usage = context_usage

    async def subscribe(self, _params: Any) -> Any:
        from mistralai_vibe_local_harness.session_protocol import (
            IdleSessionStatus as HarnessIdleSessionStatus,
            LatestPublicHistoryPage,
            PublicSession as HarnessPublicSession,
            PublicSessionState as HarnessPublicSessionState,
            SessionSnapshot as HarnessSessionSnapshot,
            TokenUsage as HarnessTokenUsage,
            TurnQueue as HarnessTurnQueue,
        )

        async def events() -> AsyncIterator[Any]:
            return
            yield

        return SimpleNamespace(
            snapshot=HarnessSessionSnapshot(
                state=HarnessPublicSessionState(
                    session=HarnessPublicSession(
                        id=self.session_id,
                        status=HarnessIdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                        token_usage=(
                            None
                            if self._usage is None
                            else HarnessTokenUsage(
                                input_tokens=self._usage[0],
                                output_tokens=self._usage[1],
                                total_tokens=sum(self._usage),
                            )
                        ),
                        context_usage=(
                            None
                            if self._context_usage is None
                            else HarnessTokenUsage(
                                input_tokens=self._context_usage[0],
                                output_tokens=self._context_usage[1],
                                total_tokens=sum(self._context_usage),
                            )
                        ),
                    ),
                    history=LatestPublicHistoryPage(entries=[]),
                    turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
                ),
                history_limit=0,
                watermark=0,
            ),
            events=events(),
        )


def _priced_runtime(**config: Any) -> RuntimeSnapshot:
    orchestrator = FakeConfigOrchestrator[VibeConfigSchema](
        build_test_vibe_config(**config)
    )
    agents = AgentManager(
        orchestrator,
        orchestrator.config.default_agent,
        harness_files=get_harness_files_manager(),
    )
    return build_unified_runtime_snapshot(orchestrator, agents)


def _stats_adapter(tmp_path: Path, session: object | None = None) -> Any:
    return _inert_adapter(
        session if session is not None else _RecordingSession(),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
    )


def _usage_state(
    *,
    usage: tuple[int, int] | None = None,
    context_usage: tuple[int, int] | None = None,
    messages: list[tuple[str, Literal["system", "user", "assistant"]]] | None = None,
    turn: PublicTurnStatus | None = None,
) -> PublicSessionState:
    return PublicSessionState(
        event_id=0,
        session=PublicSession(
            id=_RecordingSession.session_id,
            status=IdleSessionStatus(),
            created_at=1,
            updated_at=1,
            token_usage=(
                None
                if usage is None
                else TokenUsage(
                    input_tokens=usage[0],
                    output_tokens=usage[1],
                    total_tokens=sum(usage),
                )
            ),
            context_usage=(
                None
                if context_usage is None
                else TokenUsage(
                    input_tokens=context_usage[0],
                    output_tokens=context_usage[1],
                    total_tokens=sum(context_usage),
                )
            ),
        ),
        history=[
            PublicMessageEntry(
                id=entry_id,
                session_id=_RecordingSession.session_id,
                turn_id="turn-1",
                created_at=1,
                updated_at=1,
                generation_status=PublicEntryGenerationStatus.COMPLETED,
                role=role,
                content=[TextContentBlock(text=entry_id)],
            )
            for entry_id, role in messages or []
        ],
        turns=(
            []
            if turn is None
            else [
                PublicTurn(
                    id="turn-1",
                    session_id=_RecordingSession.session_id,
                    status=turn,
                    started_at=1,
                    completed_at=2 if turn is PublicTurnStatus.COMPLETED else None,
                )
            ]
        ),
    )


def test_unified_retry_state_emits_snapshot_and_compatibility_notification(
    tmp_path: Path,
) -> None:
    """*Prepare*: A running Unified session before and during a provider retry.
    *Do*: Translate the retry start and clear snapshots.
    *Assert*: Numbered snapshots carry both transitions and retry start keeps legacy parity.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _stats_adapter(tmp_path)
    idle = _usage_state(turn=PublicTurnStatus.IN_PROGRESS)
    retry = PublicRetryState(
        turn_id="turn-1", category=PublicRetryCategory.RATE_LIMITED, detail="HTTP 429"
    )
    retrying = idle.model_copy(update={"retrying": retry})

    # Do
    started, current = adapter._translate_snapshot_update(idle, retrying)
    cleared, final = adapter._translate_snapshot_update(current, idle)

    # Assert
    assert [event.method for event in started] == ["session/snapshot", "turn/retrying"]
    assert isinstance(started[0].event, SessionSnapshot)
    assert started[0].event.state.retrying == retry
    assert started[0].event.state.event_id == started[0].event_id
    assert isinstance(started[1].event, TurnRetrying)
    assert started[1].event.params.category is PublicRetryCategory.RATE_LIMITED
    assert started[1].event_id is None
    assert [event.method for event in cleared] == ["session/snapshot"]
    assert isinstance(cleared[0].event, SessionSnapshot)
    assert cleared[0].event.state.retrying is None
    assert final.retrying is None


def test_unified_snapshot_update_carrying_a_queue_change_emits_queue_event(
    tmp_path: Path,
) -> None:
    """*Prepare*: Two Unified snapshots that differ only in the turn queue.
    *Do*: Translate the second snapshot against the first.
    *Assert*: The queue change is enveloped as ``turn/queueUpdated``.

    The Harness stamps its turn queue onto every state event it publishes, so a
    state update minted for some other reason can be the first place a queue
    change is seen. ``reconcile_snapshot`` reports that as a queue event like
    any other, and an envelope that cannot carry it takes the whole app-server
    down with an unhandled ``TypeError``.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _stats_adapter(tmp_path)
    idle = _usage_state(turn=PublicTurnStatus.IN_PROGRESS)
    queued = idle.model_copy(
        update={
            "turn_queue": PublicTurnQueue(
                items=[
                    PublicQueuedTurn(
                        id="queue-1",
                        created_at=2,
                        entries=[
                            TurnUserInputEntry(
                                content=[SessionTextContentBlock(text="queued")]
                            )
                        ],
                    )
                ]
            )
        },
        deep=True,
    )

    # Do
    translated, current = adapter._translate_snapshot_update(idle, queued)

    # Assert
    assert [event.method for event in translated] == ["turn/queueUpdated"]
    event = translated[0]
    assert isinstance(event.event, TurnQueueUpdated)
    assert event.session_id == _RecordingSession.session_id
    assert [item.id for item in event.event.queue.items] == ["queue-1"]
    assert current.turn_queue == queued.turn_queue


@pytest.mark.asyncio
async def test_unified_child_retry_state_emits_compatibility_notification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A registered child session before, during, and after a provider retry.
    *Do*: Translate the child's retry start and clear state updates.
    *Assert*: Retry start emits a child notification without exposing child snapshots.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _stats_adapter(tmp_path)
    idle = _usage_state(turn=PublicTurnStatus.IN_PROGRESS)
    child_id = "session-child"
    idle = idle.model_copy(
        update={
            "session": idle.session.model_copy(update={"id": child_id}),
            "turns": [
                turn.model_copy(update={"session_id": child_id})
                for turn in idle.turns or []
            ],
        }
    )
    retry = PublicRetryState(
        turn_id="turn-1", category=PublicRetryCategory.RATE_LIMITED, detail="HTTP 429"
    )
    retrying = idle.model_copy(update={"retrying": retry})
    adapter._child_states[child_id] = idle
    states = iter(((retrying, 1), (idle, 2)))
    monkeypatch.setattr(
        adapter, "_updated_state", lambda *_args, **_kwargs: next(states)
    )
    wrapper = {
        "type": "child_session_event",
        "sessionId": child_id,
        "eventId": 1,
        "event": {"type": "session_state_updated", "sessionId": child_id},
    }

    # Do
    started, root, _ = await adapter._translate_child_event(wrapper, 20, _usage_state())
    cleared, _final, _ = await adapter._translate_child_event(
        {**wrapper, "eventId": 2}, 20, root
    )

    # Assert
    notices = [event for event in started if event.method == "turn/retrying"]
    assert len(notices) == 1
    assert isinstance(notices[0].event, TurnRetrying)
    assert notices[0].session_id == child_id
    assert notices[0].event.params.category is PublicRetryCategory.RATE_LIMITED
    assert notices[0].event_id is None
    assert not any(isinstance(event.event, SessionSnapshot) for event in started)
    assert cleared == []
    assert adapter._child_states[child_id].retrying is None


def test_unified_runtime_snapshot_prices_the_active_model() -> None:
    active = build_test_vibe_config().get_active_model()
    assert active.input_price > 0

    stats = _priced_runtime().stats

    assert stats.input_price_per_million == active.input_price
    assert stats.output_price_per_million == active.output_price
    assert stats.cached_input_price_per_million == active.cached_input_price
    assert stats.steps == 0
    assert stats.session_prompt_tokens == 0


def test_unified_stats_report_session_totals_and_the_last_call_delta(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _stats_adapter(tmp_path)
    first = _usage_state(usage=(1000, 200), context_usage=(1000, 200))
    second = _usage_state(usage=(2500, 350), context_usage=(1500, 150))

    adapter._translate_snapshot_update(_usage_state(), first)
    after_first = adapter.runtime_updated_params().runtime.stats
    adapter._translate_snapshot_update(first, second)
    after_second = adapter.runtime_updated_params().runtime.stats

    assert after_first.session_prompt_tokens == 1000
    assert after_first.session_completion_tokens == 200
    assert after_first.last_turn_total_tokens == 1200
    assert after_first.context_tokens == 1200
    assert after_second.session_prompt_tokens == 2500
    assert after_second.session_completion_tokens == 350
    assert after_second.last_turn_prompt_tokens == 1500
    assert after_second.last_turn_completion_tokens == 150
    assert after_second.context_tokens == 1650
    assert after_second.session_cost > 0


def test_unified_stats_context_tokens_use_last_call_not_turn_sum(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _stats_adapter(tmp_path)
    # token_usage = billing sum across 3 calls; context_usage = last call only.
    first = _usage_state(usage=(1000, 200), context_usage=(1000, 200))
    second = _usage_state(
        usage=(303_000, 75),  # 100k + 101k + 102k prompt, 20+30+25 completion
        context_usage=(102_000, 25),  # last call's context size
    )

    adapter._translate_snapshot_update(_usage_state(), first)
    after_first = adapter.runtime_updated_params().runtime.stats
    adapter._translate_snapshot_update(first, second)
    after_second = adapter.runtime_updated_params().runtime.stats

    # Billing totals are cumulative sums (correct for cost).
    assert after_first.session_prompt_tokens == 1000
    assert after_second.session_prompt_tokens == 303_000
    assert after_second.session_completion_tokens == 75

    # Context gauge = last call's prompt + completion, not the turn's total spend.
    assert after_first.context_tokens == 1200
    assert after_second.context_tokens == 102_025  # 102_000 + 25, not 303_075


def test_unified_stats_hold_the_context_gauge_without_context_usage(
    tmp_path: Path,
) -> None:
    """*Prepare*: Snapshots that bill more tokens but report no measured context.
    *Do*: Translate both updates.
    *Assert*: The gauge holds rather than adopting the billed delta.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _stats_adapter(tmp_path)
    measured = _usage_state(usage=(1000, 200), context_usage=(1000, 200))
    unmeasured = _usage_state(usage=(2500, 350))

    # Do
    adapter._translate_snapshot_update(_usage_state(), measured)
    after_measured = adapter.runtime_updated_params().runtime.stats
    adapter._translate_snapshot_update(measured, unmeasured)
    after_unmeasured = adapter.runtime_updated_params().runtime.stats

    # Assert
    assert after_measured.context_tokens == 1200
    # The billed delta (1500 + 150) sums every call the snapshot covers, and each
    # of those prompts is close to the whole context, so using it as the gauge
    # inflates the reading. Holding the last measurement understates at worst.
    assert after_unmeasured.session_prompt_tokens == 2500
    assert after_unmeasured.context_tokens == 1200


def test_unified_stats_hold_the_last_call_when_a_snapshot_adds_no_usage(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _stats_adapter(tmp_path)
    completed = _usage_state(usage=(1000, 200), context_usage=(1000, 200))
    queued = _usage_state(
        usage=(1000, 200), context_usage=(1000, 200), messages=[("entry-0", "user")]
    )

    adapter._translate_snapshot_update(_usage_state(), completed)
    adapter._translate_snapshot_update(completed, queued)
    stats = adapter.runtime_updated_params().runtime.stats

    assert stats.last_turn_total_tokens == 1200
    assert stats.context_tokens == 1200


def test_unified_stats_empty_the_gauge_when_a_compaction_bills_nothing(
    tmp_path: Path,
) -> None:
    """*Prepare*: A compaction that reset the measured context but billed nothing.
    *Do*: Translate the update.
    *Assert*: The gauge empties even though spend did not move.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _stats_adapter(tmp_path)
    measured = _usage_state(usage=(1000, 200), context_usage=(1000, 200))
    # The provider reported no usage on the compaction call, so the billed totals
    # are unchanged -- but the context it replaced is gone all the same.
    compacted = _usage_state(usage=(1000, 200), context_usage=(0, 0))

    # Do
    adapter._translate_snapshot_update(_usage_state(), measured)
    adapter._translate_snapshot_update(measured, compacted)
    stats = adapter.runtime_updated_params().runtime.stats

    # Assert
    assert stats.context_tokens == 0
    assert stats.session_prompt_tokens == 1000
    assert stats.session_completion_tokens == 200


def test_unified_stats_empty_the_gauge_when_a_compaction_bills_nothing_at_all(
    tmp_path: Path,
) -> None:
    """*Prepare*: A compacted snapshot carrying no billed usage whatsoever.
    *Do*: Translate the update.
    *Assert*: The gauge still empties instead of holding the pre-compaction read.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _stats_adapter(tmp_path)
    measured = _usage_state(usage=(1000, 200), context_usage=(1000, 200))
    compacted = _usage_state(context_usage=(0, 0))

    # Do
    adapter._translate_snapshot_update(_usage_state(), measured)
    adapter._translate_snapshot_update(measured, compacted)
    stats = adapter.runtime_updated_params().runtime.stats

    # Assert
    assert stats.context_tokens == 0


def test_unified_snapshot_stats_use_context_usage_for_child_gauge() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import _snapshot_stats

    state = _usage_state(usage=(303_000, 75), context_usage=(102_000, 25))
    stats = _snapshot_stats(state)

    assert stats.session_prompt_tokens == 303_000
    assert stats.session_completion_tokens == 75
    assert stats.context_tokens == 102_025


def test_unified_snapshot_stats_report_no_context_without_context_usage() -> None:
    """*Prepare*: A snapshot that bills tokens but reports no measured context.
    *Do*: Read its stats.
    *Assert*: Spend is reported; the gauge is not guessed from the billed total.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import _snapshot_stats

    state = _usage_state(usage=(300_000, 200))

    # Do
    stats = _snapshot_stats(state)

    # Assert
    assert stats.session_prompt_tokens == 300_000
    # The cumulative prompt counts every call's context over again, so a session
    # nowhere near its window would read as three times past it.
    assert stats.context_tokens == 0


def test_unified_stats_approximate_steps_from_the_committed_messages(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _stats_adapter(tmp_path)
    asked = _usage_state(messages=[("entry-0", "user")])
    answered = _usage_state(
        messages=[("entry-0", "user"), ("entry-1", "assistant"), ("entry-2", "system")]
    )

    adapter._translate_snapshot_update(_usage_state(), asked)
    adapter._translate_snapshot_update(asked, answered)

    assert adapter.runtime_updated_params().runtime.stats.steps == 2


def test_unified_stats_reach_the_client_before_the_turn_completes(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _stats_adapter(tmp_path)
    previous = _usage_state(turn=PublicTurnStatus.IN_PROGRESS)
    current = _usage_state(usage=(1000, 200), turn=PublicTurnStatus.COMPLETED)

    events, _ = adapter._translate_snapshot_update(previous, current)
    methods = [event.method for event in events]
    ids = [event.event_id for event in events]

    assert methods.count("session/statsUpdated") == 1
    assert methods.index("session/statsUpdated") < methods.index("turn/completed")
    assert ids == list(range(ids[0], ids[0] + len(ids)))
    stats_event = events[methods.index("session/statsUpdated")]
    assert stats_event.params.stats.last_turn_total_tokens == 1200
    assert stats_event.params.context_window > 0


def test_unified_stats_stay_put_when_a_snapshot_changes_nothing(tmp_path: Path) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _stats_adapter(tmp_path)
    state = _usage_state(usage=(1000, 200))
    adapter._translate_snapshot_update(_usage_state(), state)

    events, _ = adapter._translate_snapshot_update(state, state)

    assert events == []


@pytest.mark.asyncio
async def test_unified_stats_seed_from_a_resumed_sessions_running_totals(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _stats_adapter(tmp_path, session=_SubscribingSession((4000, 900)))

    await adapter.subscribe(SessionReadParams(session_id=_RecordingSession.session_id))
    stats = adapter.runtime_updated_params().runtime.stats

    assert stats.session_prompt_tokens == 4000
    assert stats.session_completion_tokens == 900
    assert stats.last_turn_total_tokens == 0
    assert stats.context_tokens == 0
    assert stats.input_price_per_million > 0


@pytest.mark.asyncio
async def test_unified_stats_seed_the_context_gauge_a_resumed_session_measured(
    tmp_path: Path,
) -> None:
    """*Prepare*: A resumed session whose last call measured a 102k context.
    *Do*: Subscribe to it.
    *Assert*: The gauge starts at that measurement, not at zero.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _stats_adapter(
        tmp_path, session=_SubscribingSession((303_000, 75), (102_000, 25))
    )

    # Do
    await adapter.subscribe(SessionReadParams(session_id=_RecordingSession.session_id))

    # Assert
    stats = adapter.runtime_updated_params().runtime.stats
    assert stats.session_prompt_tokens == 303_000
    # Seeding only the billed totals left a resumed session reading empty until
    # it made a call of its own, hiding a context already close to its window.
    assert stats.context_tokens == 102_025


@pytest.mark.asyncio
async def test_account_host_reconciles_the_experiment_manager_snapshot() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        ExperimentsInitGate,
        UserPlanFallback,
        _UnifiedAccountHost,
        _user_plan_from_manager,
    )
    from vibe.core.experiments.active import ExperimentSurface
    from vibe.core.experiments.manager import ExperimentManager
    from vibe.core.experiments.models import ExperimentAttributes
    from vibe.setup.auth.whoami import WhoAmICache, resolve_user_plan

    manager = ExperimentManager()
    manager.set_attributes(
        ExperimentAttributes(
            entrypoint="cli",
            harness=ExperimentSurface.UNIFIED,
            agent_version="0",
            os="darwin",
            planType=None,
            planName=None,
        )
    )
    context = cast(
        Any,
        SimpleNamespace(
            whoami_cache=WhoAmICache(),
            experiment_manager=manager,
            user_plan_fallback=UserPlanFallback(),
            experiments_init_gate=ExperimentsInitGate(),
        ),
    )
    host = _UnifiedAccountHost(context)

    await host.apply_account_whoami(
        console_base_url="https://console.example",
        api_key="sk-test",
        whoami=WhoAmIResult(
            plan_type=AccountPlanKind.CHAT,
            plan_name="TEAM",
            customer_id="cust-1",
            organization_kind="C",
        ),
    )

    attributes = manager.attributes()
    assert attributes is not None
    assert attributes.planName == "TEAM"
    assert attributes.customerId == "cust-1"
    assert _user_plan_from_manager(manager) == resolve_user_plan(
        AccountPlanKind.CHAT.value, "TEAM"
    )


@pytest.mark.asyncio
async def test_experiments_init_gate_waits_for_the_tracked_task() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import ExperimentsInitGate

    gate = ExperimentsInitGate()
    # No task tracked: wait is a no-op.
    await gate.wait()

    done: list[bool] = []

    async def work() -> None:
        await asyncio.sleep(0)
        done.append(True)

    task = asyncio.create_task(work())
    gate.track(task)
    await gate.wait()
    assert done == [True]
    # Already-finished task: wait returns immediately.
    await gate.wait()


@pytest.mark.asyncio
async def test_experiments_init_gate_propagates_waiter_cancellation() -> None:
    """Cancelling the reconcile while it waits must not be swallowed by the gate."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import ExperimentsInitGate

    gate = ExperimentsInitGate()
    started = asyncio.Event()

    async def slow_init() -> None:
        started.set()
        await asyncio.sleep(3600)

    task = asyncio.create_task(slow_init())
    gate.track(task)
    await started.wait()

    waiter = asyncio.create_task(gate.wait())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter

    # The tracked init task is left untouched by the gate; clean it up.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_experiments_init_gate_ignores_tracked_task_cancellation() -> None:
    """A cancelled init task settles the wait without raising into the reconcile."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import ExperimentsInitGate

    gate = ExperimentsInitGate()
    task = asyncio.create_task(asyncio.sleep(3600))
    gate.track(task)

    waiter = asyncio.create_task(gate.wait())
    await asyncio.sleep(0)
    task.cancel()
    # wait returns normally even though the tracked task was cancelled.
    await waiter


@pytest.mark.asyncio
async def test_whoami_reconcile_wins_over_in_flight_experiments_init() -> None:
    """*Prepare*: a stale-cache init that lands its snapshot late, tracked by the gate.
    *Do*: Reconcile a live /whoami while that init is in flight.
    *Assert*: The reconcile awaits init and its plan wins over the stale snapshot.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        ExperimentsInitGate,
        UserPlanFallback,
        _UnifiedAccountHost,
    )
    from vibe.core.experiments.active import ExperimentSurface
    from vibe.core.experiments.manager import ExperimentManager
    from vibe.core.experiments.models import ExperimentAttributes
    from vibe.setup.auth.whoami import WhoAmICache

    manager = ExperimentManager()
    manager.set_attributes(
        ExperimentAttributes(
            entrypoint="cli",
            harness=ExperimentSurface.UNIFIED,
            agent_version="0",
            os="darwin",
            planType=None,
            planName=None,
        )
    )
    gate = ExperimentsInitGate()

    async def stale_init() -> None:
        # A background init that read a stale disk-cache whoami, landing late.
        await asyncio.sleep(0)
        snapshot = manager.attributes()
        assert snapshot is not None
        manager.set_attributes(snapshot.model_copy(update={"planName": "STALE"}))

    task = asyncio.create_task(stale_init())
    gate.track(task)
    context = cast(
        Any,
        SimpleNamespace(
            whoami_cache=WhoAmICache(),
            experiment_manager=manager,
            user_plan_fallback=UserPlanFallback(),
            experiments_init_gate=gate,
        ),
    )
    host = _UnifiedAccountHost(context)

    await host.apply_account_whoami(
        console_base_url="https://console.example",
        api_key="sk-test",
        whoami=WhoAmIResult(
            plan_type=AccountPlanKind.CHAT,
            plan_name="TEAM",
            customer_id="cust-1",
            organization_kind="C",
        ),
    )
    await task

    attributes = manager.attributes()
    assert attributes is not None
    # Init ran first (the reconcile awaited it), so the live plan is not clobbered.
    assert attributes.planName == "TEAM"


@pytest.mark.asyncio
async def test_account_whoami_populates_user_plan_without_a_snapshot() -> None:
    # Mid-session sign-in: experiments never stamped an attribute snapshot, but a
    # successful /whoami must still populate user_plan for telemetry (the account
    # fallback), at parity with the legacy backend's _user_plan field.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        ExperimentsInitGate,
        UserPlanFallback,
        _UnifiedAccountHost,
        _user_plan_for_telemetry,
    )
    from vibe.core.experiments.manager import ExperimentManager
    from vibe.setup.auth.whoami import WhoAmICache, resolve_user_plan

    manager = ExperimentManager()
    assert manager.attributes() is None
    context = cast(
        Any,
        SimpleNamespace(
            whoami_cache=WhoAmICache(),
            experiment_manager=manager,
            user_plan_fallback=UserPlanFallback(),
            experiments_init_gate=ExperimentsInitGate(),
        ),
    )
    host = _UnifiedAccountHost(context)

    await host.apply_account_whoami(
        console_base_url="https://console.example",
        api_key="sk-test",
        whoami=WhoAmIResult(
            plan_type=AccountPlanKind.CHAT,
            plan_name="TEAM",
            customer_id="cust-1",
            organization_kind="C",
        ),
    )

    # No snapshot is fabricated, but user_plan resolves through the fallback.
    assert manager.attributes() is None
    assert _user_plan_for_telemetry(
        manager, context.user_plan_fallback
    ) == resolve_user_plan(AccountPlanKind.CHAT.value, "TEAM")

    await host.clear_account_whoami(api_key="sk-test")
    assert _user_plan_for_telemetry(manager, context.user_plan_fallback) is None


@pytest.mark.asyncio
async def test_harness_start_emits_new_session_and_ready(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    host = adapt_harness_host(
        vibe_runtime.create_harness_host(),
        _test_session_runtime_builder(build_test_vibe_config()),
    )
    try:
        await host.start(SessionStartParams())

        names = [event["event_name"] for event in telemetry_events]
        assert "vibe.new_session" in names
        assert "vibe.ready" in names
    finally:
        await host.shutdown()

    assert "vibe.session_closed" in [event["event_name"] for event in telemetry_events]
    # The backend tag rides every event unconditionally — this config has no
    # Mistral key, so experiment_attributes is absent, yet harness_backend is not.
    assert all(
        event["properties"]["harness_backend"] == "unified"
        for event in telemetry_events
    )


@pytest.mark.asyncio
async def test_harness_clear_history_re_emits_new_session_but_not_ready(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    host = adapt_harness_host(
        vibe_runtime.create_harness_host(),
        _test_session_runtime_builder(build_test_vibe_config()),
    )
    started = await host.start(SessionStartParams())
    source_session_id = started.backend.session_id
    telemetry_events.clear()
    try:
        await host.clear_history(
            started.backend, SessionHistoryClearParams(session_id=source_session_id)
        )

        names = [event["event_name"] for event in telemetry_events]
        # A fresh session is announced, but ready stays a once-per-process event.
        assert "vibe.new_session" in names
        assert "vibe.ready" not in names
        # `/clear` starts a new root, mirroring the legacy reset
        # (`_reset_session(keep_parent=False)` leaves parent_session_id unset).
        # A None parent is omitted from the payload entirely.
        new_session = next(
            e for e in telemetry_events if e["event_name"] == "vibe.new_session"
        )
        assert new_session["properties"].get("parent_session_id") is None
    finally:
        await host.shutdown()


@pytest.mark.asyncio
async def test_harness_forwards_client_telemetry_to_the_datalake(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    host = adapt_harness_host(
        vibe_runtime.create_harness_host(),
        _test_session_runtime_builder(build_test_vibe_config()),
    )
    started = await host.start(SessionStartParams())
    backend = cast(Any, started.backend)
    telemetry_events.clear()
    try:
        await backend.dispatch_extension(
            "telemetry/record",
            {
                "sessionId": backend.session_id,
                "name": "vibe.slash_command_used",
                "properties": {"command": "help", "command_type": "builtin"},
            },
        )

        forwarded = [
            event
            for event in telemetry_events
            if event["event_name"] == "vibe.slash_command_used"
        ]
        assert len(forwarded) == 1
        assert forwarded[0]["properties"]["command"] == "help"
    finally:
        await host.shutdown()


@pytest.mark.asyncio
async def test_lifecycle_telemetry_carries_the_full_plan_snapshot(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    """new_session must carry complete segmentation data: user_plan AND the
    experiment_attributes snapshot (plan + org/workspace/user), sourced from the
    session's experiment manager — parity with the legacy backend.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        _user_plan_from_manager,
    )
    from vibe.core.experiments.active import ExperimentSurface
    from vibe.core.experiments.manager import ExperimentManager
    from vibe.core.experiments.models import ExperimentAttributes

    config = build_test_vibe_config(enable_telemetry=True)
    orchestrator = FakeConfigOrchestrator[VibeConfigSchema](config)
    manager = ExperimentManager()
    manager.set_attributes(
        ExperimentAttributes(
            entrypoint="cli",
            harness=ExperimentSurface.UNIFIED,
            agent_version="0",
            os="darwin",
            userId="user-1",
            organizationId="org-1",
            workspaceId="ws-1",
            customerId="cust-1",
            planType=AccountPlanKind.CHAT.value,
            planName="TEAM",
        )
    )
    context, derivation = _stub_unified_context(
        tmp_path, orchestrator, experiment_manager=manager
    )
    telemetry = TelemetryClient(
        config_getter=lambda: context.config_orchestrator.config,
        user_plan_getter=lambda: _user_plan_from_manager(manager),
        experiment_attributes_getter=manager.attributes,
    )
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, _RecordingSession()), context, derivation, telemetry_client=telemetry
    )

    telemetry_events.clear()
    adapter._emit_new_session_telemetry()

    new_session = [
        event for event in telemetry_events if event["event_name"] == "vibe.new_session"
    ]
    assert new_session
    props = new_session[0]["properties"]
    assert props["user_plan"] == _user_plan_from_manager(manager)
    attributes = props["experiment_attributes"]
    assert attributes["planName"] == "TEAM"
    assert attributes["customerId"] == "cust-1"
    assert attributes["organizationId"] == "org-1"
    assert attributes["workspaceId"] == "ws-1"
    assert attributes["userId"] == "user-1"


@pytest.mark.asyncio
async def test_ready_wait_blocks_on_the_experiments_eval(tmp_path: Path) -> None:
    # session/ready/wait must await the background experiment eval so the plan
    # snapshot is resolved (and new_session/ready fired) before the client records
    # startup-class events — parity with the legacy AgentLoop.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    resolved = False

    async def _eval() -> None:
        nonlocal resolved
        await asyncio.sleep(0.05)
        resolved = True

    adapter = _inert_adapter(_RecordingSession(), str(tmp_path), str(tmp_path))
    adapter._experiments_task = asyncio.create_task(_eval())
    result = await adapter.dispatch_extension("session/ready/wait", {})

    assert resolved is True
    assert result.response.ready is True


@pytest.mark.asyncio
async def test_ready_wait_reports_not_ready_when_eval_cancelled(tmp_path: Path) -> None:
    # A cancelled eval (rapid start/stop) means new_session/ready never fired, so
    # ready/wait must report not-ready to keep the client from emitting unpaired
    # startup telemetry — the harness twin of the legacy cancelled-init skip.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    async def _never() -> None:
        await asyncio.sleep(3600)

    adapter = _inert_adapter(_RecordingSession(), str(tmp_path), str(tmp_path))
    task = asyncio.create_task(_never())
    adapter._experiments_task = task
    task.cancel()

    result = await adapter.dispatch_extension("session/ready/wait", {})

    assert result.response.ready is False


@pytest.mark.asyncio
async def test_readiness_endpoints_report_not_ready_while_eval_is_in_flight(
    tmp_path: Path,
) -> None:
    # runtime/read and session/ready/read must report ``ready=False`` while the
    # background experiment eval is in flight, so the TUI mounts its
    # "Initializing" loader — parity with the legacy harness's deferred init.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    async def _never() -> None:
        await asyncio.sleep(3600)

    adapter = _inert_adapter(_RecordingSession(), str(tmp_path), str(tmp_path))
    adapter._experiments_task = asyncio.create_task(_never())

    # runtime/read and session/ready/read share ``_experiments_settled``;
    # exercise the read side (no session-log dependency) to prove the helper
    # reports not-ready while the eval is in flight.
    read = await adapter.dispatch_extension("session/ready/read", {})
    assert read.response.ready is False

    adapter._experiments_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await adapter._experiments_task

    read_after = await adapter.dispatch_extension("session/ready/read", {})
    assert read_after.response.ready is True


@pytest.mark.asyncio
async def test_ready_wait_blocks_on_the_connector_resolve(tmp_path: Path) -> None:
    # session/ready/wait must await the deferred connector catalog resolve so
    # the "Initializing" loader covers the network round-trip on a cold cache.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    resolved = False

    async def _resolve() -> None:
        nonlocal resolved
        await asyncio.sleep(0.05)
        resolved = True

    adapter = _inert_adapter(_RecordingSession(), str(tmp_path), str(tmp_path))
    adapter._connector_resolve_task = asyncio.create_task(_resolve())
    result = await adapter.dispatch_extension("session/ready/wait", {})

    assert resolved is True
    assert result.response.ready is True


@pytest.mark.asyncio
async def test_resume_replays_a_legacy_connector_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A real pre-fix journal written with a connector capability.
    *Do*: Resume through deferred discovery and send another message.
    *Assert*: One resume replays it, because connectors were configured first.
    """
    # Prepare
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._runtime import DurableSessionRuntime
    from mistralai_vibe_local_harness.vibe._storage import UnifiedSessionStore
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    config = build_test_vibe_config(
        enable_connectors=True,
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path)),
    )
    catalog_service = FakeConnectorCatalogService(
        FakeConnectorRegistry(
            connectors={
                "github": [RemoteTool(name="search", description="Search GitHub")]
            }
        )
    )
    base_builder = _test_session_runtime_builder(config)

    async def build_context(options: SessionOptions, **kwargs: Any) -> Any:
        context = await base_builder(options, **kwargs)
        catalog = await catalog_service.resolve_catalog(context.config_orchestrator)
        return replace(
            context,
            connector_catalog=catalog,
            connector_selection=catalog_service.resolve_selection(
                context.config_orchestrator, catalog
            ),
            connector_catalog_service=catalog_service,
        )

    first_host = adapt_harness_host(vibe_runtime.create_harness_host(), build_context)
    started = await first_host.start(SessionStartParams())
    session_id = started.backend.session_id
    # Drop the capability baseline to write the journal the way the Harness wrote it
    # before this fix. The star absorbs it on a Harness new enough to pass one, so the
    # store stays pre-fix whichever Harness the app server resolves.
    persist = UnifiedSessionStore.record_core_input
    monkeypatch.setattr(
        UnifiedSessionStore,
        "record_core_input",
        lambda store, value, transition, *_baseline: persist(store, value, transition),
    )

    async def skip_compaction(_runtime: DurableSessionRuntime) -> bool:
        return False

    monkeypatch.setattr(DurableSessionRuntime, "_compact_store", skip_compaction)
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="first message")]
        )
    )
    await first_host.shutdown()

    resolve_catalog = AsyncMock(wraps=catalog_service.resolve_catalog)
    monkeypatch.setattr(catalog_service, "resolve_catalog", resolve_catalog)

    async def build_deferred_context(options: SessionOptions, **kwargs: Any) -> Any:
        context = await base_builder(options, **kwargs)
        return replace(context, connector_catalog_service=catalog_service)

    harness = vibe_runtime.create_harness_host()
    harness_resume = AsyncMock(wraps=harness.resume)
    monkeypatch.setattr(harness, "resume", harness_resume)
    second_host = adapt_harness_host(harness, build_deferred_context)

    # Do
    resumed = await second_host.resume(SessionResumeParams(session_id=session_id))
    accepted = await resumed.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="second message")]
        )
    )
    await second_host.shutdown()

    # Assert
    # Once: the eager pass put the connector routes in place, so the journal replayed
    # on the first attempt and never needed the divergence retry.
    assert harness_resume.await_count == 1
    resolve_catalog.assert_awaited_once()
    assert accepted.response.turn.session_id == session_id


@pytest.mark.asyncio
async def test_resume_configures_connector_routes_before_host_recovery(
    tmp_path: Path,
) -> None:
    """Resume makes connector routes available before pending actions recover."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendHostAdapter,
    )

    # Prepare
    events: list[str] = []
    config = build_test_vibe_config(
        enable_connectors=True,
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path)),
    )
    context = await _test_session_runtime_builder(config)(SessionOptions())
    service = FakeConnectorCatalogService(
        FakeConnectorRegistry(
            connectors={
                "github": [RemoteTool(name="search", description="Search GitHub")]
            }
        )
    )
    resolve_catalog = service.resolve_catalog

    async def resolve(*args: Any, **kwargs: Any) -> Any:
        events.append("resolve")
        return await resolve_catalog(*args, **kwargs)

    service.resolve_catalog = AsyncMock(side_effect=resolve)
    context = replace(context, connector_catalog_service=service)
    harness = Mock()
    harness.configure_connectors.side_effect = lambda *_args, **_kwargs: events.append(
        "configure"
    )

    async def resume(*_args: Any, **_kwargs: Any) -> _RecordingSession:
        events.append("resume")
        return _RecordingSession()

    harness.resume = AsyncMock(side_effect=resume)
    host = UnifiedHarnessBackendHostAdapter(cast(Any, harness), AsyncMock())

    # Do
    _session, resolved_context = await host._resume_harness("session-1", 100, context)

    # Assert
    assert events == ["resolve", "configure", "resume"]
    assert resolved_context.connector_catalog is not None


@pytest.mark.asyncio
async def test_resume_retries_the_catalog_when_replay_diverges_without_it(
    tmp_path: Path,
) -> None:
    """A transient catalog outage before recovery is retried once replay complains.

    The eager pass is what normally carries a legacy store, so this covers the only
    way the retry is reachable: discovery failed on the way in.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe import HarnessReplayDivergenceError
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendHostAdapter,
    )
    from vibe.app_server.connector_catalog import ConnectorCatalogUnavailableError

    # Prepare
    events: list[str] = []
    config = build_test_vibe_config(
        enable_connectors=True,
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path)),
    )
    context = await _test_session_runtime_builder(config)(SessionOptions())
    service = FakeConnectorCatalogService(
        FakeConnectorRegistry(
            connectors={
                "github": [RemoteTool(name="search", description="Search GitHub")]
            }
        )
    )
    resolve_catalog = service.resolve_catalog

    async def resolve(*args: Any, **kwargs: Any) -> Any:
        events.append("resolve")
        if events.count("resolve") == 1:
            raise ConnectorCatalogUnavailableError("catalog is down")
        return await resolve_catalog(*args, **kwargs)

    service.resolve_catalog = AsyncMock(side_effect=resolve)
    context = replace(context, connector_catalog_service=service)
    harness = Mock()
    harness.configure_connectors.side_effect = lambda *_args, **_kwargs: events.append(
        "configure"
    )
    divergence = HarnessReplayDivergenceError("session-1", 7)
    assert divergence.details is not None
    divergence.details["capabilities_required"] = True

    async def resume(*_args: Any, **_kwargs: Any) -> _RecordingSession:
        events.append("resume")
        if events.count("resume") == 1:
            raise divergence
        return _RecordingSession()

    harness.resume = AsyncMock(side_effect=resume)
    host = UnifiedHarnessBackendHostAdapter(cast(Any, harness), AsyncMock())

    # Do
    _session, resolved_context = await host._resume_harness("session-1", 100, context)

    # Assert
    assert events == ["resolve", "resume", "resolve", "configure", "resume"]
    assert resolved_context.connector_catalog is not None


@pytest.mark.asyncio
async def test_ready_wait_reports_not_ready_when_connector_resolve_cancelled(
    tmp_path: Path,
) -> None:
    # A cancelled connector resolve (rapid start/stop) must report not-ready,
    # mirroring the cancelled-experiment-eval path.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    async def _never() -> None:
        await asyncio.sleep(3600)

    adapter = _inert_adapter(_RecordingSession(), str(tmp_path), str(tmp_path))
    task = asyncio.create_task(_never())
    adapter._connector_resolve_task = task
    task.cancel()

    result = await adapter.dispatch_extension("session/ready/wait", {})

    assert result.response.ready is False


@pytest.mark.asyncio
async def test_readiness_reports_not_ready_while_connector_resolve_in_flight(
    tmp_path: Path,
) -> None:
    # _experiments_settled must report not-ready while the connector resolve
    # is in flight, so the TUI mounts its "Initializing" loader.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    async def _never() -> None:
        await asyncio.sleep(3600)

    adapter = _inert_adapter(_RecordingSession(), str(tmp_path), str(tmp_path))
    adapter._connector_resolve_task = asyncio.create_task(_never())

    read = await adapter.dispatch_extension("session/ready/read", {})
    assert read.response.ready is False

    adapter._connector_resolve_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await adapter._connector_resolve_task

    read_after = await adapter.dispatch_extension("session/ready/read", {})
    assert read_after.response.ready is True


@pytest.mark.asyncio
async def test_fresh_start_telemetry_is_suppressed_after_close(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    # The host shutdown path emits session_closed without cancelling the eval task,
    # so the deferred fresh-start callback can still run afterward. It must not emit
    # new_session/ready once the session has closed.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
    )

    config = build_test_vibe_config(enable_telemetry=True)
    orchestrator = FakeConfigOrchestrator[VibeConfigSchema](config)
    context, derivation = _stub_unified_context(tmp_path, orchestrator)
    telemetry = TelemetryClient(
        config_getter=lambda: context.config_orchestrator.config
    )
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, _RecordingSession()), context, derivation, telemetry_client=telemetry
    )

    adapter._emit_session_closed_telemetry()
    telemetry_events.clear()
    adapter._emit_fresh_start_telemetry()

    assert telemetry_events == []


def test_correlation_holder_only_advances_on_a_real_id() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import CorrelationIdHolder

    holder = CorrelationIdHolder()
    assert holder.value is None
    holder.record("corr-1")
    assert holder.value == "corr-1"
    # A header-less response must not clear a previously captured id.
    holder.record(None)
    assert holder.value == "corr-1"


@pytest.mark.asyncio
async def test_fork_uses_root_cache_affinity_without_sharing_request_metadata(
    tmp_path: Path,
) -> None:
    """*Prepare*: Root, fork, and unrelated derivations.
    *Do*: Bind each to its own session attribution and the fork to its parent.
    *Assert*: Metadata stays session-local while affinity follows the root.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._completion_attribution import build_completion_attribution
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    def attribution_for(session_id: str, parent_session_id: str | None = None):
        return build_completion_attribution(
            TelemetryClient(
                config_getter=build_test_vibe_config,
                session_id_getter=lambda: session_id,
                parent_session_id_getter=lambda: parent_session_id,
            ),
            None,
        )

    # Prepare
    # Each opened session gets a fresh derivation. Building them from one context
    # also proves their attribution holders do not leak into one another.
    context = await _test_session_runtime_builder()(SessionOptions(cwd=str(tmp_path)))
    source = context.derive(UnifiedSessionSettings())
    forked = context.derive(UnifiedSessionSettings())
    unrelated = context.derive(UnifiedSessionSettings())

    # Do
    source.completion_attribution.bind(attribution_for("session-source"))
    source.completion_affinity.bind(lambda: "session-source")
    forked.completion_attribution.bind(
        attribution_for("session-forked", parent_session_id="session-source")
    )
    forked.completion_affinity.bind(lambda: "session-source")
    unrelated.completion_attribution.bind(attribution_for("session-unrelated"))
    unrelated.completion_affinity.bind(lambda: "session-unrelated")

    # Assert
    # Read back through the adapter config, which is the only path the Harness
    # runtime has to attribution -- a holder the config does not point at would
    # pass a direct assertion and still ship nothing.
    assert source.adapter_config.completion_metadata is not None
    assert forked.adapter_config.completion_metadata is not None
    assert (
        source.adapter_config.completion_metadata("agent", 0)["session_id"]
        == "session-source"
    )
    assert (
        forked.adapter_config.completion_metadata("agent", 0)["session_id"]
        == "session-forked"
    )
    assert (
        forked.adapter_config.completion_metadata("agent", 0)["parent_session_id"]
        == "session-source"
    )
    assert source.adapter_config.request_headers()["x-affinity"] == "session-source"
    assert forked.adapter_config.request_headers()["x-affinity"] == "session-source"
    assert (
        unrelated.adapter_config.request_headers()["x-affinity"] == "session-unrelated"
    )
    assert (
        unrelated.adapter_config.request_headers()["x-affinity"]
        != source.adapter_config.request_headers()["x-affinity"]
    )


@pytest.mark.asyncio
async def test_completion_attribution_carries_message_id(tmp_path: Path) -> None:
    """*Prepare*: An attribution source with a live message-id getter.
    *Do*: Call the attribution for an agent completion.
    *Assert*: The metadata dict carries the getter's current message id.
    """
    from vibe.app_server._completion_attribution import build_completion_attribution

    current_id: list[str | None] = ["msg-123"]
    attribution = build_completion_attribution(
        TelemetryClient(
            config_getter=build_test_vibe_config,
            session_id_getter=lambda: "session-root",
        ),
        None,
        message_id_getter=lambda: current_id[0],
    )
    metadata = attribution("agent", 0)
    assert metadata["message_id"] == "msg-123"

    # The getter is read live, so a later turn's id flows through.
    current_id[0] = "msg-456"
    metadata = attribution("agent", 0)
    assert metadata["message_id"] == "msg-456"


@pytest.mark.asyncio
async def test_correlate_last_request_uses_the_provider_correlation_id(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    # The SDK writes mistral-correlation-id into the context holder; a
    # correlate_last_request client event must carry it so the datalake can join
    # it to the exact provider request (parity with the legacy backend).
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _inert_adapter(_RecordingSession(), str(tmp_path), str(tmp_path))
    adapter._context.correlation.record("corr-abc")

    await adapter.dispatch_extension(
        "telemetry/record",
        {
            "sessionId": _RecordingSession.session_id,
            "name": "vibe.user_rating_feedback",
            "properties": {"rating": "up"},
            "correlateLastRequest": True,
        },
    )
    assert telemetry_events[-1]["correlation_id"] == "corr-abc"


@pytest.mark.asyncio
async def test_client_event_without_correlation_flag_omits_the_id(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _inert_adapter(_RecordingSession(), str(tmp_path), str(tmp_path))
    adapter._context.correlation.record("corr-abc")

    await adapter.dispatch_extension(
        "telemetry/record",
        {
            "sessionId": _RecordingSession.session_id,
            "name": "vibe.slash_command_used",
            "properties": {"command": "help"},
        },
    )
    assert "correlation_id" not in telemetry_events[-1]


@pytest.mark.asyncio
async def test_parent_session_id_is_stamped_on_events(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _inert_adapter(_RecordingSession(), str(tmp_path), str(tmp_path))
    adapter._parent_session_id = "parent-xyz"

    await adapter.dispatch_extension(
        "telemetry/record",
        {
            "sessionId": _RecordingSession.session_id,
            "name": "vibe.slash_command_used",
            "properties": {"command": "help"},
        },
    )
    assert telemetry_events[-1]["properties"]["parent_session_id"] == "parent-xyz"


@pytest.mark.asyncio
async def test_cache_session_lineage_uses_root_for_affinity(tmp_path: Path) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._completion_attribution import build_completion_attribution

    class _ParentSession(_RecordingSession):
        async def read(self, _params: Any) -> Any:
            return SimpleNamespace(
                snapshot=SimpleNamespace(
                    state=SimpleNamespace(
                        session=SimpleNamespace(
                            root_session_id="root-1", parent_session_id="parent-1"
                        )
                    )
                )
            )

    attribution = build_completion_attribution(
        TelemetryClient(
            config_getter=build_test_vibe_config,
            session_id_getter=lambda: _ParentSession.session_id,
            parent_session_id_getter=lambda: "parent-1",
        ),
        None,
    )
    adapter = _inert_adapter(
        _ParentSession(),
        str(tmp_path),
        str(tmp_path),
        completion_attribution=attribution,
    )
    assert adapter._adapter_config.request_headers()["x-affinity"] == (
        _ParentSession.session_id
    )
    await adapter._cache_session_lineage()

    assert adapter._parent_session_id == "parent-1"
    assert adapter._adapter_config.completion_metadata("agent", 0)["session_id"] == (
        _ParentSession.session_id
    )
    assert adapter._adapter_config.request_headers()["x-affinity"] == "root-1"


@pytest.mark.asyncio
async def test_unified_list_merges_legacy_and_unified_sessions(tmp_path: Path) -> None:
    """The unified adapter's ``list`` returns sessions from both stores,
    each tagged with its harness provenance.
    """
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    project_cwd = str((tmp_path / "project").resolve())
    (tmp_path / "project").mkdir()

    # Create a legacy session in the same cwd.
    legacy = build_test_agent_loop(config=config, cwd=Path(project_cwd))
    legacy.messages.append(LLMMessage(role=Role.user, content="legacy hello"))
    await legacy.session_logger.save_interaction(
        legacy.messages,
        legacy.stats,
        legacy.config,
        legacy.tool_manager,
        legacy.agent_profile,
    )
    legacy_id = legacy.session_id
    await legacy.aclose()

    # Create a unified session in the same cwd.
    host = _harness_backend_host(config)
    started = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    await started.backend.start_turn(
        TurnStartParams(
            session_id=started.backend.session_id,
            message=[TextContentBlock(text="unified hello")],
        )
    )
    unified_id = started.backend.session_id
    await host.shutdown()

    listed = await _harness_backend_host(config).list(
        SessionListParams(cwd=project_cwd)
    )

    harnesses = {item.id: item.harness for item in listed.items}
    assert harnesses.get(unified_id) == "unified"
    assert harnesses.get(legacy_id) == "legacy"
    assert len(listed.items) >= 2


@pytest.mark.asyncio
async def test_unified_archive_persists_to_metadata_and_survives_a_restart(
    tmp_path: Path,
) -> None:
    """*Prepare*: A persisted unified session archived in its metadata.
    *Do*: List and read it through a new Unified Harness Host adapter.
    *Assert*: Default listing hides it while archived reads expose its timestamp.
    """
    # Prepare
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    project_cwd = str((tmp_path / "project").resolve())
    (tmp_path / "project").mkdir()
    writer = _harness_backend_host(config)
    started = await writer.start(
        SessionStartParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    await started.backend.start_turn(
        TurnStartParams(
            session_id=started.backend.session_id,
            message=[TextContentBlock(text="persist unified session")],
        )
    )
    session_id = started.backend.session_id
    assert isinstance(writer, SessionBackendHostArchive)
    metadata = await writer.archive(
        SessionArchiveParams(session_id=session_id, archived=True)
    )
    await writer.shutdown()
    reader = _harness_backend_host(config)

    # Do
    hidden = await reader.list(SessionListParams(cwd=project_cwd))
    visible = await reader.list(
        SessionListParams(cwd=project_cwd, include_archived=True)
    )
    read = await reader.read(SessionReadParams(session_id=session_id))
    await reader.shutdown()

    # Assert
    assert session_id not in {session.id for session in hidden.items}
    archived = next(session for session in visible.items if session.id == session_id)
    assert archived.archived_at == metadata.archived_at
    assert read.state.session.archived_at == metadata.archived_at


@pytest.mark.asyncio
async def test_unified_archive_uses_the_session_project_store(tmp_path: Path) -> None:
    project_cwd = str((tmp_path / "project").resolve())
    Path(project_cwd).mkdir()
    default_store = tmp_path / "default-sessions"
    project_store = tmp_path / "project-sessions"
    default_config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(default_store))
    )
    project_config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(project_store))
    )
    writer = _project_scoped_harness_backend_host(
        default_config, project_config, project_cwd
    )
    started = await writer.start(
        SessionStartParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    session_id = started.backend.session_id
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id,
            message=[TextContentBlock(text="persist in the project store")],
        )
    )

    assert isinstance(writer, SessionBackendHostArchive)
    archived = await writer.archive(
        SessionArchiveParams(session_id=session_id, archived=True)
    )
    await writer.shutdown()

    reader = _project_scoped_harness_backend_host(
        default_config, project_config, project_cwd
    )
    listed = await reader.list(
        SessionListParams(cwd=project_cwd, include_archived=True)
    )
    assert isinstance(reader, SessionBackendHostArchive)
    unarchived = await reader.archive(
        SessionArchiveParams(session_id=session_id, archived=False)
    )
    visible = await reader.list(SessionListParams(cwd=project_cwd))
    await reader.shutdown()

    assert archived.archived_at is not None
    assert session_id in {session.id for session in listed.items}
    assert unarchived.archived_at is None
    assert session_id in {session.id for session in visible.items}
    assert not (default_store / "unified").exists()


@pytest.mark.asyncio
async def test_unified_archive_configures_storage_before_resolving_a_closed_session(
    tmp_path: Path,
) -> None:
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    real_host = vibe_runtime.create_harness_host()

    class RecordingHost:
        configured = False

        def __getattr__(self, name: str) -> Any:
            return getattr(real_host, name)

        def configure_storage(self, storage_root: str) -> None:
            self.configured = True
            real_host.configure_storage(storage_root)

        async def session_cwd(self, session_id: str) -> None:
            del session_id
            assert self.configured
            return None

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    host = adapt_harness_host(
        RecordingHost(),
        _test_session_runtime_builder(config),
        build_read_context=_read_context_builder(config, str(tmp_path)),
    )
    assert isinstance(host, SessionBackendHostArchive)

    with pytest.raises(SessionBackendError) as error:
        await host.archive(
            SessionArchiveParams(session_id="missing-session", archived=True)
        )

    assert error.value.code is ProtocolErrorCode.NOT_FOUND
    await host.shutdown()


@pytest.mark.asyncio
async def test_unified_closed_archive_reuses_the_live_project_store(
    tmp_path: Path,
) -> None:
    project_cwd = str((tmp_path / "project").resolve())
    Path(project_cwd).mkdir()
    default_store = tmp_path / "default-sessions"
    project_store = tmp_path / "project-sessions"
    default_config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(default_store))
    )
    project_config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(project_store))
    )

    writer = _project_scoped_harness_backend_host(
        default_config, project_config, project_cwd
    )
    closed = await writer.start(
        SessionStartParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    closed_id = closed.backend.session_id
    await closed.backend.start_turn(
        TurnStartParams(
            session_id=closed_id,
            message=[TextContentBlock(text="persist the closed session")],
        )
    )
    await writer.shutdown()

    host = _project_scoped_harness_backend_host(
        default_config, project_config, project_cwd
    )
    live = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    live_id = live.backend.session_id
    await live.backend.start_turn(
        TurnStartParams(
            session_id=live_id,
            message=[TextContentBlock(text="keep the project store bound")],
        )
    )
    assert isinstance(host, SessionBackendHostArchive)
    await host.archive(SessionArchiveParams(session_id=live_id, archived=True))
    closed_archive = await host.archive(
        SessionArchiveParams(session_id=closed_id, archived=True)
    )
    listed = await host.list(SessionListParams(cwd=project_cwd, include_archived=True))
    await host.shutdown()

    assert closed_archive.archived_at is not None
    assert {
        session.id for session in listed.items if session.archived_at is not None
    } >= {closed_id, live_id}
    assert not (default_store / "unified").exists()


@pytest.mark.asyncio
async def test_unified_continue_falls_back_when_pointer_is_archived(
    tmp_path: Path,
) -> None:
    """*Prepare*: Two unified sessions; archive the continue pointer.
    *Do*: Read the pinned list and continue through a new Unified Host adapter.
    *Assert*: Both select the remaining active session.
    """
    # Prepare
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    project_cwd = str((tmp_path / "project").resolve())
    (tmp_path / "project").mkdir()
    writer = _harness_backend_host(config)
    first = await writer.start(
        SessionStartParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    await first.backend.start_turn(
        TurnStartParams(
            session_id=first.backend.session_id,
            message=[TextContentBlock(text="older unified session")],
        )
    )
    first_id = first.backend.session_id
    second = await writer.start(
        SessionStartParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    await second.backend.start_turn(
        TurnStartParams(
            session_id=second.backend.session_id,
            message=[TextContentBlock(text="newer unified session")],
        )
    )
    second_id = second.backend.session_id
    assert isinstance(writer, SessionBackendHostPin)
    await writer.pin(SessionPinParams(session_id=first_id, pinned=True))
    await writer.pin(SessionPinParams(session_id=second_id, pinned=True))
    before = await writer.list(SessionListParams(cwd=project_cwd))
    pointer_id = before.continue_session_id
    assert pointer_id in {first_id, second_id}
    fallback_id = first_id if pointer_id == second_id else second_id
    assert isinstance(writer, SessionBackendHostArchive)
    await writer.archive(SessionArchiveParams(session_id=pointer_id, archived=True))
    pinned = await writer.list(SessionListParams(cwd=project_cwd, pinned=True))
    await writer.shutdown()

    # Do
    reader = _harness_backend_host(config)
    continued = await reader.continue_latest(
        SessionContinueParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    await reader.shutdown()

    # Assert
    assert pinned.continue_session_id == fallback_id
    assert {session.id for session in pinned.items} == {fallback_id}
    assert continued.backend.session_id == fallback_id


@pytest.mark.asyncio
async def test_unified_list_continue_session_id_is_unified_only(tmp_path: Path) -> None:
    """``continue_session_id`` is resolved from the unified store alone,
    not from a legacy session that may be more recent.
    """
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    project_cwd = str((tmp_path / "project").resolve())
    (tmp_path / "project").mkdir()

    # Create a legacy session (newer) in the same cwd.
    legacy = build_test_agent_loop(config=config, cwd=Path(project_cwd))
    legacy.messages.append(LLMMessage(role=Role.user, content="legacy newer"))
    await legacy.session_logger.save_interaction(
        legacy.messages,
        legacy.stats,
        legacy.config,
        legacy.tool_manager,
        legacy.agent_profile,
    )
    legacy_id = legacy.session_id
    await legacy.aclose()

    # Create a unified session (older) in the same cwd.
    host = _harness_backend_host(config)
    started = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    await started.backend.start_turn(
        TurnStartParams(
            session_id=started.backend.session_id,
            message=[TextContentBlock(text="unified hello")],
        )
    )
    unified_id = started.backend.session_id
    await host.shutdown()

    listed = await _harness_backend_host(config).list(
        SessionListParams(cwd=project_cwd)
    )

    # continue_session_id must be the unified session, not the legacy one.
    assert listed.continue_session_id is not None
    assert listed.continue_session_id != legacy_id
    assert listed.continue_session_id == unified_id or (
        unified_id in {item.id for item in listed.items if item.harness == "unified"}
    )


@pytest.mark.asyncio
async def test_unified_list_degrades_to_unified_when_legacy_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the legacy listing raises ``OSError``, the adapter returns
    unified-only items without propagating the error.
    """
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    project_cwd = str((tmp_path / "project").resolve())
    (tmp_path / "project").mkdir()

    # Create a unified session.
    host = _harness_backend_host(config)
    started = await host.start(
        SessionStartParams(agent_config=SessionOptions(cwd=project_cwd))
    )
    await started.backend.start_turn(
        TurnStartParams(
            session_id=started.backend.session_id,
            message=[TextContentBlock(text="unified hello")],
        )
    )
    unified_id = started.backend.session_id
    await host.shutdown()

    # Force the legacy listing to fail.
    from vibe.app_server import _legacy_import as legacy_import_module

    def _fail_listing(_config: Any, _cwd: Any) -> Any:
        raise OSError("disk gone")

    monkeypatch.setattr(
        legacy_import_module, "list_local_resume_sessions", _fail_listing
    )

    listed = await _harness_backend_host(config).list(
        SessionListParams(cwd=project_cwd)
    )

    assert all(item.harness == "unified" for item in listed.items)
    assert unified_id in {item.id for item in listed.items}


@pytest.mark.asyncio
async def test_unified_list_folder_scoping_excludes_non_matching_legacy(
    tmp_path: Path,
) -> None:
    """Legacy sessions whose cwd does not match the request cwd are excluded."""
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    project_cwd = str((tmp_path / "project").resolve())
    other_cwd = str((tmp_path / "other").resolve())
    (tmp_path / "project").mkdir(parents=True, exist_ok=True)
    (tmp_path / "other").mkdir(parents=True, exist_ok=True)

    # Legacy session in a different cwd.
    legacy = build_test_agent_loop(config=config, cwd=Path(other_cwd))
    legacy.messages.append(LLMMessage(role=Role.user, content="other cwd"))
    await legacy.session_logger.save_interaction(
        legacy.messages,
        legacy.stats,
        legacy.config,
        legacy.tool_manager,
        legacy.agent_profile,
    )
    other_legacy_id = legacy.session_id
    await legacy.aclose()

    listed = await _harness_backend_host(config).list(
        SessionListParams(cwd=project_cwd)
    )

    assert other_legacy_id not in {item.id for item in listed.items}


@pytest.mark.asyncio
async def test_unified_list_previews_only_untitled_legacy_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A titled legacy row costs no transcript read: every consumer renders
    ``title or preview``, so the read would be thrown away.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server import (
        _legacy_import as legacy_import_module,
        _unified_harness_backend_adapter as adapter_module,
    )
    from vibe.core.session.resume_sessions import ResumeSessionInfo

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    project_cwd = str((tmp_path / "project").resolve())
    (tmp_path / "project").mkdir()

    monkeypatch.setattr(
        legacy_import_module,
        "list_local_resume_sessions",
        lambda _config, _cwd: [
            ResumeSessionInfo(
                session_id="titled",
                cwd=project_cwd,
                title="A saved title",
                updated_at="2026-01-01T00:00:00",
            ),
            ResumeSessionInfo(
                session_id="untitled", cwd=project_cwd, updated_at="2026-01-01T00:00:01"
            ),
        ],
    )
    previewed: list[str] = []

    def _preview(session_id: str, _logging: Any) -> str:
        previewed.append(session_id)
        return "the first user message"

    monkeypatch.setattr(
        adapter_module.SessionLoader, "get_first_user_message", _preview
    )

    listed = await _harness_backend_host(config).list(
        SessionListParams(cwd=project_cwd)
    )

    assert previewed == ["untitled"]
    rows = {item.id: item for item in listed.items}
    assert rows["titled"].title == "A saved title"
    assert rows["titled"].preview == ""
    assert rows["untitled"].preview == "the first user message"


@pytest.mark.asyncio
async def test_unified_list_cursor_walk_reads_one_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paging holds the merge the first page paid for. Rebuilding per page
    would sweep both stores again, and re-sorting live data mid-walk would
    drop any session whose ``updated_at`` moved it back behind the cursor.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server import _legacy_import as legacy_import_module
    from vibe.core.session.resume_sessions import ResumeSessionInfo

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    project_cwd = str((tmp_path / "project").resolve())
    (tmp_path / "project").mkdir()

    stored = [
        ResumeSessionInfo(
            session_id=f"legacy-{index}",
            cwd=project_cwd,
            title=f"title {index}",
            updated_at=f"2026-01-01T00:00:{index:02d}",
        )
        for index in range(5)
    ]
    builds = 0

    def _listing(_config: Any, _cwd: Any) -> list[ResumeSessionInfo]:
        nonlocal builds
        builds += 1
        return list(stored)

    monkeypatch.setattr(legacy_import_module, "list_local_resume_sessions", _listing)

    host = _harness_backend_host(config)
    walked: list[str] = []
    cursor: str | None = None
    while True:
        page = await host.list(
            SessionListParams(cwd=project_cwd, limit=2, cursor=cursor)
        )
        walked.extend(item.id for item in page.items)
        # The oldest session becomes the newest between pages. Against a live
        # re-sort it would jump ahead of the cursor and never be returned.
        stored[0] = replace(stored[0], updated_at="2026-01-01T00:01:00")
        if page.next_cursor is None:
            break
        cursor = page.next_cursor

    assert builds == 1
    assert sorted(walked) == [f"legacy-{index}" for index in range(5)]


@pytest.mark.asyncio
async def test_unified_read_returns_legacy_session_history_for_preview(
    tmp_path: Path,
) -> None:
    """Highlighting a legacy session in the picker calls ``session/read``,
    which the unified host does not know about. The adapter falls back to
    reading the legacy JSONL transcript and returns ``PublicHistoryEntry``
    objects so the conversation preview renders.
    """
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    project_cwd = str((tmp_path / "project").resolve())
    (tmp_path / "project").mkdir()

    # Create a legacy session with user + assistant messages.
    legacy = build_test_agent_loop(config=config, cwd=Path(project_cwd))
    legacy.messages.extend([
        LLMMessage(role=Role.user, content="legacy preview hello"),
        LLMMessage(role=Role.assistant, content="legacy preview world"),
    ])
    await legacy.session_logger.save_interaction(
        legacy.messages,
        legacy.stats,
        legacy.config,
        legacy.tool_manager,
        legacy.agent_profile,
    )
    legacy_id = legacy.session_id
    await legacy.aclose()

    host = _harness_backend_host(config)
    read = await host.read(
        SessionReadParams(session_id=legacy_id, history=PageRequest(limit=200))
    )
    await host.shutdown()

    assert read.state.session.id == legacy_id
    assert read.state.session.harness == "legacy"
    assert read.state.history is not None
    roles = [entry.model_dump().get("role") for entry in read.state.history]
    assert "user" in roles
    assert "assistant" in roles
    assert read.state.history_before_cursor is None


@pytest.mark.asyncio
async def test_unified_read_reports_which_process_holds_a_legacy_session(
    tmp_path: Path,
) -> None:
    from mistralai_vibe_local_harness.vibe._storage import SessionLease as HarnessLease

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    (tmp_path / "project").mkdir()
    legacy = build_test_agent_loop(config=config, cwd=(tmp_path / "project").resolve())
    legacy.messages.extend([
        LLMMessage(role=Role.user, content="held legacy hello"),
        LLMMessage(role=Role.assistant, content="held legacy world"),
    ])
    await legacy.session_logger.save_interaction(
        legacy.messages,
        legacy.stats,
        legacy.config,
        legacy.tool_manager,
        legacy.agent_profile,
    )
    legacy_id = legacy.session_id
    await legacy.aclose()

    host = _harness_backend_host(config)
    try:
        free = await host.read(SessionReadParams(session_id=legacy_id))
        with HarnessLease(tmp_path, legacy_id):
            held = await host.read(SessionReadParams(session_id=legacy_id))
    finally:
        await host.shutdown()

    assert held.state.session.harness == "legacy"
    assert free.lease_holder is None
    assert held.lease_holder == SessionLeaseHolder(process_id=os.getpid())


@pytest.mark.asyncio
async def test_unified_legacy_preview_reports_the_older_history_it_trims(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    (tmp_path / "project").mkdir()
    legacy = build_test_agent_loop(config=config, cwd=tmp_path / "project")
    legacy.messages.extend([
        LLMMessage(role=Role.user, content="first question"),
        LLMMessage(role=Role.assistant, content="first answer"),
    ])
    await legacy.session_logger.save_interaction(
        legacy.messages,
        legacy.stats,
        legacy.config,
        legacy.tool_manager,
        legacy.agent_profile,
    )
    legacy_id = legacy.session_id
    await legacy.aclose()

    host = _harness_backend_host(config)
    read = await host.read(
        SessionReadParams(session_id=legacy_id, history=PageRequest(limit=1))
    )
    await host.shutdown()

    assert read.state.history is not None
    assert len(read.state.history) == 1
    assert read.state.history_before_cursor == read.state.history[0].id


@pytest.mark.asyncio
async def test_unified_read_raises_not_found_for_unknown_session(
    tmp_path: Path,
) -> None:
    """When neither the unified store nor the legacy store has the session,
    ``read`` raises ``NOT_FOUND``.
    """
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    host = _harness_backend_host(config)

    with pytest.raises(SessionBackendError) as exc_info:
        await host.read(
            SessionReadParams(
                session_id="nonexistent-session-id", history=PageRequest(limit=200)
            )
        )
    await host.shutdown()

    assert exc_info.value.code is ProtocolErrorCode.NOT_FOUND


@pytest.mark.asyncio
async def test_a_turn_deferred_behind_a_cancelled_setup_is_refused(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    from vibe.app_server._session_backend_port import SessionBackendError

    adapter = _inert_adapter(
        _session_stub(session_id="session-1", cwd=str(tmp_path)),
        str(tmp_path),
        str(tmp_path),
    )
    started = asyncio.Event()

    async def never_finishes() -> None:
        started.set()
        await asyncio.Event().wait()

    adapter.defer_turns(never_finishes())
    await started.wait()
    adapter._deferred_setup_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await adapter._deferred_setup_task

    with pytest.raises(SessionBackendError):
        await adapter._await_deferred_setup()


@pytest.mark.asyncio
async def test_first_turn_is_accepted_before_deferred_setup_finishes(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    session = _RecordingSession()
    adapter = _inert_adapter(
        session, str(tmp_path), str(tmp_path), deferred_turns=session
    )
    setup_started = asyncio.Event()

    async def never_finishes() -> WorktreeResolution:
        setup_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    adapter.defer_turns(never_finishes())
    await setup_started.wait()

    result = await asyncio.wait_for(
        adapter.start_turn(
            TurnStartParams(
                session_id=session.session_id,
                message=[TextContentBlock(text="hello")],
                client_user_message_id="message-1",
            )
        ),
        timeout=1,
    )

    assert result.response.turn.id == "turn-1"
    assert hasattr(session, "deferred_turn_params")
    assert not adapter._deferred_setup_task.done()

    adapter._deferred_setup_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await adapter._deferred_setup_task


@pytest.mark.asyncio
async def test_deferred_turn_sets_client_message_id_before_reservation(
    tmp_path: Path,
) -> None:
    """*Prepare*: A deferred session whose reservation observes attribution state.
    *Do*: Start the first turn with a client user-message identity.
    *Assert*: Attribution contains that identity before the Harness reserves work.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    # Prepare
    observed_message_ids: list[str | None] = []

    class _AttributionAwareSession(_RecordingSession):
        async def start_deferred_turn(
            self, params: HarnessDeferredTurnStartParams
        ) -> HarnessDeferredTurnStartResult:
            observed_message_ids.append(adapter._message_id_holder[0])
            return await super().start_deferred_turn(params)

    session = _AttributionAwareSession()
    adapter = _inert_adapter(
        session, str(tmp_path), str(tmp_path), deferred_turns=session
    )

    async def setup() -> WorktreeResolution:
        return WorktreeResolution(options=SessionOptions(cwd=str(tmp_path)))

    adapter.defer_turns(setup())
    params = TurnStartParams(
        session_id=session.session_id,
        message=[TextContentBlock(text="hello")],
        client_user_message_id="message-1",
    )

    # Do
    result = await adapter.start_turn(params)

    # Assert
    assert observed_message_ids == ["message-1"]
    assert result.on_response_abandoned is not None
    result.on_response_abandoned()


@pytest.mark.asyncio
async def test_deferred_worktree_retry_uses_a_distinct_effect_id(
    tmp_path: Path,
) -> None:
    """*Prepare*: A deferred worktree turn whose first response is abandoned.
    *Do*: Retry the first turn while reusing the same session and request values.
    *Assert*: The retry has a new effect ID and keeps it for terminal replacement.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    # Prepare
    session = _RecordingSession()
    adapter = _inert_adapter(
        session, str(tmp_path), str(tmp_path), deferred_turns=session
    )

    async def setup() -> WorktreeResolution:
        return WorktreeResolution(options=SessionOptions(cwd=str(tmp_path)))

    adapter.defer_turns(setup(), worktree_progress=_worktree_progress("feature"))
    params = TurnStartParams(
        session_id=session.session_id,
        message=[TextContentBlock(text="hello")],
        client_user_message_id="message-1",
    )
    first = await adapter.start_turn(params)
    first_preparation = session.deferred_turn_params.prepare
    first_entry_id = first_preparation.pending_history_entries[0].id
    assert first.on_response_abandoned is not None
    first.on_response_abandoned()

    # Do
    adapter._settle_reserved_turn_configuration = AsyncMock(
        side_effect=RuntimeError("setup stopped")
    )
    second = await adapter.start_turn(params)
    second_preparation = session.deferred_turn_params.prepare
    second_entry_id = second_preparation.pending_history_entries[0].id
    with pytest.raises(HarnessDeferredTurnPreparationError) as exc_info:
        await second_preparation.run(_preparation_context(session.session_id))
    failed_entry_id = exc_info.value.history_entries[0].id

    # Assert
    assert second_entry_id != first_entry_id
    assert failed_entry_id == second_entry_id
    assert second.on_response_abandoned is not None
    second.on_response_abandoned()


@pytest.mark.asyncio
async def test_deferred_turn_persists_activity_metadata_after_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    class _EphemeralSession(_RecordingSession):
        ephemeral = True

    session = _EphemeralSession()
    services = Mock()
    services.notify = AsyncMock()
    adapter = _inert_adapter(
        session, str(tmp_path), str(tmp_path), services=services, deferred_turns=session
    )
    runtime_update = cast(Any, object())
    monkeypatch.setattr(
        adapter, "runtime_updated_params", Mock(return_value=runtime_update)
    )
    params = TurnStartParams(
        session_id=session.session_id,
        message=[TextContentBlock(text="hello")],
        client_user_message_id="message-1",
    )

    async def setup() -> WorktreeResolution:
        return WorktreeResolution(options=SessionOptions(cwd=str(tmp_path)))

    monkeypatch.setattr(adapter, "_settle_reserved_turn_configuration", AsyncMock())
    pin_model_choice = AsyncMock(return_value=True)
    monkeypatch.setattr(adapter, "_pin_session_model_choice", pin_model_choice)
    monkeypatch.setattr(
        adapter, "_prepared_turn_params", AsyncMock(return_value=params)
    )
    persist_scheduled_loops = AsyncMock()
    monkeypatch.setattr(
        adapter, "_persist_scheduled_loops_after_promotion", persist_scheduled_loops
    )
    adapter.defer_turns(setup())

    result = await adapter.start_turn(params)

    assert adapter._metadata is not None
    accepted_bumped_at = adapter._metadata.bumped_at
    meta_path = tmp_path / "unified" / session.session_id / "meta.json"
    assert not meta_path.exists()
    services.notify.assert_not_awaited()
    assert result.after_response is not None
    result.after_response()

    preparation = session.deferred_turn_params.prepare
    prepared = await preparation.run(_preparation_context(session.session_id))
    services.notify.assert_awaited_once_with("runtime/updated", runtime_update)
    session.ephemeral = False
    assert prepared.after_promotion is not None
    await prepared.after_promotion()

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    assert metadata["bumped_at"] == accepted_bumped_at
    pin_model_choice.assert_awaited_once_with()
    persist_scheduled_loops.assert_awaited_once_with(was_ephemeral=True)


@pytest.mark.asyncio
async def test_abandoned_deferred_turn_releases_claim_for_retry(tmp_path: Path) -> None:
    """A dropped deferred response releases both adapter and Harness reservations."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    class _AbandonableSession(_RecordingSession):
        def __init__(self) -> None:
            super().__init__()
            self.abandoned = False
            self.deferred_starts = 0

        async def start_deferred_turn(
            self, params: HarnessDeferredTurnStartParams
        ) -> HarnessDeferredTurnStartResult:
            self.deferred_starts += 1
            result = await super().start_deferred_turn(params)

            def abandon() -> None:
                self.abandoned = True

            return HarnessDeferredTurnStartResult(
                turn_id=result.turn_id,
                session_id=result.session_id,
                started_at=result.started_at,
                last_event_id=result.last_event_id,
                after_response=result.after_response,
                on_response_abandoned=abandon,
            )

        async def start_turn(self, params: Any) -> Any:
            raise AssertionError("retry must use the deferred path")

    session = _AbandonableSession()
    adapter = _inert_adapter(
        session, str(tmp_path), str(tmp_path), deferred_turns=session
    )
    setup_started = asyncio.Event()

    async def never_finishes() -> WorktreeResolution:
        setup_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    adapter.defer_turns(never_finishes())
    await setup_started.wait()
    params = TurnStartParams(
        session_id=session.session_id,
        message=[TextContentBlock(text="hello")],
        client_user_message_id="message-1",
    )

    first = await adapter.start_turn(params)
    assert first.on_response_abandoned is not None
    first.on_response_abandoned()
    second = await adapter.start_turn(params)

    assert session.abandoned
    assert session.deferred_starts == 2
    assert second.on_response_abandoned is not None
    assert adapter._deferred_turn_claimed
    adapter._deferred_setup_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await adapter._deferred_setup_task


@pytest.mark.parametrize(
    "response_callback",
    ["after_response", "on_response_abandoned"],
    ids=["delivered", "abandoned"],
)
@pytest.mark.asyncio
async def test_interrupting_deferred_setup_releases_claim_for_retry(
    tmp_path: Path, response_callback: str
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    class _InterruptibleSession(_RecordingSession):
        ephemeral = True

        def __init__(self) -> None:
            super().__init__()
            self.deferred_starts = 0
            self.interrupted = False

        async def start_deferred_turn(
            self, params: HarnessDeferredTurnStartParams
        ) -> HarnessDeferredTurnStartResult:
            self.deferred_starts += 1
            result = await super().start_deferred_turn(params)

            def after_response() -> None:
                self.reserved_turn_id = result.turn_id

            return HarnessDeferredTurnStartResult(
                turn_id=result.turn_id,
                session_id=result.session_id,
                started_at=result.started_at,
                last_event_id=result.last_event_id,
                after_response=after_response,
                on_response_abandoned=result.on_response_abandoned,
            )

        async def interrupt_turn(self, params: Any) -> Any:
            assert self.reserved_turn_id == params.expected_turn_id
            self.reserved_turn_id = None
            response_settled = False

            def settle_response() -> None:
                nonlocal response_settled
                if response_settled:
                    return
                response_settled = True
                self.interrupted = True

            return SimpleNamespace(
                response={"accepted": True, "last_event_id": 0},
                after_response=settle_response,
                on_response_abandoned=settle_response,
            )

    session = _InterruptibleSession()
    adapter = _inert_adapter(
        session, str(tmp_path), str(tmp_path), deferred_turns=session
    )
    setup_started = asyncio.Event()

    async def never_finishes() -> WorktreeResolution:
        setup_started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    adapter.defer_turns(never_finishes())
    await setup_started.wait()
    params = TurnStartParams(
        session_id=session.session_id,
        message=[TextContentBlock(text="hello")],
        client_user_message_id="message-1",
    )

    first = await adapter.start_turn(params)
    assert first.after_response is not None
    first.after_response()
    interrupted = await adapter.interrupt_turn(
        TurnInterruptParams(
            session_id=session.session_id, expected_turn_id=first.response.turn.id
        )
    )

    assert adapter._deferred_turn_claimed
    callback = getattr(interrupted, response_callback)
    assert callback is not None
    callback()
    assert session.interrupted
    assert not adapter._deferred_turn_claimed

    second = await adapter.start_turn(params)
    assert session.deferred_starts == 2
    assert second.on_response_abandoned is not None
    adapter._deferred_setup_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await adapter._deferred_setup_task


@pytest.mark.asyncio
async def test_later_turn_preparation_failure_keeps_created_worktree_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure after workspace setup must not report worktree creation as failed."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    session = _RecordingSession()
    adapter = _inert_adapter(
        session, str(tmp_path), str(tmp_path), deferred_turns=session
    )
    worktree_root = tmp_path / "created-worktree"
    prepared = PreparedWorktree(
        name="created-worktree",
        branch="vibe/created-worktree",
        root=worktree_root,
        path=worktree_root,
        repo_root=tmp_path,
        base_commit="base",
        created=True,
        branch_created=True,
    )

    async def setup() -> WorktreeResolution:
        return WorktreeResolution(
            options=SessionOptions(cwd=str(worktree_root)), prepared_worktree=prepared
        )

    later_error = RuntimeError("prompt preparation failed")

    async def fail_after_worktree() -> None:
        raise later_error

    monkeypatch.setattr(
        adapter, "_settle_reserved_turn_configuration", fail_after_worktree
    )
    adapter.defer_turns(setup(), worktree_progress=_worktree_progress(prepared.name))
    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id,
            message=[TextContentBlock(text="hello")],
            client_user_message_id="message-1",
        )
    )
    preparation = session.deferred_turn_params.prepare

    with pytest.raises(HarnessDeferredTurnPreparationError) as exc_info:
        await preparation.run(_preparation_context(session.session_id))

    entry = PublicEffectEntry.model_validate(
        exc_info.value.history_entries[0], from_attributes=True
    )
    assert isinstance(entry.state, CompletedEffectState)
    assert entry.state.display.message == "created-worktree on vibe/created-worktree"


async def _start_deferred_worktree_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[_RecordingSession, WorktreeProgressFeed, asyncio.Event]:
    """A deferred worktree turn whose setup finishes once the event is set."""
    session = _RecordingSession()
    adapter = _inert_adapter(
        session, str(tmp_path), str(tmp_path), deferred_turns=session
    )
    worktree_root = tmp_path / "feature"
    prepared = PreparedWorktree(
        name="feature",
        branch="vibe/feature",
        root=worktree_root,
        path=worktree_root,
        repo_root=tmp_path,
        base_commit="base",
        created=True,
        branch_created=True,
    )
    checkout_done = asyncio.Event()

    async def setup() -> WorktreeResolution:
        await checkout_done.wait()
        return WorktreeResolution(
            options=SessionOptions(cwd=str(worktree_root)), prepared_worktree=prepared
        )

    feed = WorktreeProgressFeed(asyncio.get_running_loop())
    adapter.defer_turns(
        setup(), worktree_progress=WorktreeProgress(name="feature", feed=feed)
    )
    params = TurnStartParams(
        session_id=session.session_id,
        message=[TextContentBlock(text="hello")],
        client_user_message_id="message-1",
    )
    monkeypatch.setattr(adapter, "_settle_reserved_turn_configuration", AsyncMock())
    monkeypatch.setattr(
        adapter, "_pin_session_model_choice", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        adapter, "_prepared_turn_params", AsyncMock(return_value=params)
    )
    await adapter.start_turn(params)
    return session, feed, checkout_done


@pytest.mark.asyncio
async def test_deferred_worktree_publishes_progress_until_the_worktree_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A deferred worktree turn whose setup waits on a checkout.
    *Do*: Report checkout progress from another thread, then finish the setup.
    *Assert*: The pending entry is replaced with the progress, and nothing is
        replaced once the setup is done.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.git.worktree import WorktreeCreationPhase, WorktreeCreationProgress

    # Prepare
    session, feed, checkout_done = await _start_deferred_worktree_turn(
        tmp_path, monkeypatch
    )
    replaced: list[tuple[Any, ...]] = []
    published = asyncio.Event()

    async def replace(entries: tuple[Any, ...]) -> None:
        replaced.append(entries)
        published.set()

    preparation = asyncio.ensure_future(
        session.deferred_turn_params.prepare.run(
            _preparation_context(session.session_id, replace)
        )
    )

    # Do
    await asyncio.to_thread(
        feed.report,
        WorktreeCreationProgress(
            WorktreeCreationPhase.CHECKING_OUT, completed_files=5, total_files=10
        ),
    )
    await asyncio.wait_for(published.wait(), timeout=1)
    checkout_done.set()
    result = await asyncio.wait_for(preparation, timeout=1)
    published_count = len(replaced)
    feed.report(WorktreeCreationProgress(WorktreeCreationPhase.CHECKING_OUT))
    await asyncio.sleep(0.05)

    # Assert
    entry = PublicEffectEntry.model_validate(replaced[0][0], from_attributes=True)
    assert isinstance(entry.detail, WorktreeEffectDetail)
    assert entry.detail.progress is not None
    assert entry.detail.progress.model_dump(by_alias=True) == {
        "phase": "checking_out",
        "completedFiles": 5,
        "totalFiles": 10,
    }
    assert isinstance(entry.state, RunningEffectState)
    assert len(replaced) == published_count
    settled = PublicEffectEntry.model_validate(
        result.history_entries[0], from_attributes=True
    )
    assert isinstance(settled.state, CompletedEffectState)


@pytest.mark.asyncio
async def test_a_failed_progress_update_leaves_the_worktree_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A deferred worktree turn whose progress cannot be published.
    *Do*: Report checkout progress, then finish the setup.
    *Assert*: The turn is still prepared, with the worktree completed.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.git.worktree import WorktreeCreationPhase, WorktreeCreationProgress

    # Prepare
    session, feed, checkout_done = await _start_deferred_worktree_turn(
        tmp_path, monkeypatch
    )
    attempted = asyncio.Event()

    async def replace(_entries: tuple[Any, ...]) -> None:
        attempted.set()
        raise RuntimeError("history closed")

    preparation = asyncio.ensure_future(
        session.deferred_turn_params.prepare.run(
            _preparation_context(session.session_id, replace)
        )
    )

    # Do
    feed.report(WorktreeCreationProgress(WorktreeCreationPhase.CHECKING_OUT))
    await asyncio.wait_for(attempted.wait(), timeout=1)
    checkout_done.set()
    result = await asyncio.wait_for(preparation, timeout=1)

    # Assert
    settled = PublicEffectEntry.model_validate(
        result.history_entries[0], from_attributes=True
    )
    assert isinstance(settled.state, CompletedEffectState)


class _RecordingServices:
    """Stands in for the adapter's notification services."""

    def __init__(self) -> None:
        self.notifications: list[tuple[str, Any]] = []

    async def notify(self, method: str, params: Any) -> None:
        self.notifications.append((method, params))


@pytest.mark.asyncio
async def test_deferred_worktree_failure_is_pushed_as_a_history_entry(
    tmp_path: Path,
) -> None:
    """A client gating its UI on the worktree has no turn to carry the failure."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.git.worktree import WorktreeError

    session = _RecordingSession()
    services = _RecordingServices()
    adapter = _inert_adapter(
        session, str(tmp_path), str(tmp_path), deferred_turns=session, services=services
    )

    async def setup() -> WorktreeResolution:
        raise WorktreeError("Path already exists but is not a git worktree.")

    adapter.defer_turns(setup(), worktree_progress=_worktree_progress("unwritable"))
    await adapter._deferred_setup_task

    assert len(services.notifications) == 1
    method, params = services.notifications[0]
    assert method == "history/entryAdded"
    entry = PublicEffectEntry.model_validate(params.entry, from_attributes=True)
    assert entry.detail.tool_name == "worktree"
    assert isinstance(entry.state, FailedEffectState)
    # The gate prints the underlying error, like Python's `Error: {e}`.
    assert entry.state.error is not None
    assert entry.state.error.message == "Path already exists but is not a git worktree."


@pytest.mark.asyncio
async def test_a_turn_after_a_failed_worktree_does_not_publish_it_again(
    tmp_path: Path,
) -> None:
    """The pushed failure is the only entry; a later turn must not add another."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.git.worktree import WorktreeError

    session = _RecordingSession()
    services = _RecordingServices()
    adapter = _inert_adapter(
        session, str(tmp_path), str(tmp_path), deferred_turns=session, services=services
    )

    async def setup() -> WorktreeResolution:
        raise WorktreeError("Path already exists but is not a git worktree.")

    adapter.defer_turns(setup(), worktree_progress=_worktree_progress("unwritable"))
    await adapter._deferred_setup_task

    with pytest.raises(SessionBackendError) as exc_info:
        await adapter.start_turn(
            TurnStartParams(
                session_id=session.session_id,
                message=[TextContentBlock(text="hello")],
                client_user_message_id="message-1",
            )
        )

    assert exc_info.value.code is ProtocolErrorCode.CONFLICT
    assert not hasattr(session, "deferred_turn_params")
    assert len(services.notifications) == 1
    method, _params = services.notifications[0]
    assert method == "history/entryAdded"


@pytest.mark.asyncio
async def test_turn_start_during_worktree_failure_emit_publishes_once(
    tmp_path: Path,
) -> None:
    """A turn/start that arrives while the failure notify awaits must not republish."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    notify_started = asyncio.Event()
    release_notify = asyncio.Event()

    class _PausingServices(_RecordingServices):
        async def notify(self, method: str, params: Any) -> None:
            notify_started.set()
            await release_notify.wait()
            await super().notify(method, params)

    session = _RecordingSession()
    services = _PausingServices()
    adapter = _inert_adapter(
        session, str(tmp_path), str(tmp_path), deferred_turns=session, services=services
    )

    async def setup() -> WorktreeResolution:
        raise RuntimeError("worktree preparation failed")

    adapter.defer_turns(setup(), worktree_progress=_worktree_progress("feature"))
    await notify_started.wait()
    starting = asyncio.create_task(
        adapter.start_turn(
            TurnStartParams(
                session_id=session.session_id,
                message=[TextContentBlock(text="hello")],
                client_user_message_id="message-1",
            )
        )
    )
    await asyncio.sleep(0)
    release_notify.set()

    with pytest.raises(SessionBackendError) as exc_info:
        await starting
    await adapter._deferred_setup_task

    assert exc_info.value.code is ProtocolErrorCode.CONFLICT
    assert not hasattr(session, "deferred_turn_params")
    assert len(services.notifications) == 1
    method, _params = services.notifications[0]
    assert method == "history/entryAdded"


@pytest.mark.asyncio
async def test_a_deferred_failure_without_worktree_progress_pushes_nothing(
    tmp_path: Path,
) -> None:
    """Only a worktree run carries the pushed failure; other deferrals stay put."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    session = _RecordingSession()
    services = _RecordingServices()
    adapter = _inert_adapter(
        session, str(tmp_path), str(tmp_path), deferred_turns=session, services=services
    )

    async def setup() -> WorktreeResolution:
        raise RuntimeError("boom")

    adapter.defer_turns(setup())
    await adapter._deferred_setup_task

    assert services.notifications == []


@pytest.mark.asyncio
async def test_reused_worktree_is_not_reported_as_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A named worktree already linked elsewhere is reported as reused."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    session = _RecordingSession()
    adapter = _inert_adapter(
        session, str(tmp_path), str(tmp_path), deferred_turns=session
    )
    worktree_root = tmp_path / "existing-worktree"
    prepared = PreparedWorktree(
        name="existing-worktree",
        branch="vibe/existing-worktree",
        root=worktree_root,
        path=worktree_root,
        repo_root=tmp_path,
        base_commit="base",
        created=False,
        branch_created=False,
    )

    async def setup() -> WorktreeResolution:
        return WorktreeResolution(
            options=SessionOptions(cwd=str(worktree_root)), prepared_worktree=prepared
        )

    params = TurnStartParams(
        session_id=session.session_id,
        message=[TextContentBlock(text="hello")],
        client_user_message_id="message-1",
    )
    monkeypatch.setattr(adapter, "_settle_reserved_turn_configuration", AsyncMock())
    monkeypatch.setattr(
        adapter, "_pin_session_model_choice", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        adapter, "_prepared_turn_params", AsyncMock(return_value=params)
    )
    adapter.defer_turns(setup(), worktree_progress=_worktree_progress(prepared.name))
    await adapter.start_turn(params)
    prepared_result = await session.deferred_turn_params.prepare.run(
        _preparation_context(session.session_id)
    )

    entry = PublicEffectEntry.model_validate(
        prepared_result.history_entries[0], from_attributes=True
    )
    assert isinstance(entry.state, CompletedEffectState)
    assert entry.detail.display.settled_verb == "Reused"
    assert entry.state.display.verb == "Reused"
    assert entry.state.display.message == "existing-worktree on vibe/existing-worktree"


@pytest.mark.asyncio
async def test_missing_deferred_turn_capability_waits_for_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A session without deferred-turn APIs and blocked setup.
    *Do*: Apply the moved context and start the first turn.
    *Assert*: Unified configuration lands and the turn waits for setup.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    # Prepare
    session = _RecordingSession()
    adapter, _ = await _unified_adapter_with_real_context(tmp_path, session)
    await adapter.adopt_context(adapter._context)
    setup_started = asyncio.Event()
    release_setup = asyncio.Event()

    async def setup() -> WorktreeResolution:
        setup_started.set()
        await release_setup.wait()
        return WorktreeResolution(options=SessionOptions(cwd=str(tmp_path)))

    adapter.defer_turns(setup())
    await setup_started.wait()

    # Do
    starting = asyncio.create_task(
        adapter.start_turn(
            TurnStartParams(
                session_id=session.session_id,
                message=[TextContentBlock(text="hello")],
                client_user_message_id="message-1",
            )
        )
    )
    await asyncio.sleep(0)

    # Assert
    assert session.core_configs
    assert not starting.done()
    release_setup.set()
    result = await asyncio.wait_for(starting, timeout=1)
    assert result.response.turn.id == "turn-1"
    assert not hasattr(session, "deferred_turn_params")


@pytest.mark.asyncio
async def test_worktree_move_does_not_announce_a_parked_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A running session whose worktree configuration push must park.
    *Do*: Attempt to move the session to the prepared worktree.
    *Assert*: The move rolls back and no CWD update is announced.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendHostAdapter,
    )

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    # Prepare
    original_cwd = tmp_path / "original"
    moved_cwd = tmp_path / "moved"
    original_cwd.mkdir()
    moved_cwd.mkdir()
    session = _RecordingSession()
    backend, _ = await _unified_adapter_with_real_context(
        original_cwd, session, deferred_turns=session
    )
    original_context = backend._context
    moved_session = _RecordingSession()
    moved_backend, _ = await _unified_adapter_with_real_context(
        moved_cwd, moved_session, deferred_turns=moved_session
    )
    session.cwd = str(original_cwd.resolve())
    session.active_turn_id = "turn-active"
    services = Mock()
    services.notify = AsyncMock()
    host = UnifiedHarnessBackendHostAdapter(cast(Any, Mock()), AsyncMock(), services)
    cast(Any, host)._lifecycle_context = AsyncMock(
        return_value=(moved_backend._context, object())
    )

    # Do
    with pytest.raises(SessionBackendError, match="could not be applied"):
        await host._move_session(backend, SessionOptions(cwd=str(moved_cwd)))

    # Assert
    assert backend._context is original_context
    assert session.cwd == str(original_cwd.resolve())
    services.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_context_adoption_uses_the_result_of_its_own_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A successful context apply followed by another parked write.
    *Do*: Adopt the context while the deferred state reports the later write.
    *Assert*: The successful context remains adopted.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    # Prepare
    session = _RecordingSession()
    backend, _ = await _unified_adapter_with_real_context(tmp_path, session)
    replacement, _ = await _unified_adapter_with_real_context(
        tmp_path, _RecordingSession()
    )
    monkeypatch.setattr(backend, "_apply_derivation", AsyncMock(return_value=True))
    backend._deferred.park()

    # Do
    await backend.adopt_context(replacement._context)

    # Assert
    assert backend._context is replacement._context
    assert backend._deferred.parked is True


@pytest.mark.asyncio
async def test_first_turn_after_enqueue_skips_deferred_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A deferred session promoted by enqueueing its first turn.
    *Do*: Start another turn after workspace setup finishes.
    *Assert*: The promoted session uses the normal start path.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    class _PromotedSession(_RecordingSession):
        def __init__(self) -> None:
            super().__init__()
            self.ephemeral = True

        async def enqueue_turn(self, params: Any) -> Any:
            result = await super().enqueue_turn(params)
            self.ephemeral = False
            return result

        async def start_deferred_turn(
            self, params: HarnessDeferredTurnStartParams
        ) -> HarnessDeferredTurnStartResult:
            if not self.ephemeral:
                raise RuntimeError(
                    "Deferred turn preparation requires an ephemeral session"
                )
            return await super().start_deferred_turn(params)

    # Prepare
    session = _PromotedSession()
    adapter, _ = await _unified_adapter_with_real_context(
        tmp_path, session, deferred_turns=session
    )

    async def setup() -> WorktreeResolution:
        return WorktreeResolution(options=SessionOptions(cwd=str(tmp_path)))

    adapter.defer_turns(setup())
    await adapter.enqueue_turn(
        TurnEnqueueParams(
            session_id=session.session_id,
            entries=[
                TurnUserInputEntry(content=[SessionTextContentBlock(text="queued")])
            ],
        )
    )

    # Do
    result = await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id,
            message=[TextContentBlock(text="hello")],
            client_user_message_id="message-1",
        )
    )

    # Assert
    assert result.response.turn.id == "turn-1"
    assert not hasattr(session, "deferred_turn_params")
    assert session.ephemeral is False


@pytest.mark.asyncio
async def test_deferred_start_falls_back_when_concurrent_promotion_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    class _PromotedDuringDeferredStartSession(_RecordingSession):
        def __init__(self) -> None:
            super().__init__()
            self.ephemeral = True
            self.deferred_starts = 0
            self.normal_starts = 0

        async def start_deferred_turn(
            self, params: HarnessDeferredTurnStartParams
        ) -> HarnessDeferredTurnStartResult:
            del params
            self.deferred_starts += 1
            self.ephemeral = False
            raise RuntimeError(
                "Deferred turn preparation requires an ephemeral session"
            )

        async def start_turn(self, params: Any) -> Any:
            self.normal_starts += 1
            return await super().start_turn(params)

    session = _PromotedDuringDeferredStartSession()
    adapter = _inert_adapter(
        session, str(tmp_path), str(tmp_path), deferred_turns=session
    )
    params = TurnStartParams(
        session_id=session.session_id,
        message=[TextContentBlock(text="hello")],
        client_user_message_id="message-1",
    )

    async def setup() -> WorktreeResolution:
        return WorktreeResolution(options=SessionOptions(cwd=str(tmp_path)))

    monkeypatch.setattr(adapter, "_settle_reserved_turn_configuration", AsyncMock())
    monkeypatch.setattr(
        adapter, "_pin_session_model_choice", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        adapter, "_prepared_turn_params", AsyncMock(return_value=params)
    )
    monkeypatch.setattr(
        adapter, "_persist_scheduled_loops_after_promotion", AsyncMock()
    )
    monkeypatch.setattr(
        adapter, "_persist_session_metadata_after_promotion", AsyncMock()
    )
    adapter.defer_turns(setup())

    result = await adapter.start_turn(params)

    assert result.response.turn.id == "turn-1"
    assert session.deferred_starts == 1
    assert session.normal_starts == 1
    assert not adapter._deferred_turn_claimed


@pytest.mark.asyncio
async def test_pending_derivation_flushes_across_a_reserved_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A parked derivation and a reserved worktree setup turn.
    *Do*: Attempt the idle push, then settle during deferred turn preparation.
    *Assert*: Only the settle applies configuration across the reservation. A
    reserved turn reads as running, so the idle push parks rather than landing
    on a turn that has not started.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    # Prepare
    session = _RecordingSession()
    session.reserved_turn_id = "turn-reserved"
    adapter, derivation = await _unified_adapter_with_real_context(
        tmp_path, session, deferred_turns=session
    )
    adapter._deferred.park()

    # Do
    await adapter._apply_derivation_when_idle()
    await adapter._settle_reserved_turn_configuration()

    # Assert
    assert session.allow_reserved_flags == [True]
    assert len(session.settings) == 1
    assert session.system_instructions == [derivation.core_config.system_instructions]
    assert session.core_configs == [derivation.core_config]
    assert session.cwd == str(derivation.adapter_config.workspace.cwd)


@pytest.mark.asyncio
async def test_a_pick_settled_for_a_reserved_turn_is_announced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A parked pick and a reserved worktree setup turn.
    *Do*: Settle during deferred turn preparation.
    *Assert*: ``runtime/updated`` is emitted. The write that parked the pick
    answered ``pending``, and a reserved turn never reaches the boundary the
    other settle announces from, so subscribers would otherwise be left on the
    outgoing model for the whole of the turn that is about to run.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    # Prepare
    session = _RecordingSession()
    session.reserved_turn_id = "turn-reserved"
    services = Mock()
    services.notify = AsyncMock()
    adapter, _ = await _unified_adapter_with_real_context(
        tmp_path, session, deferred_turns=session, services=services
    )
    adapter._deferred.park()

    # Do
    await adapter._settle_reserved_turn_configuration()

    # Assert
    services.notify.assert_awaited_once()
    assert services.notify.await_args.args[0] == "runtime/updated"


@pytest.mark.asyncio
async def test_a_reserved_turn_settle_waits_for_an_apply_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A turn reserved for workspace setup, and an apply already
    mid-derive -- so the parked flag is clear while its push is still coming.
    *Do*: Settle for the reserved turn, as deferred turn preparation does.
    *Assert*: It waits for that apply, finds what the apply re-parked, and
    lands it. Reading the flag outside the lock steps past a push in flight,
    and the reserved turn then runs on the configuration being replaced.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    # Prepare
    session = _RecordingSession()
    session.reserved_turn_id = "turn-reserved"
    adapter, _ = await _unified_adapter_with_real_context(
        tmp_path, session, deferred_turns=session
    )
    release = asyncio.Event()
    # Patched on the deferred configuration, not the adapter: it captured the
    # bound method when it was built, so patching the adapter gates nothing.
    derive = adapter._deferred._derive

    async def gated_derive() -> Any:
        await release.wait()
        return await derive()

    monkeypatch.setattr(adapter._deferred, "_derive", gated_derive)
    adapter._deferred.park()
    in_flight = asyncio.create_task(adapter._deferred.apply())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # Do
    settling = asyncio.create_task(adapter._settle_reserved_turn_configuration())
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(in_flight, settling)

    # Assert
    assert session.allow_reserved_flags == [True]
    assert len(session.settings) == 1


@pytest.mark.asyncio
async def test_a_parked_writes_projection_waits_for_the_configuration_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A running turn, so the write parks, and the configuration
    lock held by another caller.
    *Do*: Apply the written config.
    *Assert*: The projection waits for the lock. Derived off-lock, it can be
    computed before a settle adopts a newer configuration and assigned after,
    leaving the session reporting a runtime it is no longer running.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    # Prepare
    session = _RecordingSession()
    session.active_turn_id = "turn-1"
    adapter, _ = await _unified_adapter_with_real_context(tmp_path, session)
    released = asyncio.Event()

    async def hold_the_lock() -> None:
        async with adapter._deferred.exclusive():
            await released.wait()

    holding = asyncio.create_task(hold_the_lock())
    await asyncio.sleep(0)

    # Do
    writing = asyncio.create_task(adapter._apply_written_config())
    # Long enough for an off-lock derivation to finish and assign: the point is
    # that no wait makes it finish while another caller holds the lock.
    await asyncio.sleep(0.1)

    # Assert
    assert writing.done() is False

    released.set()
    await asyncio.gather(holding, writing)
    assert adapter._deferred.parked is True


@pytest.mark.asyncio
async def test_a_queued_turn_waits_behind_the_same_gate(tmp_path: Path) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    from vibe.app_server._session_backend_port import SessionBackendError

    adapter = _inert_adapter(
        _session_stub(session_id="session-1", cwd=str(tmp_path)),
        str(tmp_path),
        str(tmp_path),
    )
    started = asyncio.Event()

    async def never_finishes() -> None:
        started.set()
        await asyncio.Event().wait()

    adapter.defer_turns(never_finishes())
    await started.wait()
    queued = asyncio.create_task(
        adapter.enqueue_turn(
            TurnEnqueueParams(
                session_id="session-1",
                entries=[
                    TurnUserInputEntry(content=[SessionTextContentBlock(text="hello")])
                ],
            )
        )
    )
    await asyncio.sleep(0)

    assert not queued.done()

    adapter._deferred_setup_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await adapter._deferred_setup_task

    with pytest.raises(SessionBackendError):
        await queued


@pytest.mark.asyncio
async def test_a_turn_is_refused_when_the_setup_failed(tmp_path: Path) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    from vibe.app_server._session_backend_port import SessionBackendError

    adapter = _inert_adapter(
        _session_stub(session_id="session-1", cwd=str(tmp_path)),
        str(tmp_path),
        str(tmp_path),
    )

    async def fails() -> None:
        raise RuntimeError("no worktree for you")

    adapter.defer_turns(fails())

    with pytest.raises(SessionBackendError):
        await adapter._await_deferred_setup()


def _saved_legacy_session(save_dir: Path, session_id: str) -> Path:
    """A session on disk in the shape the store before the Harness left behind."""
    from vibe.utils.session_id import shorten_session_id

    session_dir = save_dir / f"session_20260901_105513_{shorten_session_id(session_id)}"
    session_dir.mkdir(parents=True)
    (session_dir / "meta.json").write_text(
        json.dumps({"session_id": session_id}), encoding="utf-8"
    )
    return session_dir


def _legacy_store_config(save_dir: Path) -> Any:
    return build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(save_dir), session_prefix="session"
        )
    )


@pytest.mark.asyncio
async def test_unified_host_deletes_a_session_only_the_legacy_store_has(
    tmp_path: Path,
) -> None:
    """*Prepare*: A saved session the Harness has never seen, which `list` still shows.
    *Do*: Delete it through the public session route.
    *Assert*: The route succeeds and the directory is gone.
    """
    # Prepare
    save_dir = tmp_path / "sessions"
    session_id = "ed37b4ae-9623-15ec-3363-289c2a531a0e"
    session_dir = _saved_legacy_session(save_dir, session_id)
    client, server = _connect_harness_host(_legacy_store_config(save_dir))
    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")

        # Do
        deleted = EmptyResponse.model_validate(
            await client.request(
                "session/delete", SessionDeleteParams(session_id=session_id)
            )
        )

        # Assert
        assert isinstance(deleted, EmptyResponse)
        assert not session_dir.exists()
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_unified_host_still_reports_a_session_neither_store_has(
    tmp_path: Path,
) -> None:
    """The reach into the legacy store must not turn a missing session into a success."""
    client, server = _connect_harness_host(_legacy_store_config(tmp_path / "sessions"))
    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")

        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                "session/delete",
                SessionDeleteParams(session_id="00000000-0000-0000-0000-000000000000"),
            )

        assert exc_info.value.error.code is ProtocolErrorCode.NOT_FOUND
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_unified_pin_updates_legacy_session_and_projects_saved_pin(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    legacy = build_test_agent_loop(config=config, cwd=tmp_path)
    legacy.messages.append(LLMMessage(role=Role.user, content="legacy hello"))
    await legacy.session_logger.save_interaction(
        legacy.messages,
        legacy.stats,
        legacy.config,
        legacy.tool_manager,
        legacy.agent_profile,
    )
    session_id = legacy.session_id
    await legacy.aclose()
    host = _harness_backend_host(config)
    try:
        assert isinstance(host, SessionBackendHostPin)
        pinned = await host.pin(SessionPinParams(session_id=session_id, pinned=True))
        repeated = await host.pin(SessionPinParams(session_id=session_id, pinned=True))
        assert pinned.pinned_at is not None
        assert repeated.pinned_at == pinned.pinned_at
        listed = await host.list(SessionListParams(pinned=True))
        assert [(item.id, item.pinned_at) for item in listed.items] == [
            (session_id, pinned.pinned_at)
        ]
        read = await host.read(SessionReadParams(session_id=session_id))
        assert read.state.session.pinned_at == pinned.pinned_at
        unpinned = await host.pin(SessionPinParams(session_id=session_id, pinned=False))
        assert unpinned.pinned_at is None
        assert (await host.list(SessionListParams(pinned=True))).items == []
        with pytest.raises(SessionBackendError) as error:
            await host.pin(SessionPinParams(session_id="missing-session", pinned=True))
        assert error.value.code is ProtocolErrorCode.NOT_FOUND
    finally:
        await host.shutdown()


@pytest.mark.asyncio
async def test_unified_list_interleaves_pin_filtered_cursor_walks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibe.app_server import _legacy_import as legacy_import_module
    from vibe.core.session.resume_sessions import ResumeSessionInfo

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    stored = [
        ResumeSessionInfo(
            session_id=f"legacy-{index}",
            cwd=str(tmp_path),
            title=f"title {index}",
            updated_at=f"2026-01-01T00:00:{index:02d}",
            pinned_at="2026-01-01T00:00:00Z" if index % 2 else None,
        )
        for index in range(6)
    ]
    monkeypatch.setattr(
        legacy_import_module, "list_local_resume_sessions", lambda _config, _cwd: stored
    )
    host = _harness_backend_host(config)
    try:
        pinned = await host.list(SessionListParams(pinned=True, limit=1))
        unpinned = await host.list(SessionListParams(pinned=False, limit=1))
        assert pinned.next_cursor is not None
        assert unpinned.next_cursor is not None
        pinned_next = await host.list(
            SessionListParams(pinned=True, limit=10, cursor=pinned.next_cursor)
        )
        unpinned_next = await host.list(
            SessionListParams(pinned=False, limit=10, cursor=unpinned.next_cursor)
        )
        assert [item.id for item in pinned.items + pinned_next.items] == [
            "legacy-5",
            "legacy-3",
            "legacy-1",
        ]
        assert [item.id for item in unpinned.items + unpinned_next.items] == [
            "legacy-4",
            "legacy-2",
            "legacy-0",
        ]
    finally:
        await host.shutdown()


@pytest.mark.asyncio
async def test_disabled_session_logging_keeps_the_context_off_the_save_dir(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    save_dir = tmp_path / "sessions"
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["session_logging"] = {"enabled": False, "save_dir": str(save_dir)}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    process = runtime_module.HarnessProcess(experimental_harness=True)
    try:
        # Do
        first = await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path))
        )
        second = await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path))
        )

        # Assert
        root = Path(first.storage_root)
        assert first.session_logging_enabled is False
        assert root.is_dir()
        assert root != save_dir and save_dir not in root.parents
        assert second.storage_root == first.storage_root
    finally:
        await process.close()

    assert not root.exists()
    assert not (save_dir / "unified").exists()


@pytest.mark.asyncio
async def test_enabled_session_logging_still_uses_the_configured_save_dir(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    save_dir = tmp_path / "sessions"
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["session_logging"] = {"enabled": True, "save_dir": str(save_dir)}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    process = runtime_module.HarnessProcess(experimental_harness=True)
    try:
        context = await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path))
        )
    finally:
        await process.close()

    assert context.storage_root == str(save_dir)
    assert context.session_logging_enabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize("session_logging_enabled", [True, False])
async def test_session_log_summary_and_store_follow_session_logging_enabled(
    tmp_path: Path, session_logging_enabled: bool
) -> None:
    # Prepare
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    save_dir = tmp_path / "sessions"
    throwaway = tmp_path / "throwaway"
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=session_logging_enabled, save_dir=str(save_dir)
        )
    )
    host = adapt_harness_host(
        vibe_runtime.create_harness_host(),
        _test_session_runtime_builder(
            config, storage_root=str(save_dir if session_logging_enabled else throwaway)
        ),
    )
    try:
        started = await host.start(SessionStartParams())
        backend = cast(Any, started.backend)

        # Do
        turn = await backend.start_turn(
            TurnStartParams(
                session_id=backend.session_id, message=[TextContentBlock(text="hello")]
            )
        )
        summary = await backend._session_log_summary()

        # Assert
        assert turn.response.turn.id is not None
        assert summary.enabled is session_logging_enabled
        assert summary.persisted is session_logging_enabled
        session_id = backend.session_id
    finally:
        await host.shutdown()

    if session_logging_enabled:
        assert summary.path == str(save_dir / "unified" / session_id)
        assert (save_dir / "unified" / session_id / "CURRENT").is_file()
        assert (save_dir / "unified" / session_id / "meta.json").is_file()
    else:
        assert summary.path is None
        assert not (save_dir / "unified").exists()
        assert (throwaway / "unified" / session_id / "CURRENT").is_file()
        # The summary says the session is not persisted, so the writers that
        # answer to the same question must not have run either.
        assert not (throwaway / "unified" / session_id / "meta.json").exists()
        assert not (
            throwaway / "unified" / session_id / "scheduled-loops.json"
        ).exists()


@pytest.mark.asyncio
async def test_disabled_session_logging_store_is_removed_when_the_session_closes(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    save_dir = tmp_path / "sessions"
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["session_logging"] = {"enabled": False, "save_dir": str(save_dir)}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    process = runtime_module.HarnessProcess(experimental_harness=True)
    client_transport, server_transport = memory_transport_pair()
    harness = await runtime_module.create_harness_server(
        server_transport, transport_kind="in_process", process=process
    )
    client = AppServerClient(client_transport, run_peer=harness.serve)
    session = await AppServerSession.start(
        client,
        client_info=ClientInfo(name="test", version="0"),
        capabilities=ClientCapabilities(),
        session_options=SessionOptions(cwd=str(tmp_path)),
    )
    root = process._throwaway_storage_root.current
    assert root is not None and root.is_dir()

    # Do — closing the session is as far out as the TUI and ``vibe -p`` get, so
    # ``HarnessProcess.close`` is deliberately never called.
    await session.close()

    # Assert
    assert not root.exists()
    assert not (save_dir / "unified").exists()


async def _disabled_logging_session(
    process: runtime_module.HarnessProcess, cwd: Path
) -> AppServerSession:
    client_transport, server_transport = memory_transport_pair()
    harness = await runtime_module.create_harness_server(
        server_transport, transport_kind="in_process", process=process
    )
    return await AppServerSession.start(
        AppServerClient(client_transport, run_peer=harness.serve),
        client_info=ClientInfo(name="test", version="0"),
        capabilities=ClientCapabilities(),
        session_options=SessionOptions(cwd=str(cwd)),
    )


def _write_disabled_session_logging(config_dir: Path, save_dir: Path) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["session_logging"] = {"enabled": False, "save_dir": str(save_dir)}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


@pytest.mark.asyncio
async def test_disabled_session_logging_builds_a_fresh_store_after_the_last_release(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Prepare — ACP keeps one HarnessProcess across close_session / new_session,
    # so releasing the store on a stop must not stop the next session getting one.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    save_dir = tmp_path / "sessions"
    _write_disabled_session_logging(config_dir, save_dir)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    process = runtime_module.HarnessProcess(experimental_harness=True)
    try:
        lease = process._throwaway_storage_root.lease()
        first = await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path))
        )
        first_root = Path(first.storage_root)
        assert first_root.is_dir()

        # Do
        lease.release()
        assert not first_root.exists()
        second = await process.build_unified_session_context(
            SessionOptions(cwd=str(tmp_path))
        )

        # Assert
        second_root = Path(second.storage_root)
        assert second_root.is_dir()
        assert second_root != first_root
    finally:
        await process.close()

    assert not (save_dir / "unified").exists()


def test_releasing_one_throwaway_lease_twice_leaves_the_other_holder_alone() -> None:
    # Prepare — `session/stop` and a later disconnect each shut the same app
    # server's host down, so one lease is released twice while a second app
    # server still holds the store.
    root = runtime_module.ThrowawayStorageRoot()
    first = root.lease()
    second = root.lease()
    path = root.path()
    assert path.is_dir()

    # Do
    first.release()
    first.release()

    # Assert
    assert path.is_dir()
    assert root.current == path
    second.release()
    assert not path.exists()


@pytest.mark.asyncio
async def test_disabled_session_logging_keeps_the_store_while_a_session_still_holds_it(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    save_dir = tmp_path / "sessions"
    _write_disabled_session_logging(config_dir, save_dir)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    process = runtime_module.HarnessProcess(experimental_harness=True)
    try:
        first = await _disabled_logging_session(process, tmp_path)
        second = await _disabled_logging_session(process, tmp_path)
        root = process._throwaway_storage_root.current
        assert root is not None and root.is_dir()

        # Do
        await first.close()

        # Assert — the second session is still writing into this directory.
        assert root.is_dir()
        await second.close()
        assert not root.exists()
    finally:
        await process.close()

    assert not (save_dir / "unified").exists()


@pytest.mark.asyncio
async def test_shared_process_keeps_the_disabled_logging_store_until_it_closes(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    save_dir = tmp_path / "sessions"
    _write_disabled_session_logging(config_dir, save_dir)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    process = runtime_module.HarnessProcess(experimental_harness=True, shared=True)
    session = await _disabled_logging_session(process, tmp_path)
    root = process._throwaway_storage_root.current
    assert root is not None and root.is_dir()

    try:
        # Do
        await session.close()

        # Assert — the Host outlives the session and still writes into the store.
        assert root.is_dir()
    finally:
        await process.close()

    assert not root.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("lifecycle", ["resume", "continue"])
async def test_disabled_session_logging_refuses_to_reopen_a_stored_session(
    tmp_path: Path, lifecycle: str
) -> None:
    # Prepare
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=False, save_dir=str(tmp_path / "sessions")
        )
    )
    host = adapt_harness_host(
        vibe_runtime.create_harness_host(),
        _test_session_runtime_builder(config, storage_root=str(tmp_path / "throwaway")),
    )

    try:
        # Do
        with pytest.raises(SessionBackendError) as caught:
            if lifecycle == "resume":
                await host.resume(SessionResumeParams(session_id="stored-session"))
            else:
                await host.continue_latest(SessionContinueParams())
    finally:
        await host.shutdown()

    # Assert
    assert caught.value.code == ProtocolErrorCode.NOT_FOUND
    assert "Session logging is disabled" in str(caught.value)


@pytest.mark.asyncio
async def test_disabled_session_logging_inlines_mentioned_images(
    tmp_path: Path,
) -> None:
    # Prepare
    image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    (tmp_path / "shot.png").write_bytes(image_bytes)
    save_dir = tmp_path / "sessions"
    config = build_test_vibe_config(
        active_model="local-llamacpp",
        models=[
            ModelConfig(name="devstral", provider="llamacpp", alias="local-llamacpp")
        ],
        session_logging=SessionLoggingConfig(enabled=False, save_dir=str(save_dir)),
    )
    client, server = _connect_harness_host(config)
    await client.initialize(ClientInfo(name="test", version="0"))
    await client.notify("initialized")
    started = SessionReadResponse.model_validate(
        await client.request(
            "session/start",
            SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path))),
        )
    )

    try:
        # Do
        response = WorkspacePromptPrepareResponse.model_validate(
            await client.request(
                "workspace/prompt/prepare",
                WorkspacePromptPrepareParams(
                    session_id=started.state.session.id, message="look at @shot.png"
                ),
            )
        )
    finally:
        await server.close()

    # Assert
    assert len(response.prompt.images) == 1
    source = response.prompt.images[0].source
    assert isinstance(source, InlineImageSource)
    assert base64.b64decode(source.data) == image_bytes
    assert not (save_dir / "unified").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "/loop",
        "/loop list",
        "/loop cancel all",
        "/loop every ninety seconds check the build",
        "/loop weekdays at 9am review CI",
    ],
)
async def test_loop_start_preserves_user_text_and_adds_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prompt: str
) -> None:
    from vibe.app_server._loop_prompt import LOOP_INSTRUCTIONS

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    _write_workspace_skill(tmp_path, "loop", "Unrelated skill instructions")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)
    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id, message=[TextContentBlock(text=prompt)]
        )
    )
    assert session.sent[-1].message == [
        TextContentBlock(text=prompt),
        TextContentBlock(text=LOOP_INSTRUCTIONS),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("replace", [False, True])
async def test_loop_queued_input_preserves_user_text_and_adds_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace: bool
) -> None:
    from vibe.app_server._loop_prompt import LOOP_INSTRUCTIONS

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    _write_workspace_skill(tmp_path, "loop", "Unrelated skill instructions")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)
    from vibe.app_server.models import TurnInputEntry

    entries: list[TurnInputEntry] = [
        TurnUserInputEntry(
            content=[SessionTextContentBlock(text="/loop every two hours check CI")]
        )
    ]
    if replace:
        await adapter.replace_queued_turn(
            TurnQueueReplaceParams(
                session_id=session.session_id, queue_item_id="q1", entries=entries
            )
        )
    else:
        await adapter.enqueue_turn(
            TurnEnqueueParams(session_id=session.session_id, entries=entries)
        )
    assert (
        session.sent[-1].entries[-1].content[0].text == "/loop every two hours check CI"
    )
    assert len(session.sent[-1].entries[-1].content) == 2
    assert session.sent[-1].entries[-1].content[-1].text == LOOP_INSTRUCTIONS


@pytest.mark.asyncio
async def test_loop_steering_adds_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibe.app_server._loop_prompt import LOOP_INSTRUCTIONS

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    _write_workspace_skill(tmp_path, "loop", "Unrelated skill instructions")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session)
    await adapter.steer_turn(
        TurnSteerParams(
            session_id=session.session_id,
            expected_turn_id="turn-1",
            message=[TextContentBlock(text="/loop cancel all")],
        )
    )
    assert session.sent[-1].message[-1].text == LOOP_INSTRUCTIONS


@pytest.mark.asyncio
async def test_cron_tool_uses_the_live_hosts_scheduler(tmp_path: Path) -> None:
    from mistralai_vibe_local_harness.protocol import (
        RustProvidedToolCall,
        RustProvidedToolCallAction,
        RustToolSucceededEvent,
    )
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedHarnessBackendHostAdapter,
    )

    host = _harness_backend_host()
    assert isinstance(host, UnifiedHarnessBackendHostAdapter)
    try:
        opened = await host.start(SessionStartParams())
        adapter = opened.backend
        assert isinstance(adapter, UnifiedHarnessBackendAdapter)
        execute = adapter._context.vibe_tools.executor_factory(
            tmp_path, max_todos=lambda: 10, scheduled_loops=host.cron_scheduler
        )(adapter.session_id)
        result = await execute(
            RustProvidedToolCallAction(
                action_id="cron-1",
                turn_id="turn-1",
                call_id="call-1",
                call=RustProvidedToolCall(
                    group_name="vibe",
                    tool_name="cron",
                    arguments={
                        "action": "schedule_cron",
                        "cron": "0 9 * * 1-5",
                        "prompt": "check CI",
                    },
                ),
            )
        )
        assert isinstance(result, RustToolSucceededEvent)
        loops = await adapter.scheduled_loops()
        assert len(loops) == 1
        assert loops[0].cron == "0 9 * * 1-5"
        assert loops[0].prompt == "check CI"

        await adapter.shutdown()
        from mistralai_vibe_local_harness.protocol import RustToolFailedEvent

        rejected = await execute(
            RustProvidedToolCallAction(
                action_id="cron-stale",
                turn_id="turn-1",
                call_id="call-stale",
                call=RustProvidedToolCall(
                    group_name="vibe",
                    tool_name="cron",
                    arguments={
                        "action": "schedule",
                        "interval_seconds": 30,
                        "prompt": "stale",
                    },
                ),
            )
        )
        assert isinstance(rejected, RustToolFailedEvent)
        assert "live interactive session" in rejected.result.error.message
        assert await adapter.scheduled_loops() == loops
    finally:
        await host.shutdown()


def _enforce_admin_toml(
    monkeypatch: pytest.MonkeyPatch, toml_text: str = 'theme = "nord"\n'
) -> None:
    from vibe.app_server import _admin_config
    from vibe.core.config.admin_config import ManagedConfig, ManagedConfigResult

    async def fake_fetch(_base_url: str, _api_key: str) -> ManagedConfigResult:
        return ManagedConfigResult(
            config=ManagedConfig(state="enabled", toml=toml_text)
        )

    monkeypatch.setattr(_admin_config, "resolve_api_key", lambda _env: "api-key")
    monkeypatch.setattr(_admin_config, "fetch_managed_config", fake_fetch)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")


@pytest.mark.asyncio
async def test_a_mid_turn_admin_config_fetch_defers_instead_of_announcing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The Core reads its settings at turn start, so the derivation cannot land
    # until the turn ends -- announcing it now would describe a runtime nothing
    # runs.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedRuntimeDerivation,
        UnifiedSessionContext,
    )
    from vibe.core.config.layers.admin import AdminConfigLayer

    _enforce_admin_toml(monkeypatch)

    orchestrator = FakeConfigOrchestrator[VibeConfigSchema](build_test_vibe_config())
    orchestrator.insert_layer(AdminConfigLayer(), 0)
    harness_files = get_harness_files_manager()
    agents = AgentManager(
        orchestrator, orchestrator.config.default_agent, harness_files=harness_files
    )
    derivation = UnifiedRuntimeDerivation(
        runtime=build_unified_runtime_snapshot(orchestrator, agents),
        core_config=_stub_core_config(),
        adapter_config=_stub_adapter_config(),
        skill_payloads={},
    )
    context = UnifiedSessionContext(
        storage_root=str(tmp_path),
        legacy_source_loader=cast(Any, None),
        legacy_source_resolver=cast(Any, None),
        plugins=cast(Any, object()),
        plugin_provider=cast(Any, object()),
        requested_plugins=(),
        config_orchestrator=cast(Any, orchestrator),
        harness_files=harness_files,
        agents=agents,
        derive=lambda _settings: derivation,
        permissions=cast(Any, None),
        mcp_catalog=ResolvedMCPCatalog(revision="test", servers=()),
        mcp_authorization_provider=MCPAuthenticationService(),
        plugin_mcp=_empty_plugin_mcp(),
        mcp_cache_root=str(tmp_path / "mcp-descriptors"),
        mcp_enable_system_trust_store=False,
    )
    session = _RecordingSession()
    session.active_turn_id = "turn-1"
    adapter = UnifiedHarnessBackendAdapter(cast(Any, session), context, derivation)

    changed = await adapter.refresh_admin_config()

    assert changed is False
    assert adapter._deferred.parked is True
    layer = orchestrator.get_layer(AdminConfigLayer.NAME)
    assert isinstance(layer, AdminConfigLayer)
    assert layer.enforced_keys == ["theme"]


@pytest.mark.asyncio
async def test_opening_a_unified_session_enforces_org_managed_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The enforced layer has to shadow the session's live config without the
    # user having to run ``/reload``, and the client has to be told.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.config.layers.admin import AdminConfigLayer

    _enforce_admin_toml(monkeypatch)

    services = FakeSessionBackendServices()
    process = runtime_module.HarnessProcess(experimental_harness=True)
    # The real catalog fetch fails offline and emits its own runtime/updated.
    monkeypatch.setattr(
        process.connector_catalog, "resolve_catalog", AsyncMock(return_value=None)
    )
    host = process.create_session_backend_host(cast(Any, services))
    try:
        started = await host.start(
            SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path)))
        )
        backend = cast(Any, started.backend)
        orchestrator = backend._context.config_orchestrator
        assert started.after_response is not None
        started.after_response()
        assert backend._admin_config_task is not None
        await backend._admin_config_task
        if backend._connector_resolve_task is not None:
            await backend._connector_resolve_task

        assert orchestrator.config.theme == "nord"
        layer = orchestrator.get_layer(AdminConfigLayer.NAME)
        assert isinstance(layer, AdminConfigLayer)
        assert layer.enforced_keys == ["theme"]
        assert [method for method, _ in services.notifications] == ["runtime/updated"]
    finally:
        await host.shutdown()
        await process.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("toml", "expected_error"),
    [
        ('allowed_models = ["missing-model"]\n', "matches none"),
        ('active_model = "unterminated\n', "Illegal character"),
    ],
)
async def test_opening_a_unified_session_notifies_invalid_managed_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, toml: str, expected_error: str
) -> None:
    """A bad managed policy must not silently leave a new session unrestricted."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server import _admin_config
    from vibe.core.config.admin_config import ManagedConfig, ManagedConfigResult

    allow_fetch = asyncio.Event()

    async def fake_fetch(_base_url: str, _api_key: str) -> ManagedConfigResult:
        await allow_fetch.wait()
        return ManagedConfigResult(config=ManagedConfig(state="enabled", toml=toml))

    monkeypatch.setattr(_admin_config, "resolve_api_key", lambda _env: "api-key")
    monkeypatch.setattr(_admin_config, "fetch_managed_config", fake_fetch)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    services = FakeSessionBackendServices()
    process = runtime_module.HarnessProcess(experimental_harness=True)
    host = process.create_session_backend_host(cast(Any, services))
    try:
        started = await host.start(
            SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path)))
        )
        backend = cast(Any, started.backend)
        notices: list[tuple[str, str]] = []
        backend._session.publish_notice = lambda message, level="warning": (
            notices.append((message, level))
        )

        allow_fetch.set()
        await backend._admin_config_task

        assert len(notices) == 1
        message, level = notices[0]
        assert message.startswith(
            "Your administrator-managed configuration could not be applied: "
        )
        assert expected_error in message
        assert level == "error"
        assert services.notifications == []
    finally:
        await host.shutdown()
        await process.close()


@pytest.mark.asyncio
async def test_ready_wait_blocks_on_the_admin_config_fetch(tmp_path: Path) -> None:
    # Whatever the org enforces has to be live before the first turn, so
    # session/ready/wait waits on the open-time fetch the same way it waits on
    # the experiments eval — otherwise that turn runs under the overridden
    # settings and the enforcement only lands from the second turn on.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    fetched = False

    async def _fetch() -> None:
        nonlocal fetched
        await asyncio.sleep(0.05)
        fetched = True

    adapter = _inert_adapter(_RecordingSession(), str(tmp_path), str(tmp_path))
    adapter._admin_config_task = asyncio.create_task(_fetch())

    result = await adapter.dispatch_extension("session/ready/wait", {})

    assert fetched is True
    assert result.response.ready is True


@pytest.mark.asyncio
async def test_ready_wait_stays_ready_when_the_admin_fetch_is_cancelled(
    tmp_path: Path,
) -> None:
    # A cancelled fetch means shutdown or a newer refresh superseded this one;
    # neither says anything about whether the session itself came up, so unlike
    # the experiments eval it must not flip the session to not-ready.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    async def _never() -> None:
        await asyncio.sleep(3600)

    adapter = _inert_adapter(_RecordingSession(), str(tmp_path), str(tmp_path))
    task = asyncio.create_task(_never())
    adapter._admin_config_task = task
    task.cancel()

    result = await adapter.dispatch_extension("session/ready/wait", {})

    assert result.response.ready is True


@pytest.mark.asyncio
async def test_clearing_history_refetches_the_org_managed_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ``/clear`` swaps in a brand-new backend, and the one it replaces owned the
    # open-time fetch. Without a fetch of its own the replacement runs unenforced.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    _enforce_admin_toml(monkeypatch)

    services = FakeSessionBackendServices()
    process = runtime_module.HarnessProcess(experimental_harness=True)
    host = process.create_session_backend_host(cast(Any, services))
    try:
        started = await host.start(
            SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path)))
        )
        assert started.after_response is not None
        started.after_response()

        cleared = await cast(Any, host).clear_history(
            started.backend,
            SessionHistoryClearParams(session_id=started.backend.session_id),
        )

        backend = cast(Any, cleared.backend)
        assert cleared.after_response is not None
        cleared.after_response()
        assert backend._admin_config_task is not None
        await backend._admin_config_task
        assert backend._context.config_orchestrator.config.theme == "nord"
    finally:
        await host.shutdown()
        await process.close()


@pytest.mark.asyncio
async def test_a_detached_fork_does_not_fetch_the_admin_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A detached fork is shut down before it can ever run a turn, so a fetch on
    # its behalf only costs the admin endpoint a request nobody reads.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server import _admin_config

    fetches = 0

    async def counting_fetch(_base_url: str, _api_key: str) -> object:
        nonlocal fetches
        fetches += 1
        raise OSError("admin endpoint is unreachable")

    monkeypatch.setattr(_admin_config, "resolve_api_key", lambda _env: "api-key")
    monkeypatch.setattr(_admin_config, "fetch_managed_config", counting_fetch)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    services = FakeSessionBackendServices()
    process = runtime_module.HarnessProcess(experimental_harness=True)
    host = process.create_session_backend_host(cast(Any, services))
    try:
        started = await host.start(
            SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path)))
        )
        assert started.after_response is not None
        started.after_response()
        backend = cast(Any, started.backend)
        await backend._admin_config_task
        assert fetches == 1

        await host.fork(
            SessionForkParams(source_session_id=backend.session_id, attach=False)
        )

        assert fetches == 1
    finally:
        await host.shutdown()
        await process.close()


@pytest.mark.asyncio
async def test_unreachable_admin_endpoint_never_fails_a_unified_session_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fetch is backgrounded precisely so the session does not wait on, or die
    # with, the admin endpoint -- the same contract the legacy backend has.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server import _admin_config
    from vibe.core.config.admin_config import ManagedConfigResult

    async def unreachable(_base_url: str, _api_key: str) -> ManagedConfigResult:
        return ManagedConfigResult(error="admin endpoint is unreachable")

    monkeypatch.setattr(_admin_config, "resolve_api_key", lambda _env: "api-key")
    monkeypatch.setattr(_admin_config, "fetch_managed_config", unreachable)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    services = FakeSessionBackendServices()
    process = runtime_module.HarnessProcess(experimental_harness=True)
    host = process.create_session_backend_host(cast(Any, services))
    try:
        started = await host.start(
            SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path)))
        )
        backend = cast(Any, started.backend)
        assert started.after_response is not None
        started.after_response()
        await backend._admin_config_task

        assert backend.session_id
        assert services.notifications == []
    finally:
        await host.shutdown()
        await process.close()


def test_session_mcp_state_reads_tools_and_plugin_sources_off_the_harness() -> None:
    """*Prepare*: A harness snapshot routing a configured and a plugin server,
      under a global glob that disables one configured tool.
    *Do*: Project it into the session MCP state.
    *Assert*: The glob-disabled tools read disabled, including on a disabled
      server whose alias the harness normalizes, and the plugin server is kept
      with its own transport.
    """
    from mistralai_vibe_local_harness.vibe._mcp_models import (
        MCPAuthorizationRef,
        MCPRemoteToolDescriptor,
        MCPSourceState,
        MCPToolFilter,
        ResolvedMCPServerConfig,
    )
    from mistralai_vibe_local_harness.vibe._mcp_naming import build_route_snapshot
    from vibe.app_server._plugin_mcp import PluginMCPServerEntry, PluginMCPSource
    from vibe.app_server._unified_harness_backend_adapter import _session_mcp_state
    from vibe.core.config import MCPHttp, MCPStdio
    from vibe.core.plugins import PluginMCPServerDefinition

    # Prepare
    def resolved(name: str) -> ResolvedMCPServerConfig:
        return ResolvedMCPServerConfig(
            name=name,
            transport="stdio",
            command="fake-mcp",
            authorization=MCPAuthorizationRef(
                name, f"fingerprint-{name}", "none", "descriptor-1"
            ),
        )

    local_tools = (
        MCPRemoteToolDescriptor(remote_name="search"),
        MCPRemoteToolDescriptor(remote_name="write_file"),
    )
    plugin_tools = (MCPRemoteToolDescriptor(remote_name="lookup"),)
    idle_tools = (MCPRemoteToolDescriptor(remote_name="foo"),)
    snapshot = build_route_snapshot(
        catalog_revision="catalog-1",
        resolved=[
            (resolved("local"), local_tools),
            (resolved("records"), plugin_tools),
        ],
        sources=(
            MCPSourceState(name="local", status="enabled", descriptors=local_tools),
            MCPSourceState(
                name="records", status="connected", descriptors=plugin_tools
            ),
            MCPSourceState(name="my-server", status="disabled", descriptors=idle_tools),
        ),
        tool_filter=MCPToolFilter(disabled_globs=("local_write*", "my_server_*")),
    )
    config = build_test_vibe_config(
        mcp_servers=[
            MCPStdio(name="local", transport="stdio", command="fake-mcp"),
            MCPStdio(
                name="my-server", transport="stdio", command="fake-mcp", disabled=True
            ),
        ],
        disabled_tools=["local_write*", "my_server_*"],
    )
    server = MCPHttp(name="records", transport="http", url="https://plugin.test/mcp")
    plugin_source = PluginMCPSource(
        entry=PluginMCPServerEntry(
            definition=PluginMCPServerDefinition(
                plugin_name="productivity",
                plugin_namespace="productivity",
                source_id="records",
                private_alias="plugin_productivity_records",
                server=server,
                config_file=Path("/plugins/productivity/mcp.json"),
            ),
            server=server,
        ),
        status="connected",
    )
    plugin_mcp = SimpleNamespace(sources=lambda: (plugin_source,))

    # Do
    state = _session_mcp_state(
        snapshot, FakeConfigOrchestrator(config), cast(Any, plugin_mcp)
    )

    # Assert
    assert {
        source.name: (
            source.transport,
            {tool.remote_name: tool.enabled for tool in source.tools},
        )
        for source in state.sources
    } == {
        "local": ("stdio", {"search": True, "write_file": False}),
        "records": ("http", {"lookup": True}),
        "my-server": ("stdio", {"foo": False}),
    }
    idle = next(source for source in state.sources if source.name == "my-server")
    assert [tool.display_name for tool in idle.tools] == ["my_server_foo"]


@pytest.mark.asyncio
async def test_a_failed_connector_load_reaches_the_mcp_state(tmp_path: Path) -> None:
    """*Prepare*: A session whose deferred connector catalog load fails.
    *Do*: Run the background load.
    *Assert*: The failure is in the MCP runtime state and announced, rather than
      /mcp reading as if nothing were configured.
    """
    from mistralai_vibe_local_harness.vibe._connector_models import (
        empty_connector_snapshot,
    )
    from vibe.app_server.connector_catalog import ConnectorCatalogUnavailableError

    # Prepare
    session = _RecordingSession()
    cast(Any, session).read_connectors = AsyncMock(
        return_value=empty_connector_snapshot()
    )
    services = SimpleNamespace(notify=AsyncMock())
    runtime = build_runtime_snapshot(
        SessionOptions(),
        FakeConfigOrchestrator(build_test_vibe_config()),
        get_harness_files_manager(),
    )
    adapter = _inert_adapter(
        session, str(tmp_path), str(tmp_path), runtime=runtime, services=services
    )
    service = Mock()
    service.resolve_catalog = AsyncMock(
        side_effect=ConnectorCatalogUnavailableError("catalog is down")
    )

    # Do
    await adapter._resolve_connectors_background(service)

    # Assert
    assert adapter.runtime_updated_params().runtime.mcp.connector_error == (
        "catalog is down"
    )
    assert [call.args[0] for call in services.notify.await_args_list] == [
        "runtime/updated"
    ]


@pytest.mark.asyncio
async def test_a_failed_connector_apply_reaches_the_mcp_state(tmp_path: Path) -> None:
    """*Prepare*: A session whose deferred connector catalog resolves but fails to apply.
    *Do*: Run the background load.
    *Assert*: The failure is in the MCP runtime state and announced.
    """
    from mistralai_vibe_local_harness.vibe._connector_models import (
        empty_connector_snapshot,
    )

    # Prepare
    session = _RecordingSession()
    cast(Any, session).read_connectors = AsyncMock(
        return_value=empty_connector_snapshot()
    )
    services = SimpleNamespace(notify=AsyncMock())
    runtime = build_runtime_snapshot(
        SessionOptions(),
        FakeConfigOrchestrator(build_test_vibe_config()),
        get_harness_files_manager(),
    )
    adapter = _inert_adapter(
        session, str(tmp_path), str(tmp_path), runtime=runtime, services=services
    )
    cast(Any, adapter).reconfigure_connectors = AsyncMock(
        side_effect=SessionBackendError(ProtocolErrorCode.CONFLICT, "turn active")
    )
    service = Mock()
    service.resolve_catalog = AsyncMock(return_value=Mock())

    # Do
    await adapter._resolve_connectors_background(service)

    # Assert
    assert adapter.runtime_updated_params().runtime.mcp.connector_error == (
        "The connector catalog could not be applied"
    )
    assert [call.args[0] for call in services.notify.await_args_list] == [
        "runtime/updated"
    ]


def test_session_mcp_state_judges_tools_by_the_names_the_harness_routes() -> None:
    """*Prepare*: A disabled server with cached tools whose display name collides
      with a routed tool, under a global glob matching the routed name.
    *Do*: Project it into the session MCP state.
    *Assert*: The routed tool reads enabled; the disabled server's tool is still
      listed, disabled.
    """
    from mistralai_vibe_local_harness.vibe._mcp_models import (
        MCPAuthorizationRef,
        MCPRemoteToolDescriptor,
        MCPSourceState,
        MCPToolFilter,
        ResolvedMCPServerConfig,
    )
    from mistralai_vibe_local_harness.vibe._mcp_naming import build_route_snapshot
    from vibe.app_server._unified_harness_backend_adapter import _session_mcp_state
    from vibe.core.config import MCPStdio

    # Prepare
    docs_tools = (MCPRemoteToolDescriptor(remote_name="search_all"),)
    idle_tools = (MCPRemoteToolDescriptor(remote_name="all"),)
    snapshot = build_route_snapshot(
        catalog_revision="catalog-1",
        resolved=[
            (
                ResolvedMCPServerConfig(
                    name="docs",
                    transport="stdio",
                    command="fake-mcp",
                    authorization=MCPAuthorizationRef(
                        "docs", "fingerprint-docs", "none", "descriptor-1"
                    ),
                ),
                docs_tools,
            )
        ],
        sources=(
            MCPSourceState(name="docs", status="enabled", descriptors=docs_tools),
            MCPSourceState(
                name="docs_search", status="disabled", descriptors=idle_tools
            ),
        ),
        tool_filter=MCPToolFilter(enabled_globs=("docs_search_all",)),
    )
    config = build_test_vibe_config(
        mcp_servers=[
            MCPStdio(name="docs", transport="stdio", command="fake-mcp"),
            MCPStdio(
                name="docs_search", transport="stdio", command="fake-mcp", disabled=True
            ),
        ],
        enabled_tools=["docs_search_all"],
    )
    plugin_mcp = SimpleNamespace(sources=tuple)

    # Do
    state = _session_mcp_state(
        snapshot, FakeConfigOrchestrator(config), cast(Any, plugin_mcp)
    )

    # Assert
    assert {
        source.name: [(tool.display_name, tool.enabled) for tool in source.tools]
        for source in state.sources
    }["docs"] == [("docs_search_all", True)]
    assert [
        tool.enabled
        for source in state.sources
        if source.name == "docs_search"
        for tool in source.tools
    ] == [False]


class _RecordingRewindSnapshots(RewindFileSnapshots):
    """Snapshots that note every restore instead of touching the disk."""

    def __init__(self) -> None:
        super().__init__(cwd=lambda: None)
        self.restored: list[str] = []

    def restore(self, anchor: str) -> tuple[list[str], list[str]]:
        self.restored.append(anchor)
        return [], []


def _rewindable_adapter(tmp_path: Path, host: object) -> Any:
    """An adapter whose session holds one user message, ``entry-0``."""
    session = _RecordingSession()
    session.sent.append(SimpleNamespace(message=[TextContentBlock(text="first")]))
    adapter = _inert_adapter(session, str(tmp_path), str(tmp_path), host=host)
    adapter._rewind_snapshots = _RecordingRewindSnapshots()
    return adapter


@pytest.mark.asyncio
async def test_a_failed_in_place_rewind_leaves_the_files_alone(tmp_path: Path) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    host = Mock()
    host.rewind = AsyncMock(side_effect=RuntimeError("rewind failed"))
    adapter = _rewindable_adapter(tmp_path, host)

    with pytest.raises(RuntimeError, match="rewind failed"):
        await adapter.dispatch_extension(
            "session/rewind",
            {
                "sessionId": adapter.session_id,
                "entryId": "entry-0",
                "restoreFiles": True,
                "inplace": True,
            },
        )

    assert adapter._rewind_snapshots.restored == []


@pytest.mark.asyncio
async def test_a_failed_fork_rewind_leaves_the_files_alone(tmp_path: Path) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendHostAdapter,
    )

    harness = Mock()
    harness.fork = AsyncMock(side_effect=RuntimeError("fork failed"))
    host = UnifiedHarnessBackendHostAdapter(cast(Any, harness), AsyncMock())
    source = _rewindable_adapter(tmp_path, harness)
    source._context = replace(source._context, derive=lambda _settings: Mock())

    with pytest.raises(RuntimeError, match="fork failed"):
        await host.rewind_fork(
            source,
            SessionRewindParams(
                session_id=source.session_id, entry_id="entry-0", restore_files=True
            ),
        )

    assert source._rewind_snapshots.restored == []


@pytest.mark.asyncio
async def test_unified_session_start_fetches_the_model_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from unittest.mock import AsyncMock

    import vibe.app_server._unified_harness_backend_adapter as adapter_module

    config = build_test_vibe_config(experimental_enable_model_catalog=True)
    fetched = AsyncMock(return_value=None)
    monkeypatch.setattr(adapter_module, "fetch_and_cache_model_catalog", fetched)
    client, server = _connect_harness_host(config)

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        await client.request("session/start", SessionStartParams())
        deadline = time.monotonic() + 2.0
        while not fetched.called and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
    finally:
        await server.close()

    assert fetched.await_count >= 1


@pytest.mark.asyncio
async def test_unified_session_start_skips_the_fetch_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from unittest.mock import AsyncMock

    import vibe.app_server._unified_harness_backend_adapter as adapter_module

    client, server = _connect_harness_host()  # flag defaults to False
    fetched = AsyncMock(return_value=None)
    monkeypatch.setattr(adapter_module, "fetch_and_cache_model_catalog", fetched)

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        await client.request("session/start", SessionStartParams())
        await asyncio.sleep(0.05)
    finally:
        await server.close()

    assert fetched.await_count == 0


@pytest.mark.asyncio
async def test_clear_history_kicks_the_model_catalog_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The clear_history replacement skips _start_experiments (it reuses the
    # source's resolved manager), so the catalog fetch needs its own kick:
    # the source's in-flight fetch died with the source adapter.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from unittest.mock import AsyncMock

    import vibe.app_server._unified_harness_backend_adapter as adapter_module

    fetched = AsyncMock(return_value=None)
    monkeypatch.setattr(adapter_module, "fetch_and_cache_model_catalog", fetched)
    host = adapter_module.adapt_harness_host(
        pytest.importorskip("mistralai_vibe_local_harness.vibe").create_harness_host(),
        _test_session_runtime_builder(
            build_test_vibe_config(experimental_enable_model_catalog=True)
        ),
    )

    started = await host.start(SessionStartParams())
    await asyncio.sleep(0)
    first_count = fetched.await_count
    assert first_count >= 1

    await host.clear_history(
        started.backend,
        SessionHistoryClearParams(session_id=started.backend.session_id),
    )
    await asyncio.sleep(0)

    assert fetched.await_count > first_count


@pytest.mark.asyncio
async def test_clear_history_keeps_an_in_memory_catalog_over_a_worse_disk_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The optimistic disk apply must fill, never replace: a catalog fetched
    # this run survives /clear even when the disk entry is missing or worse.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from unittest.mock import AsyncMock, Mock

    import vibe.app_server._unified_harness_backend_adapter as adapter_module
    from vibe.core.model_catalog import parse_model_catalog_response

    fetched_catalog = parse_model_catalog_response({
        "models": [{"id": "mistral-medium-3.5", "label": "Mistral Medium 3.5"}]
    })
    assert fetched_catalog is not None
    # First fetch delivers the catalog; later fetches return None so any
    # preservation observed must come from the holder, not a re-fetch.
    fetch_results = iter([fetched_catalog, None])
    fetched = AsyncMock(side_effect=lambda _config: next(fetch_results))
    monkeypatch.setattr(adapter_module, "fetch_and_cache_model_catalog", fetched)
    monkeypatch.setattr(
        adapter_module, "load_cached_model_catalog", Mock(return_value=None)
    )
    host = adapter_module.adapt_harness_host(
        pytest.importorskip("mistralai_vibe_local_harness.vibe").create_harness_host(),
        _test_session_runtime_builder(
            build_test_vibe_config(experimental_enable_model_catalog=True)
        ),
    )

    started = await host.start(SessionStartParams())
    backend = cast(adapter_module.UnifiedHarnessBackendAdapter, started.backend)
    holder = backend._context.model_catalog
    deadline = time.monotonic() + 2.0
    while holder.value is None and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert holder.value is fetched_catalog

    await host.clear_history(
        started.backend,
        SessionHistoryClearParams(session_id=started.backend.session_id),
    )
    await asyncio.sleep(0.05)

    assert holder.value is fetched_catalog
