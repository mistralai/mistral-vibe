from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
import contextlib
import logging
from pathlib import Path
import re
import time
import tomllib
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, cast

import pytest
import tomli_w

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.stubs.app_server import build_test_app_server
from tests.stubs.fake_account_gateway import FakeAccountGateway
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server import _runtime as runtime_module
from vibe.app_server._account import WhoAmIResult
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
    SessionBackendHost,
    SessionBackendRuntimeView,
    SessionConnectorSourceState,
    SessionConnectorState,
)
from vibe.app_server._unified_scheduled_loops import ScheduledLoopStoreError
from vibe.app_server.client import AppServerClient
from vibe.app_server.events import HistoryEntryAdded
from vibe.app_server.models import (
    AccountPlanKind,
    CompletedEffectState,
    ConnectorCounts,
    FailedEffectState,
    GenericEffectDetail,
    IdleSessionStatus,
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
    PublicCallbackEntry,
    PublicCheckpointEntry,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicMessageEntry,
    PublicSession,
    PublicSessionState,
    PublicTurn,
    PublicTurnStatus,
    ResourceContentBlock,
    ShellEffectDetail,
    ShellEffectOutput,
    TextContentBlock,
    TokenUsage,
    TurnErrorCode,
    validate_history_entry,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
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
    PageRequest,
    PluginInfoParams,
    PluginInfoResponse,
    ProtocolErrorCode,
    RuntimeReadParams,
    RuntimeReadResponse,
    RuntimeSnapshot,
    ServerWarningParams,
    SessionContinueParams,
    SessionDeleteParams,
    SessionForkParams,
    SessionHistoryClearParams,
    SessionHistoryClearResponse,
    SessionKind,
    SessionListParams,
    SessionListResponse,
    SessionOptions,
    SessionReadParams,
    SessionReadResponse,
    SessionResumeParams,
    SessionStartParams,
    SessionTextContentBlock,
    SkillsListParams,
    SkillsListResponse,
    TurnContextInputEntry,
    TurnEnqueueParams,
    TurnEnqueueResponse,
    TurnInterruptParams,
    TurnQueueReadParams,
    TurnQueueReplaceParams,
    TurnQueueReplaceResponse,
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
from vibe.core.config import MCPStdio, SessionLoggingConfig, VibeConfigSchema
from vibe.core.config.admin_config import AdminConfigApplyResult, AdminConfigOutcome
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.experiments.active import ExperimentSurface
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
from vibe.core.tools.mcp.registry import MCPRegistry
from vibe.core.tools.models import ToolPermission
from vibe.core.types import LLMMessage, Role, ScheduledLoop
from vibe.user_content import UserDisplayContent, UserTextResource

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        JsonObject,
    )

    # Imported for typing only: the module pulls in the optional Harness
    # extra, and these tests skip rather than fail when it is absent.
    from vibe.app_server._unified_harness_backend_adapter import SessionContextBuilder

_SESSION_CREATED = re.compile(
    r"^Session created: harness=(?P<harness>\w+) session_id=(?P<session_id>\S+)$"
)


class _RecordingSession:
    """Stands in for the Harness session, recording every pushed configuration."""

    session_id = "session-1"
    parent_session_id: str | None = None
    active_turn_id: str | None = None

    def __init__(self) -> None:
        self.active_model: str | None = None
        self.applied: list[object] = []
        self.settings: list[object] = []
        self.capabilities: list[object] = []
        self.plugins: list[tuple[object, ...] | None] = []
        self.sent: list[Any] = []

    async def persist_active_model(self, active_model: str) -> bool:
        if self.active_model == active_model:
            return False
        self.active_model = active_model
        return True

    def apply_adapter_config(self, adapter_config: object) -> None:
        self.applied.append(adapter_config)

    async def apply_runtime_configuration(
        self,
        settings: object,
        adapter_config: object,
        capabilities: object,
        *,
        plugins: tuple[object, ...] | None = None,
    ) -> None:
        # The real Harness rejects this mid-turn: Core reads its settings when
        # the turn starts and reconfigures them through its own command queue.
        # A double that accepted it anyway would let a caller that has to stay
        # off this path mid-turn pass here and fail in front of a user.
        from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
            HarnessTurnConflictError,
        )

        if self.active_turn_id is not None:
            raise HarnessTurnConflictError(self.active_turn_id)
        self.settings.append(settings)
        self.applied.append(adapter_config)
        self.capabilities.append(capabilities)
        self.plugins.append(plugins)

    async def apply_capabilities(
        self, capabilities: object, *, plugins: tuple[object, ...] | None = None
    ) -> None:
        self.capabilities.append(capabilities)
        self.plugins.append(plugins)

    async def start_turn(self, params: Any) -> Any:
        self.sent.append(params)
        turn = SimpleNamespace(id="turn-1", session_id=self.session_id, started_at=0)
        return SimpleNamespace(response=SimpleNamespace(turn=turn), after_response=None)

    async def enqueue_turn(self, params: Any) -> Any:
        from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
            TurnEnqueueResponse as HarnessTurnEnqueueResponse,
        )

        self.sent.append(params)
        return SimpleNamespace(
            response=HarnessTurnEnqueueResponse(queue_item_id="queue-1"),
            after_response=None,
        )

    async def replace_queued_turn(self, params: Any) -> Any:
        from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
            TurnQueueReplaceResponse as HarnessTurnQueueReplaceResponse,
        )

        self.sent.append(params)
        return SimpleNamespace(
            response=HarnessTurnQueueReplaceResponse(
                queue_item_id=params.queue_item_id
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
        from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
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
            }
            for index, blocks in enumerate(self._sent_blocks())
        ]
        return SimpleNamespace(
            snapshot=HarnessSessionSnapshot(
                state=HarnessPublicSessionState(
                    session=HarnessPublicSession(
                        id=self.session_id,
                        status=IdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                    ),
                    history=LatestPublicHistoryPage(entries=entries),
                    turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
                ),
                history_limit=len(entries),
                watermark=0,
            )
        )

    def _sent_blocks(self) -> list[list[Any]]:
        sent = (
            getattr(params, "message", None) or getattr(params, "input", None)
            for params in self.sent
        )
        return [blocks for blocks in sent if blocks]


def _admin_result(
    outcome: AdminConfigOutcome, *, error: str | None = None
) -> AdminConfigApplyResult:
    return AdminConfigApplyResult(outcome, error=error)


def _stub_core_config() -> Any:
    """A real Core config for a stub derivation.

    ``_apply_derivation`` reads ``core_config.capabilities`` to push the skill
    catalogue, so a ``None`` here would only ever prove the stub is a stub.
    """
    from mistralai_vibe_local_harness.vibe._host import (  # pyright: ignore[reportMissingImports]
        _core_config,
    )

    return _core_config("session-1")


def _stub_adapter_config() -> Any:
    """A real adapter config for a stub derivation.

    The adapter reads ``adapter_config.skills`` to resolve ``/skill-name``, so
    a placeholder here fails on attribute access rather than on anything the
    test is about.
    """
    from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
        LocalRuntimeAdapterConfig,
    )

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


def _inert_adapter(
    session: object,
    cwd: str | None,
    storage_root: str,
    *,
    runtime: object | None = None,
    host: object | None = None,
    telemetry_client: TelemetryClient | None = None,
    permissions: object | None = None,
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
    )
    return UnifiedHarnessBackendAdapter(
        cast(Any, session),
        cwd,
        context,
        derivation,
        host=cast(Any, host),
        telemetry_client=telemetry_client,
    )


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


def test_unified_connector_state_flattens_remote_tool_descriptions() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
        ConnectorRouteSnapshot,
        ConnectorSourceState,
    )
    from mistralai_vibe_local_harness.vibe._connector_models import (  # pyright: ignore[reportMissingImports]
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
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
    expect_title_model: bool,
) -> None:
    """Only cli/desktop clients get a background title model, matching legacy."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

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
async def test_unified_runtime_enables_large_output_offloading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A Unified Harness session context for a local CLI workspace.
    *Do*: Derive the Core configuration used to start the session.
    *Assert*: Large outputs use the same filesystem thresholds as Vibe Work.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustFilesystemLargeOutputPolicy,
    )

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


def test_unified_mcp_projection_update_preserves_connector_sources() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    class FakeSession:
        session_id = "session-1"

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


@pytest.mark.asyncio
async def test_legacy_session_start_records_the_python_harness(
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

    assert recorded == [("python", session.session_id)]


@pytest.mark.asyncio
async def test_unified_harness_session_start_records_the_rust_harness(
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
    assert recorded == [("rust", started.state.session.id)]
    assert started.state.history == []
    assert started.state.session.cwd is not None


@pytest.mark.asyncio
async def test_unified_adapter_tracks_open_callbacks_for_delivery_lifecycle(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._session import (  # pyright: ignore[reportMissingImports]
        _approval_callback,
    )

    class FakeSession:
        session_id = "session-1"
        rejected: object | None = None

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
        required_permissions=(),
    )

    events = adapter._callback_events({
        "type": "callback_requested",
        "callback": callback,
    })

    assert events is not None
    assert isinstance(adapter.open_callbacks()[0], PublicCallbackEntry)
    assert adapter.open_callbacks()[0].callback_id == "approval-call-1"
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
async def test_unified_adapter_routes_child_callback_through_the_local_host(
    tmp_path: Path,
) -> None:
    """*Prepare*: A child callback is registered under an attached Unified root.
    *Do*: Reject delivery through the root adapter.
    *Assert*: The Host receives the result addressed to the child Session.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._session import (  # pyright: ignore[reportMissingImports]
        _approval_callback,
    )

    callback = _approval_callback(
        session_id="session-child",
        callback_id="approval-child-call",
        action=_approval_action("child-turn-1"),
        created_at=1,
        required_permissions=(),
    )

    class FakeSession:
        session_id = "session-root"

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
    from mistralai_vibe_local_harness.vibe._session import (  # pyright: ignore[reportMissingImports]
        _approval_callback,
    )

    from vibe.app_server.protocol import CallbackResult, CallbackResultParams

    # Prepare
    class FakeSession:
        session_id = "session-1"

        async def respond_to_callback(self, _params: object) -> object:
            return SimpleNamespace(response={"last_event_id": 1}, after_response=None)

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
    from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustIdleTurn,
        RustImageContentBlock,
        RustNoNextAction,
        RustSessionTransition,
        RustTextContentBlock,
        RustTurnStartedObservation,
    )
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        TURN_QUEUE_MAX_ITEMS,
        HistoryCursor,
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._projection import (  # pyright: ignore[reportMissingImports]
        SessionProjector,
    )
    from mistralai_vibe_local_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
        ProjectionStateV1,
    )

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
    from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustIdleTurn,
        RustNoNextAction,
        RustProtocolError,
        RustSessionTransition,
        RustTextContentBlock,
        RustToolFailureResult,
        RustToolResultCommittedObservation,
        RustToolSuccessResult,
    )
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        TURN_QUEUE_MAX_ITEMS,
        HistoryCursor,
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._projection import (  # pyright: ignore[reportMissingImports]
        SessionProjector,
    )
    from mistralai_vibe_local_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
        ProjectionStateV1,
    )

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
    from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustIdleTurn,
        RustNoNextAction,
        RustSessionTransition,
        RustToolResultCommittedObservation,
        RustToolSuccessResult,
    )
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        TURN_QUEUE_MAX_ITEMS,
        HistoryCursor,
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._projection import (  # pyright: ignore[reportMissingImports]
        SessionProjector,
    )
    from mistralai_vibe_local_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
        ProjectionStateV1,
    )

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

    effect = _project_history_entry(raw)

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
    from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustCompletedTurn,
        RustNoNextAction,
        RustSessionTransition,
        RustToolDiscoveryFinishedObservation,
        RustToolDiscoverySummary,
    )
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        HistoryCursor,
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._projection import (  # pyright: ignore[reportMissingImports]
        SessionProjector,
    )
    from mistralai_vibe_local_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
        ProjectionStateV1,
    )

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

    effect = _project_history_entry(raw)

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
    from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
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


def test_unified_turn_error_maps_internal_provider_code_to_public_backend_error():
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        FailedPublicTurn as HarnessFailedPublicTurn,
        PublicError as HarnessPublicError,
    )

    from vibe.app_server._unified_harness_backend_adapter import _public_turn

    turn = _public_turn(
        HarnessFailedPublicTurn(
            id="turn-1",
            session_id="session-1",
            started_at=1,
            completed_at=2,
            error=HarnessPublicError(
                code="model_stream_failed",
                message="provider rejected the request",
                details={"requestId": "req-1"},
            ),
        )
    )

    assert turn.error is not None
    assert turn.error.code == TurnErrorCode.BACKEND_ERROR
    assert turn.error.message == "provider rejected the request"
    assert turn.error.details == {"requestId": "req-1"}


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
    from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
        HarnessStaleTurnError,
    )

    from vibe.app_server._unified_harness_backend_adapter import _harness_call

    async def fail() -> None:
        raise HarnessStaleTurnError(active_turn_id)

    with pytest.raises(SessionBackendError) as exc_info:
        await _harness_call(fail())

    assert exc_info.value.code is expected_code
    assert str(exc_info.value) == expected_message


@pytest.mark.parametrize(
    ("auto_approve", "edit_mode", "shell_mode"),
    [(False, "ask", "ask"), (True, "allow", "allow")],
)
@pytest.mark.asyncio
async def test_unified_runtime_config_gates_editing_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    auto_approve: bool,
    edit_mode: str,
    shell_mode: str,
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
    assert tool_modes["process.start"] == shell_mode
    assert tool_modes["process.output"] == "allow"
    assert tool_modes["process.write"] == "allow"
    assert tool_modes["process.list"] == "allow"
    assert tool_modes["process.stop"] == "allow"
    assert derivation.core_config.settings.tools.background_processes.mode == "enabled"
    assert derivation.adapter_config.process_authority == "host_shell"
    assert derivation.adapter_config.command_environment == (
        runtime_module._command_environment_mode()
    )
    assert derivation.runtime.bypass_tool_permissions is auto_approve


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
    assert derivation.adapter_config.active_model.model == active_model.name
    assert (
        derivation.adapter_config.active_model.temperature == active_model.temperature
    )
    assert derivation.adapter_config.active_model.thinking == active_model.thinking
    assert derivation.adapter_config.compaction_model.model == "compact-model"
    assert derivation.adapter_config.compaction_model.temperature == 0.7
    assert derivation.adapter_config.compaction_model.thinking == "medium"


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
        cast(Any, session),
        str(tmp_path),
        context,
        context.derive(UnifiedSessionSettings()),
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
        cast(Any, session),
        str(tmp_path),
        context,
        context.derive(UnifiedSessionSettings()),
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


def _write_workspace_skill(root: Path, name: str, body: str) -> Path:
    path = root / ".vibe" / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: Reviews a diff.\n---\n\n{body}\n",
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
        cast(Any, session),
        str(tmp_path),
        context,
        context.derive(UnifiedSessionSettings()),
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
        cast(Any, session),
        str(tmp_path),
        context,
        context.derive(UnifiedSessionSettings()),
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

    assert [(source.name, source.kind) for source in runtime.mcp.sources] == [
        ("local", MCPSourceKind.SERVER),
        ("github", MCPSourceKind.CONNECTOR),
    ]
    assert runtime.connectors == ConnectorCounts(connected=1, total=1)


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
        cast(Any, session),
        str(tmp_path),
        context,
        context.derive(UnifiedSessionSettings()),
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


async def _skill_adapter(tmp_path: Path, session: _RecordingSession) -> Any:
    """An adapter over a workspace holding one user-invocable skill."""
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )

    _write_workspace_skill(tmp_path, "code-review", "Read the diff twice.")
    process = HarnessProcess(experimental_harness=True)
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path), trust_workspace=True)
    )
    return UnifiedHarnessBackendAdapter(
        cast(Any, session),
        str(tmp_path),
        context,
        context.derive(UnifiedSessionSettings()),
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

    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id,
            message=[TextContentBlock(text="/code-review please")],
        )
    )

    blocks = session.sent[-1].message
    # Appended, not prepended: the legacy loop emits the user message first and
    # only then the skill result, and the model sees the same order here.
    assert blocks[0].text == "/code-review please"
    assert "Read the diff twice." in blocks[-1].text
    assert '<skill_content name="code-review">' in blocks[-1].text


@pytest.mark.asyncio
async def test_unified_enqueue_turn_appends_skill_body_after_mentioned_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A previously loaded skill whose body mentions another file.
    *Do*: Enqueue the skill invocation with a user-authored file mention.
    *Assert*: The user file and full unexpanded skill body are stored in order.
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

    Assert the catalogue is empty. Core would otherwise put the skill in the
    prompt and the model would have no tool to load it with.
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
    assert "code-review" in derivation.adapter_config.skills


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


async def _unified_adapter_with_real_context(
    tmp_path: Path, session: object, *, auto_approve: bool = False
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
        cast(Any, session), str(tmp_path), context, derivation
    )
    return adapter, derivation


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

    await adapter.switch_agent(
        AgentSwitchParams(
            session_id=_RecordingSession.session_id, agent_name="auto-approve"
        )
    )

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

    Deriving the modes from the catalogue alone left ``write_file`` on ``ask``:
    the model could offer an edit and the user could approve it, in the one mode
    whose whole promise is that it cannot touch the workspace.

    ``deny`` is the only verdict a mode settles on its own. Everything a profile
    permits is left at ``ask`` for the resolver to decide per call, which is why
    accept-edits' editing tools read as ``ask`` here and still edit unprompted.
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

    await adapter.switch_agent(
        AgentSwitchParams(
            session_id=_RecordingSession.session_id, agent_name="accept-edits"
        )
    )
    editing = cast(Any, session.applied[-1]).tool_modes

    assert planning["file_system.write_file"] == "deny"
    assert planning["file_system.search_replace"] == "deny"
    assert planning["file_system.read_file"] == "ask"
    assert editing["file_system.write_file"] == "ask"
    assert editing["file_system.search_replace"] == "ask"
    assert editing["file_system.bash"] == "ask"


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


def _always(_tool_name: str) -> ToolPermission:
    return ToolPermission.ALWAYS


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
    offered to edit files in a mode that advertises itself as read-only.

    ``always`` stops at ``ask`` rather than ``allow``: the mode is the only gate
    the Runtime checks, and ``allow`` retires the resolver along with every rule
    the tool applies to the call itself. What ``always`` means is settled one
    call at a time, by the resolver, which clears the ordinary ones without a
    prompt -- see ``test_unified_permissions``.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import _rust_tool_modes

    modes = _rust_tool_modes(
        {"write_file", "read_file"},
        lambda name: (
            ToolPermission(permission)
            if name == "write_file"
            else ToolPermission.ALWAYS
        ),
        bypass_approval=False,
    )

    assert modes["file_system.write_file"] == expected_mode
    assert modes["file_system.read_file"] == "ask"


def test_unified_builtin_modes_keep_background_starts_off_the_resolver() -> None:
    """*Prepare*: A catalogue whose shell is configured ``always``.
    *Do*: Map it with no bypass.
    *Assert*: The shell builtin asks and ``process.start`` still allows. No Vibe
    tool stands behind ``process.start``, so routing it through the resolver
    would raise a prompt carrying nothing to scope -- one ``grant`` cannot
    record, and would therefore ask again on every background start.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import _rust_tool_modes

    modes = _rust_tool_modes({"bash", "read_file"}, _always, bypass_approval=False)

    assert modes["file_system.bash"] == "ask"
    assert modes["process.start"] == "allow"


def test_unified_builtin_bypass_wins_over_a_never_permission() -> None:
    """``AgentLoop._should_execute_tool`` short-circuits on the bypass before it
    ever reads a permission, so ``--auto-approve`` has to outrank a ``never`` here
    too -- otherwise the two backends disagree about the same config.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import _rust_tool_modes

    modes = _rust_tool_modes(
        {"write_file"}, lambda _name: ToolPermission.NEVER, bypass_approval=True
    )

    assert modes["file_system.write_file"] == "allow"


def test_unified_shell_builtin_takes_the_strictest_permission_it_stands_for() -> None:
    """One Rust builtin covers three shell names; the mode has to hold for each."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import _rust_tool_modes

    permissions = {"bash": ToolPermission.ALWAYS, "powershell": ToolPermission.NEVER}

    modes = _rust_tool_modes(
        {"bash", "powershell"}, permissions.__getitem__, bypass_approval=False
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
    from vibe.app_server._runtime import _rust_tool_modes

    available = set(shell_tool.split("_and_")) | {"read_file"}

    modes = _rust_tool_modes(available, _always, bypass_approval=True)

    assert modes["file_system.bash"] == "allow"
    assert modes["process.start"] == "allow"


def test_unified_shell_builtin_is_denied_when_no_shell_tool_is_available() -> None:
    """Disabling the shell in the layered config still has to stop the Runtime."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import _rust_tool_modes

    modes = _rust_tool_modes({"read_file"}, _always, bypass_approval=True)

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

    assert ceiling["file_system.read_file"] == "ask"
    assert ceiling["file_system.bash"] == "ask"
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

    assert ceiling["file_system.read_file"] == "ask"
    assert ceiling["file_system.write_file"] == "deny"


def test_agent_ceiling_stops_a_declared_always_at_ask() -> None:
    # ``allow`` would retire Vibe's resolver, and installing a plugin is not a way to
    # retire it.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import rust_agent_tool_ceiling

    ceiling = rust_agent_tool_ceiling(
        {"read_file"},
        lambda _name: ToolPermission.NEVER,
        {"tools": {"read_file": {"permission": "always"}}},
    )

    assert ceiling["file_system.read_file"] == "ask"


def test_agent_ceiling_declared_never_denies_a_tool_the_session_allows() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import rust_agent_tool_ceiling

    ceiling = rust_agent_tool_ceiling(
        {"read_file", "write_file"},
        _always,
        {"tools": {"write_file": {"permission": "never"}}},
    )

    assert ceiling["file_system.read_file"] == "ask"
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

    assert ceiling["file_system.read_file"] == "ask"


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
    assert ceiling["file_system.read_file"] == "ask"


def test_agent_ceiling_without_overrides_matches_the_session_catalogue() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import _rust_tool_modes, rust_agent_tool_ceiling

    available = {"read_file", "write_file", "bash"}

    assert rust_agent_tool_ceiling(available, _always, {}) == _rust_tool_modes(
        available, _always, bypass_approval=False
    )


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
    system_prompt_id: str, expected_phrases: tuple[str, ...]
) -> None:
    """*Prepare*: Vibe configuration contains a system-prompt experiment variant.
    *Do*: Resolve Unified system instructions through the Vibe composition seam.
    *Assert*: The SDK-owned instructions include that variant's product guidance.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    config = build_test_vibe_config(system_prompt_id=system_prompt_id)

    # Do
    instructions = runtime_module._build_unified_system_instructions(config)

    # Assert
    assert all(phrase in instructions for phrase in expected_phrases)


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
        text: str, *, base_dir: Path
    ) -> list[ResourceContentBlock]:
        calls.append((text, base_dir))
        return [mentioned_block]

    monkeypatch.setattr(
        adapter_module,
        "mentioned_file_content_blocks_async",
        fake_mentioned_file_blocks,
    )
    adapter = _inert_adapter(
        SimpleNamespace(session_id="session-1", ephemeral=True),
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
async def test_unified_flush_events_does_not_wait_before_event_stream_starts(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        IdleSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._session import (  # pyright: ignore[reportMissingImports]
        HarnessSessionSubscription,
    )

    class FakeHarnessSession:
        session_id = "session-1"

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
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        Event as HarnessEvent,
        IdleSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
        TurnQueueUpdatedEvent as HarnessTurnQueueUpdatedEvent,
    )
    from mistralai_vibe_local_harness.vibe._session import (  # pyright: ignore[reportMissingImports]
        HarnessSessionSubscription,
    )

    class FakeHarnessSession:
        session_id = "session-1"

        def __init__(self) -> None:
            self.events_started = asyncio.Event()
            self.release_events = asyncio.Event()
            self.turn_queue = HarnessTurnQueue(items=[], paused=False, max_items=32)

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
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
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
    from mistralai_vibe_local_harness.vibe._session import (  # pyright: ignore[reportMissingImports]
        HarnessSessionSubscription,
    )

    class Result:
        def __init__(self, response: object) -> None:
            self.response = response
            self.after_response = None

    class FakeHarnessSession:
        session_id = "session-1"

        def __init__(self) -> None:
            self.active_model: str | None = None
            self.received: object | None = None
            self.replaced: Any | None = None
            self.turn_queue = HarnessTurnQueue(items=[], paused=False, max_items=32)

        async def persist_active_model(self, active_model: str) -> bool:
            if self.active_model == active_model:
                return False
            self.active_model = active_model
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
    assert event.method == "turn_queue_updated"
    assert cast(Any, event.params).queue == read.response.queue
    await cast(Any, subscription.events).aclose()


def test_unified_state_updates_preserve_the_subscription_history_window(
    tmp_path: Path,
) -> None:
    """A full-state update must not replay history omitted from the subscription."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
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
        SimpleNamespace(session_id="session-root"),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
    )
    previous = adapter._read_response(
        HarnessSessionSnapshot(
            state=state(entries[-2:], 1), history_limit=2, watermark=1
        )
    ).state

    replayed, current, _ = adapter._state_update_events(
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

    added, current, _ = adapter._state_update_events(
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
async def test_unified_root_subscription_translates_child_session_events(
    tmp_path: Path,
) -> None:
    """*Prepare*: A root Harness subscription registers a child before its first update.
    *Do*: Consume the child update through the root adapter event stream.
    *Assert*: The emitted App Server notification retains the child Session identity.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        IdleSessionStatus,
        InProgressPublicTurn,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        RunningSessionStatus,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._session import (  # pyright: ignore[reportMissingImports]
        HarnessSessionSubscription,
    )

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
    event = await anext(subscription.events)

    # Assert
    assert event.session_id == child_id
    assert event.params is not None
    assert event.params.model_dump(by_alias=True)["sessionId"] == child_id
    await cast(Any, subscription.events).aclose()


def test_unified_subagent_analytics_emits_one_content_free_terminal_event(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    """*Prepare*: A Unified wait effect transitions from running to a timed-out result.
    *Do*: Reconcile the terminal Harness snapshot twice through the Vibe adapter.
    *Assert*: One existing tool-finished event exposes only approved dimensions.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
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
        SimpleNamespace(session_id="session-root"),
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
    _, current, _ = adapter._state_update_events(event, previous, 20)
    adapter._state_update_events({**event, "eventId": 2}, current, 20)

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
    """*Prepare*: The runtime buffers three completions (first turn, follow-up, compaction).
    *Do*: Drain the buffer through the adapter twice.
    *Assert*: One ``vibe.request_sent`` per payload, call-type mapped, then empty.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
        RequestSentTelemetry,
    )

    telemetry = TelemetryClient(
        config_getter=lambda: build_test_vibe_config(enable_telemetry=True),
        harness_backend=ExperimentSurface.UNIFIED,
    )
    adapter = _inert_adapter(
        SimpleNamespace(session_id="session-root"),
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

    adapter._forward_request_sent()

    events = [e for e in telemetry_events if e["event_name"] == "vibe.request_sent"]
    assert [e["properties"]["call_type"] for e in events] == [
        "main_call",
        "secondary_call",
        "secondary_call",
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
        == 3
    )


def _tool_effect(
    effect_id: str, tool_name: str, status: str, tool_input: JsonObject | None = None
) -> JsonObject:
    state: JsonObject = {"status": status}
    if status == "failed":
        state["error"] = {"message": "boom", "code": "tool_failed"}
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
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
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
        SimpleNamespace(session_id="session-root"),
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
    assert props["harness_backend"] == "unified"
    # No harness background-process flag, so the field is never set.
    assert "bash_background" not in props


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
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
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
        SimpleNamespace(session_id="session-root"),
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
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
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
        SimpleNamespace(session_id="session-root"),
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
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
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
        SimpleNamespace(session_id="session-root"),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
        telemetry_client=telemetry,
    )
    adapter._context_tokens_before = 4321

    adapter._updated_state(_event([], 1), 20)
    # The usage-less snapshot must not wipe the last reading.
    assert adapter._context_tokens_before == 4321

    adapter._updated_state(
        _event([_compaction_checkpoint("auto-ret", "automatic", succeeded=True)], 2), 20
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
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        IdleSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._session import (  # pyright: ignore[reportMissingImports]
        HarnessSessionSubscription,
    )

    stream_open = asyncio.Event()

    class FakeHarnessSession:
        session_id = "session-1"

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
async def test_unified_harness_forks_an_unused_live_session() -> None:
    host = _harness_backend_host()
    started = await host.start(SessionStartParams())

    forked = await host.fork(
        SessionForkParams(source_session_id=started.backend.session_id, attach=True)
    )
    await host.shutdown()

    assert forked.backend is not None
    assert forked.backend.session_id != started.backend.session_id
    assert forked.response.source_session_id == started.backend.session_id
    assert forked.response.state.session.parent_session_id == started.backend.session_id
    assert forked.response.state.session.root_session_id == started.backend.session_id


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
    assert await cast(Any, forked.backend).scheduled_loops() == [loop]
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
async def test_unified_schedule_write_failure_backs_off_before_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A fired loop whose next schedule cannot be persisted.
    *Do*: Record the scheduler's fallback wait.
    *Assert*: It waits one loop interval rather than retrying on the next tick.
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
        id="unpersisted-loop",
        interval_seconds=30,
        prompt="already ran",
        next_fire_at=0,
        created_at=0,
    )
    sleeps: list[float] = []

    async def fail_mark_fired(_loop_id: str) -> None:
        raise ScheduledLoopStoreError("disk unavailable")

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(adapter._scheduled_loops, "mark_fired", fail_mark_fired)
    monkeypatch.setattr(asyncio, "sleep", record_sleep)

    # Do
    await adapter._mark_scheduled_loop_attempted(loop)

    # Assert
    assert sleeps == [30]
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

    assert host.harness_kind == "rust"
    assert first.backend.session_id != second.backend.session_id


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
    from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
        ForeignHookDefinition,
        compile_foreign_hooks,
    )
    from mistralai_vibe_local_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
        UnifiedSessionStore,
    )

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
    # The count stands for hooks that actually bound, not files that parsed.
    assert len(context.hooks.bindings) == 2


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
    from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
        compile_foreign_hooks,
    )

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
    from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
        compile_foreign_hooks,
    )

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
async def test_unified_harness_resume_and_list_use_the_persisted_store() -> None:
    first_host = _harness_backend_host()
    started = await first_host.start(SessionStartParams())
    session_id = started.backend.session_id
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await first_host.shutdown()

    second_host = _harness_backend_host()
    resumed = await second_host.resume(SessionResumeParams(session_id=session_id))
    listed = await second_host.list(SessionListParams())
    await second_host.shutdown()

    assert resumed.backend.session_id == session_id
    assert isinstance(resumed.backend, SessionBackendRuntimeView)
    assert resumed.backend.runtime_updated_params().session_id == session_id
    assert [session.id for session in listed.items] == [session_id]
    assert listed.continue_session_id == session_id


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
    assert not schedule_path.exists()


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
    from mistralai_vibe_local_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
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
    from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        SessionStartParams as HarnessSessionStartParams,
    )

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    unified = vibe_runtime.UnifiedHarnessSessionBackendHost(tmp_path)
    started = await unified.start(HarnessSessionStartParams(history_limit=10))
    session_id = started.session_id
    active_model = config.get_active_model().alias
    await started.persist_active_model(active_model)
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
    await root.backend.start_turn(
        TurnStartParams(
            session_id=root.backend.session_id,
            message=[TextContentBlock(text="persist root")],
        )
    )
    await other.backend.start_turn(
        TurnStartParams(
            session_id=other.backend.session_id,
            message=[TextContentBlock(text="persist other")],
        )
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


def test_legacy_and_unified_hosts_share_the_same_lease_namespace(
    tmp_path: Path,
) -> None:
    vibe_runtime = pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
        SessionLease as HarnessLease,
    )

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

        from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
            CompiledHooks,
            LegacyImportSource,
            LegacySessionReference as HarnessLegacySessionReference,
            LocalRuntimeAdapterConfig,
        )
        from mistralai_vibe_local_harness.vibe._host import (  # pyright: ignore[reportMissingImports]
            _core_config,
        )

        from vibe.app_server._plugins import (
            UnifiedPluginProvider,
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
                adapter_config=LocalRuntimeAdapterConfig(),
            )

        plugins = await resolve_session_plugins(harness_files)
        return UnifiedSessionContext(
            storage_root=orchestrator.config.session_logging.save_dir,
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
                config_orchestrator=orchestrator,
                harness_files=harness_files,
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
        UnifiedRuntimeDerivation,
        UnifiedSessionContext,
    )
    from vibe.app_server.protocol import ConfigReloadParams

    class CountingOrchestrator(FakeConfigOrchestrator[VibeConfigSchema]):
        reloads = 0

        async def reload(self, **_kwargs: object) -> None:
            type(self).reloads += 1

    # Isolated so the assertion measures ``reload_config`` itself: a successful
    # admin fetch reloads the orchestrator as a side effect.
    async def no_admin_refresh(_orchestrator: object) -> object:
        return _admin_result(AdminConfigOutcome.DISABLED)

    monkeypatch.setattr(adapter_module, "refresh_admin_layer", no_admin_refresh)

    orchestrator = CountingOrchestrator(build_test_vibe_config())
    harness_files = get_harness_files_manager()
    agents = AgentManager(
        orchestrator, orchestrator.config.default_agent, harness_files=harness_files
    )
    derivation = UnifiedRuntimeDerivation(
        runtime=build_unified_runtime_snapshot(orchestrator, agents),
        core_config=_stub_core_config(),
        adapter_config=_stub_adapter_config(),
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
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), str(tmp_path), context, derivation
    )

    await adapter.reload_config(
        ConfigReloadParams(
            session_id=_RecordingSession.session_id, reload_runtime=reload_runtime
        )
    )

    assert CountingOrchestrator.reloads == 1
    assert session.applied == [derivation.adapter_config]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        ("FETCH_FAILED", ["Admin-managed config not applied outcome=fetch_failed"]),
        ("PARSE_FAILED", ["Admin-managed config not applied outcome=parse_failed"]),
        ("APPLY_FAILED", ["Admin-managed config not applied outcome=apply_failed"]),
        ("DISABLED", []),
        ("NO_API_KEY", []),
        ("APPLIED", []),
    ],
)
async def test_unified_config_reload_reports_the_admin_config_outcome(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
    outcome: str,
    expected: list[str],
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server import _unified_harness_backend_adapter as adapter_module
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedRuntimeDerivation,
        UnifiedSessionContext,
    )
    from vibe.app_server.protocol import ConfigReloadParams

    async def refresh(_orchestrator: object) -> object:
        return _admin_result(AdminConfigOutcome[outcome], error="boom")

    monkeypatch.setattr(adapter_module, "refresh_admin_layer", refresh)

    orchestrator = FakeConfigOrchestrator[VibeConfigSchema](build_test_vibe_config())
    harness_files = get_harness_files_manager()
    agents = AgentManager(
        orchestrator, orchestrator.config.default_agent, harness_files=harness_files
    )
    derivation = UnifiedRuntimeDerivation(
        runtime=build_unified_runtime_snapshot(orchestrator, agents),
        core_config=_stub_core_config(),
        adapter_config=_stub_adapter_config(),
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
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, _RecordingSession()), str(tmp_path), context, derivation
    )

    with caplog.at_level(logging.WARNING, logger="vibe"):
        await adapter.reload_config(
            ConfigReloadParams(session_id=_RecordingSession.session_id)
        )

    assert [
        record.getMessage().split(" error=")[0]
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ] == expected


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
        cast(Any, session),
        str(tmp_path),
        context,
        context.derive(UnifiedSessionSettings()),
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
    from mistralai_vibe_local_harness.vibe._projection import (  # pyright: ignore[reportMissingImports]
        public_notice_entry,
    )

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


def test_pinning_a_cross_dir_session_cwd_drops_the_trust_grant(tmp_path: Path) -> None:
    # --trust is an ephemeral grant scoped to the caller's invocation cwd. Pinning a
    # resume/continue/fork to a session's stored cwd must not let --trust silently trust
    # -- and thus auto-run the hooks.toml of -- a project the caller is not in.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import _with_session_cwd
    from vibe.app_server.protocol import SessionOptions

    options = SessionOptions(cwd=str(tmp_path / "project-a"), trust_workspace=True)
    stored = str((tmp_path / "project-b").resolve())

    pinned = _with_session_cwd(options, stored)

    assert pinned.cwd == stored
    assert pinned.trust_workspace is False


def test_pinning_the_same_cwd_keeps_the_trust_grant(tmp_path: Path) -> None:
    # Continuing a session from within its own cwd with --trust still trusts it: the
    # grant is only dropped when the pinned cwd differs from the caller's invocation cwd.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import _with_session_cwd
    from vibe.app_server.protocol import SessionOptions

    cwd = tmp_path / "project"
    options = SessionOptions(cwd=str(cwd), trust_workspace=True)

    pinned = _with_session_cwd(options, str(cwd.resolve()))

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
        UnifiedRuntimeDerivation,
        UnifiedSessionContext,
    )
    from vibe.app_server.protocol import AccountReadParams

    # Prepare
    healed = "https://tenant.example"
    orchestrator = FakeConfigOrchestrator[VibeConfigSchema](build_test_vibe_config())
    assert orchestrator.config.vibe_base_url != healed
    harness_files = get_harness_files_manager()
    agents = AgentManager(
        orchestrator, orchestrator.config.default_agent, harness_files=harness_files
    )
    derivation = UnifiedRuntimeDerivation(
        runtime=build_unified_runtime_snapshot(orchestrator, agents),
        core_config=_stub_core_config(),
        adapter_config=_stub_adapter_config(),
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
        account_gateway=FakeAccountGateway(
            WhoAmIResult(
                plan_type=AccountPlanKind.CHAT, plan_name="TEAM", vibe_base=healed
            )
        ),
    )
    session = _RecordingSession()
    session.active_turn_id = "turn-1"
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), str(tmp_path), context, derivation
    )

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
        from mistralai_vibe_local_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
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
async def test_unified_derivation_keeps_the_session_counters_and_reprices_them(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedRuntimeDerivation,
        UnifiedSessionContext,
    )

    started = _priced_runtime()
    swapped = _priced_runtime(active_model="devstral-small")
    assert (
        swapped.stats.input_price_per_million != started.stats.input_price_per_million
    )
    derivation = UnifiedRuntimeDerivation(
        runtime=swapped,
        core_config=_stub_core_config(),
        adapter_config=_stub_adapter_config(),
    )
    context = UnifiedSessionContext(
        storage_root=str(tmp_path),
        legacy_source_loader=cast(Any, None),
        legacy_source_resolver=cast(Any, None),
        plugins=cast(Any, object()),
        plugin_provider=cast(Any, object()),
        requested_plugins=(),
        config_orchestrator=cast(Any, None),
        harness_files=get_harness_files_manager(),
        agents=cast(Any, None),
        derive=lambda _settings: derivation,
        permissions=cast(Any, None),
        mcp_catalog=ResolvedMCPCatalog(revision="test", servers=()),
        mcp_authorization_provider=MCPAuthenticationService(),
        plugin_mcp=_empty_plugin_mcp(),
        mcp_cache_root=str(tmp_path / "mcp-descriptors"),
        mcp_enable_system_trust_store=False,
    )
    adapter = cast(
        Any,
        UnifiedHarnessBackendAdapter(
            cast(Any, _RecordingSession()),
            str(tmp_path),
            context,
            UnifiedRuntimeDerivation(
                runtime=started,
                core_config=_stub_core_config(),
                adapter_config=_stub_adapter_config(),
            ),
        ),
    )
    adapter._translate_snapshot_update(_usage_state(), _usage_state(usage=(1000, 200)))

    await adapter._apply_derivation()
    stats = adapter.runtime_updated_params().runtime.stats

    assert stats.session_prompt_tokens == 1000
    assert stats.session_completion_tokens == 200
    assert stats.input_price_per_million == swapped.stats.input_price_per_million
    assert stats.output_price_per_million == swapped.stats.output_price_per_million


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
        UnifiedRuntimeDerivation,
        UnifiedSessionContext,
        _user_plan_from_manager,
    )
    from vibe.core.experiments.active import ExperimentSurface
    from vibe.core.experiments.manager import ExperimentManager
    from vibe.core.experiments.models import ExperimentAttributes

    config = build_test_vibe_config(enable_telemetry=True)
    orchestrator = FakeConfigOrchestrator[VibeConfigSchema](config)
    harness_files = get_harness_files_manager()
    agents = AgentManager(
        orchestrator, orchestrator.config.default_agent, harness_files=harness_files
    )
    derivation = UnifiedRuntimeDerivation(
        runtime=build_unified_runtime_snapshot(orchestrator, agents),
        core_config=_stub_core_config(),
        adapter_config=_stub_adapter_config(),
    )
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
    context = UnifiedSessionContext(
        storage_root=str(tmp_path),
        legacy_source_loader=cast(Any, None),
        legacy_source_resolver=cast(Any, None),
        plugins=cast(Any, object()),
        plugin_provider=cast(Any, object()),
        plugin_mcp=_empty_plugin_mcp(),
        requested_plugins=(),
        config_orchestrator=cast(Any, orchestrator),
        harness_files=harness_files,
        agents=agents,
        derive=lambda _settings: derivation,
        permissions=cast(Any, None),
        mcp_catalog=ResolvedMCPCatalog(revision="test", servers=()),
        mcp_authorization_provider=MCPAuthenticationService(),
        mcp_cache_root=str(tmp_path / "mcp-descriptors"),
        mcp_enable_system_trust_store=False,
        experiment_manager=manager,
    )
    telemetry = TelemetryClient(
        config_getter=lambda: context.config_orchestrator.config,
        user_plan_getter=lambda: _user_plan_from_manager(manager),
        experiment_attributes_getter=manager.attributes,
    )
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, _RecordingSession()),
        str(tmp_path),
        context,
        derivation,
        telemetry_client=telemetry,
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
    pytest.importorskip("mistralai_rust_harness.vibe")
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
async def test_ready_wait_reports_not_ready_when_connector_resolve_cancelled(
    tmp_path: Path,
) -> None:
    # A cancelled connector resolve (rapid start/stop) must report not-ready,
    # mirroring the cancelled-experiment-eval path.
    pytest.importorskip("mistralai_rust_harness.vibe")

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
    pytest.importorskip("mistralai_rust_harness.vibe")

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
        UnifiedRuntimeDerivation,
        UnifiedSessionContext,
    )

    config = build_test_vibe_config(enable_telemetry=True)
    orchestrator = FakeConfigOrchestrator[VibeConfigSchema](config)
    harness_files = get_harness_files_manager()
    agents = AgentManager(
        orchestrator, orchestrator.config.default_agent, harness_files=harness_files
    )
    derivation = UnifiedRuntimeDerivation(
        runtime=build_unified_runtime_snapshot(orchestrator, agents),
        core_config=_stub_core_config(),
        adapter_config=_stub_adapter_config(),
    )
    context = UnifiedSessionContext(
        storage_root=str(tmp_path),
        legacy_source_loader=cast(Any, None),
        legacy_source_resolver=cast(Any, None),
        plugins=cast(Any, object()),
        plugin_provider=cast(Any, object()),
        plugin_mcp=_empty_plugin_mcp(),
        requested_plugins=(),
        config_orchestrator=cast(Any, orchestrator),
        harness_files=harness_files,
        agents=agents,
        derive=lambda _settings: derivation,
        permissions=cast(Any, None),
        mcp_catalog=ResolvedMCPCatalog(revision="test", servers=()),
        mcp_authorization_provider=MCPAuthenticationService(),
        mcp_cache_root=str(tmp_path / "mcp-descriptors"),
        mcp_enable_system_trust_store=False,
    )
    telemetry = TelemetryClient(
        config_getter=lambda: context.config_orchestrator.config
    )
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, _RecordingSession()),
        str(tmp_path),
        context,
        derivation,
        telemetry_client=telemetry,
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
async def test_completion_attribution_is_not_shared_between_forked_sessions(
    tmp_path: Path,
) -> None:
    """*Prepare*: Two derivations off one context, as a rewind makes.
    *Do*: Bind each to its own session's attribution, the forked one last.
    *Assert*: Each runtime config still reports the session it belongs to.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._completion_attribution import build_completion_attribution
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionSettings

    def attribution_for(session_id: str):
        return build_completion_attribution(
            TelemetryClient(
                config_getter=build_test_vibe_config,
                session_id_getter=lambda: session_id,
            ),
            None,
        )

    # Prepare
    # A rewind derives afresh against the context the source session is still
    # running on, so both derivations come from one context here too.
    context = await _test_session_runtime_builder()(SessionOptions(cwd=str(tmp_path)))
    source = context.derive(UnifiedSessionSettings())
    forked = context.derive(UnifiedSessionSettings())

    # Do
    source.completion_attribution.bind(attribution_for("session-source"))
    forked.completion_attribution.bind(attribution_for("session-forked"))

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
async def test_cache_parent_session_id_reads_from_state(tmp_path: Path) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    class _ParentSession(_RecordingSession):
        async def read(self, _params: Any) -> Any:
            return SimpleNamespace(
                snapshot=SimpleNamespace(
                    state=SimpleNamespace(
                        session=SimpleNamespace(parent_session_id="parent-1")
                    )
                )
            )

    adapter = _inert_adapter(_ParentSession(), str(tmp_path), str(tmp_path))
    await adapter._cache_parent_session_id()

    assert adapter._parent_session_id == "parent-1"


@pytest.mark.asyncio
async def test_the_sweep_reads_resumable_directories_from_the_harness_store() -> None:
    """The listing the sweep is told about comes from the harness's own store.

    Not from the legacy session index, which globs one level under the save dir
    for a session prefix and so cannot see `unified/<id>/`. Reading it there
    answered that nothing is in use, and the sweep reclaimed the checkout of
    every closed session that was still resumable.

    Paged to the end and cwd-less entries dropped: a session with no directory
    holds no worktree, and a page left unread is a worktree the sweep is free
    to delete.
    """
    pytest.importorskip("mistralai.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendHostAdapter,
    )

    pages = [
        SimpleNamespace(
            items=[SimpleNamespace(cwd="/w/one"), SimpleNamespace(cwd=None)],
            next_cursor="page-2",
        ),
        SimpleNamespace(items=[SimpleNamespace(cwd="/w/two")], next_cursor=None),
    ]
    cursors: list[str | None] = []

    async def _list(**kwargs: Any) -> Any:
        cursors.append(kwargs["cursor"])
        return pages[len(cursors) - 1]

    host = cast(Any, object.__new__(UnifiedHarnessBackendHostAdapter))
    host._host = SimpleNamespace(list=_list)

    assert await host._harness_resumable_directories() == (
        Path("/w/one"),
        Path("/w/two"),
    )
    assert cursors == [None, "page-2"]


@pytest.mark.asyncio
async def test_the_sweep_is_told_about_legacy_sessions_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worktrees on disk are not split the way the sessions are.

    A harness host that answered only for `unified/` reclaimed the checkouts a
    saved legacy session still resumed into, so trying the experimental harness
    and going back left those sessions pointed at directories that were gone.
    """
    pytest.importorskip("mistralai.vibe")
    from vibe.app_server import _unified_harness_backend_adapter as adapter_module
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendHostAdapter,
    )

    async def _list(**_kwargs: Any) -> Any:
        return SimpleNamespace(
            items=[SimpleNamespace(cwd="/w/harness")], next_cursor=None
        )

    monkeypatch.setattr(
        adapter_module, "resume_directories", lambda _config: (Path("/w/legacy"),)
    )
    host = cast(Any, object.__new__(UnifiedHarnessBackendHostAdapter))
    host._host = SimpleNamespace(list=_list)

    resumable = host._resumable_directories(cast(Any, object()))

    assert await resumable() == (Path("/w/legacy"), Path("/w/harness"))


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
    from vibe.app_server import _unified_harness_backend_adapter as adapter_module

    def _fail_listing(_config: Any, _cwd: Any) -> Any:
        raise OSError("disk gone")

    monkeypatch.setattr(adapter_module, "list_local_resume_sessions", _fail_listing)

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
