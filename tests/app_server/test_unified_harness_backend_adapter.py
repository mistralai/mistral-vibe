from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from pathlib import Path
import re
from typing import Any, cast

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.app_server import build_test_app_server
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server import _runtime as runtime_module
from vibe.app_server._mcp_auth import MCPAuthenticationService
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
)
from vibe.app_server.client import AppServerClient
from vibe.app_server.models import (
    CompletedEffectState,
    FailedEffectState,
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
    PublicCallbackEntry,
    PublicEffectEntry,
    PublicMessageEntry,
    ResourceContentBlock,
    ShellEffectOutput,
    TextContentBlock,
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
    FeedbackShouldShowParams,
    FeedbackShouldShowResponse,
    PageRequest,
    PluginInfoParams,
    PluginInfoResponse,
    ProtocolErrorCode,
    RuntimeReadParams,
    RuntimeReadResponse,
    SessionContinueParams,
    SessionForkParams,
    SessionKind,
    SessionListParams,
    SessionOptions,
    SessionReadParams,
    SessionReadResponse,
    SessionResumeParams,
    SessionStartParams,
    TurnStartParams,
    TurnStartResponse,
    TurnSteerParams,
    WorkspacePromptPrepareParams,
    WorkspacePromptPrepareResponse,
)
from vibe.app_server.server import AppServer
from vibe.app_server.session import AppServerSession
from vibe.app_server.transport import memory_transport_pair
from vibe.core.agents.manager import AgentManager
from vibe.core.config import SessionLoggingConfig, VibeConfigSchema
from vibe.core.config.admin_config import AdminConfigApplyResult, AdminConfigOutcome
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.session.session_interop import (
    InvalidLegacyInteropSourceError,
    export_legacy_committed_history,
    resolve_legacy_session_reference,
)
from vibe.core.session.session_lease import SessionBusyError, SessionLease
from vibe.core.types import LLMMessage, Role
from vibe.user_content import UserTextResource

_SESSION_CREATED = re.compile(
    r"^Session created: harness=(?P<harness>\w+) session_id=(?P<session_id>\S+)$"
)


class _RecordingSession:
    """Stands in for the Harness session, recording every pushed adapter config."""

    session_id = "session-1"
    active_turn_id: str | None = None

    def __init__(self) -> None:
        self.applied: list[object] = []

    def apply_adapter_config(self, adapter_config: object) -> None:
        self.applied.append(adapter_config)


def _admin_result(
    outcome: AdminConfigOutcome, *, error: str | None = None
) -> AdminConfigApplyResult:
    return AdminConfigApplyResult(outcome, error=error)


def _inert_adapter(
    session: object,
    cwd: str | None,
    storage_root: str,
    *,
    runtime: object | None = None,
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
        config_orchestrator=cast(Any, None),
        harness_files=cast(Any, None),
        derive=cast(Any, None),
        mcp_catalog=ResolvedMCPCatalog(revision="test", servers=()),
        mcp_authorization_provider=MCPAuthenticationService(),
        mcp_cache_root=str(Path(storage_root) / "mcp-descriptors"),
        mcp_enable_system_trust_store=False,
    )
    derivation = UnifiedRuntimeDerivation(
        runtime=cast(Any, runtime if runtime is not None else object()),
        core_config=cast(Any, None),
        plugin_lock=cast(Any, None),
        adapter_config=cast(Any, None),
    )
    return UnifiedHarnessBackendAdapter(cast(Any, session), cwd, context, derivation)


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


def test_unified_mcp_projection_update_preserves_connector_sources() -> None:
    pytest.importorskip("mistralai_rust_harness.vibe")

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
    pytest.importorskip("mistralai_rust_harness.vibe")
    from mistralai_rust_harness.vibe._session import (  # pyright: ignore[reportMissingImports]
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
    )

    events = adapter._callback_events({
        "type": "callback_requested",
        "callback": callback,
    })

    assert events is not None
    assert isinstance(adapter.open_callbacks()[0], PublicCallbackEntry)
    assert adapter.open_callbacks()[0].callback_id == "approval-call-1"

    await adapter.reject_callback_delivery(
        "session-1", "approval-call-1", CallbackResultError(message="not delivered")
    )

    assert fake_session.rejected is not None
    rejected = cast(Any, fake_session.rejected)
    assert rejected.result.callback_id == "approval-call-1"
    assert rejected.result.error.message == "not delivered"


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
    pytest.importorskip("mistralai_rust_harness.vibe")
    from mistralai_rust_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustIdleTurn,
        RustImageContentBlock,
        RustNoNextAction,
        RustSessionTransition,
        RustTextContentBlock,
        RustTurnStartedObservation,
    )
    from mistralai_rust_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        HistoryCursor,
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
    )
    from mistralai_rust_harness.vibe._projection import (  # pyright: ignore[reportMissingImports]
        SessionProjector,
    )
    from mistralai_rust_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
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
    pytest.importorskip("mistralai_rust_harness.vibe")
    from mistralai_rust_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustIdleTurn,
        RustNoNextAction,
        RustProtocolError,
        RustSessionTransition,
        RustTextContentBlock,
        RustToolFailureResult,
        RustToolResultCommittedObservation,
        RustToolSuccessResult,
    )
    from mistralai_rust_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        HistoryCursor,
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
    )
    from mistralai_rust_harness.vibe._projection import (  # pyright: ignore[reportMissingImports]
        SessionProjector,
    )
    from mistralai_rust_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
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


def test_unified_shell_result_projection_uses_public_output_shape() -> None:
    pytest.importorskip("mistralai_rust_harness.vibe")
    from mistralai_rust_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustIdleTurn,
        RustNoNextAction,
        RustSessionTransition,
        RustToolResultCommittedObservation,
        RustToolSuccessResult,
    )
    from mistralai_rust_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        HistoryCursor,
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
    )
    from mistralai_rust_harness.vibe._projection import (  # pyright: ignore[reportMissingImports]
        SessionProjector,
    )
    from mistralai_rust_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
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
    effect = validate_history_entry(raw)

    assert isinstance(effect, PublicEffectEntry)
    assert isinstance(effect.state, CompletedEffectState)
    assert effect.detail.tool_name == "bash"
    assert ShellEffectOutput.model_validate(effect.state.output) == ShellEffectOutput(
        stdout="slept\n", stderr="", truncated=False
    )
    assert effect.state.output_text == "slept\n"


def _approval_action(turn_id: str) -> Any:
    from mistralai_rust_harness.protocol import (  # pyright: ignore[reportMissingImports]
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
    pytest.importorskip("mistralai_rust_harness.vibe")
    from mistralai_rust_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
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
    pytest.importorskip("mistralai_rust_harness.vibe")
    from mistralai_rust_harness.vibe import (  # pyright: ignore[reportMissingImports]
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
    ("auto_approve", "expected_mode"), [(False, "ask"), (True, "allow")]
)
@pytest.mark.asyncio
async def test_unified_runtime_config_gates_editing_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    auto_approve: bool,
    expected_mode: str,
) -> None:
    pytest.importorskip("mistralai_rust_harness.vibe")
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
    assert tool_modes["file_system.read_file"] == "allow"
    assert tool_modes["file_system.write_file"] == expected_mode
    assert tool_modes["file_system.search_replace"] == expected_mode
    assert tool_modes["file_system.bash"] == expected_mode


@pytest.mark.asyncio
async def test_unified_runtime_denies_a_tool_disabled_by_a_live_config_patch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_rust_harness.vibe")
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
    assert after["file_system.bash"] == "deny"
    assert after["file_system.read_file"] == "allow"


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
    from vibe.app_server._runtime import _rust_tool_modes

    available = set(shell_tool.split("_and_")) | {"read_file"}

    modes = _rust_tool_modes(available, bypass_approval=True)

    assert modes["file_system.bash"] == "allow"


def test_unified_shell_builtin_is_denied_when_no_shell_tool_is_available() -> None:
    """Disabling the shell in the layered config still has to stop the Runtime."""
    from vibe.app_server._runtime import _rust_tool_modes

    modes = _rust_tool_modes({"read_file"}, bypass_approval=True)

    assert modes["file_system.bash"] == "deny"
    assert modes["file_system.read_file"] == "allow"


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
    pytest.importorskip("mistralai_rust_harness.vibe")
    config = build_test_vibe_config(system_prompt_id=system_prompt_id)

    # Do
    instructions = runtime_module._build_unified_system_instructions(config)

    # Assert
    assert all(phrase in instructions for phrase in expected_phrases)


# Adapter tests that install a plugin from the vibe_sdk fixtures live in
# test_unified_harness_plugins.py: the fixtures sit outside the vibe/ release
# tree, so those tests are omitted from the public tree and this file is not.


@pytest.mark.asyncio
async def test_unified_plugin_lock_mismatch_is_reported_as_a_conflict() -> None:
    """The pin detects a changed environment; it cannot restore the old one.

    An operator who edited a plugin has to be told that is what happened, so
    the mismatch is a conflict with an actionable message rather than the
    internal error every unmapped Harness code falls back to.
    """
    pytest.importorskip("mistralai_rust_harness.vibe")
    from mistralai_rust_harness.vibe import (  # pyright: ignore[reportMissingImports]
        HarnessResourceLockError,
    )

    from vibe.app_server._unified_harness_backend_adapter import _harness_call

    async def fail() -> None:
        raise HarnessResourceLockError("session-1", "plugin")

    with pytest.raises(SessionBackendError) as exc_info:
        await _harness_call(fail())

    assert exc_info.value.code is ProtocolErrorCode.CONFLICT
    assert "plugins installed now" in str(exc_info.value)
    assert "start a new session" in str(exc_info.value)


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
    assert response.prompt.auto_title == "hello"


@pytest.mark.asyncio
async def test_unified_turn_start_injects_mentioned_file_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_rust_harness.vibe")
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
    adapter = _inert_adapter(object(), str(tmp_path), str(tmp_path))

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
    pytest.importorskip("mistralai_rust_harness.vibe")
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
    pytest.importorskip("mistralai_rust_harness.vibe")
    from mistralai_rust_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
        IdleSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
    )
    from mistralai_rust_harness.vibe._session import (  # pyright: ignore[reportMissingImports]
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
                    )
                ),
                history_limit=1,
                watermark=watermark,
            )

    adapter = _inert_adapter(FakeHarnessSession(), str(tmp_path), str(tmp_path))

    subscription = await adapter.subscribe(SessionReadParams(session_id="session-1"))
    await asyncio.wait_for(adapter.flush_events(), timeout=0.1)
    await cast(Any, subscription.events).aclose()


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
async def test_unified_harness_start_returns_distinct_session_identities() -> None:
    host = _harness_backend_host()

    first = await host.start(SessionStartParams())
    second = await host.start(SessionStartParams())
    await host.shutdown()

    assert host.harness_kind == "rust"
    assert first.backend.session_id != second.backend.session_id


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
@pytest.mark.parametrize("use_short_id", [False, True])
async def test_unified_resume_imports_quiescent_legacy_history(
    tmp_path: Path, use_short_id: bool
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
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
    session_id = legacy.session_id
    await legacy.aclose()

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
    from mistralai_rust_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
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
    vibe_runtime = pytest.importorskip("mistralai_rust_harness.vibe")
    from mistralai_rust_harness.session_protocol import (  # pyright: ignore[reportMissingImports]
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
        exported = export_legacy_committed_history(session_id, config.session_logging)
        assert exported is not None
        assert exported.history == []
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
    vibe_runtime = pytest.importorskip("mistralai_rust_harness.vibe")
    from mistralai_rust_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
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


def _harness_backend_host(config: VibeConfigSchema | None = None) -> SessionBackendHost:
    vibe_runtime = pytest.importorskip("mistralai_rust_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import adapt_harness_host

    return adapt_harness_host(
        vibe_runtime.create_harness_host(), _test_session_runtime_builder(config)
    )


def _test_session_runtime_builder(
    config: VibeConfigSchema | None = None,
) -> Callable[[SessionOptions], Awaitable[Any]]:
    orchestrator = FakeConfigOrchestrator(config or build_test_vibe_config())

    async def build(options: SessionOptions) -> Any:
        from mistralai_rust_harness.vibe import (  # pyright: ignore[reportMissingImports]
            LegacyImportSource,
            LegacySessionReference as HarnessLegacySessionReference,
            LocalRuntimeAdapterConfig,
        )
        from mistralai_rust_harness.vibe._host import (  # pyright: ignore[reportMissingImports]
            _core_config,
            _empty_plugin_lock,
        )

        from vibe.app_server._plugins import resolve_session_plugins
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
            )

        harness_files = get_harness_files_manager()
        agents = AgentManager(
            orchestrator,
            options.agent or orchestrator.config.default_agent,
            harness_files=harness_files,
        )

        def derive(_settings: Any) -> Any:
            return UnifiedRuntimeDerivation(
                runtime=build_unified_runtime_snapshot(orchestrator, agents),
                core_config=_core_config("runtime-template"),
                plugin_lock=_empty_plugin_lock(),
                adapter_config=LocalRuntimeAdapterConfig(),
            )

        return UnifiedSessionContext(
            storage_root=orchestrator.config.session_logging.save_dir,
            legacy_source_loader=load_legacy_source,
            legacy_source_resolver=resolve_legacy_source,
            plugins=await resolve_session_plugins(get_harness_files_manager()),
            config_orchestrator=orchestrator,
            harness_files=harness_files,
            derive=derive,
            mcp_catalog=ResolvedMCPCatalog(revision="test", servers=()),
            mcp_authorization_provider=MCPAuthenticationService(),
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
    pytest.importorskip("mistralai_rust_harness.vibe")
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
async def test_unified_harness_will_not_pretend_to_reload_plugins() -> None:
    client, server = _connect_harness_host()

    try:
        await client.initialize(ClientInfo(name="test", version="0"))
        await client.notify("initialized")
        started = SessionReadResponse.model_validate(
            await client.request("session/start", SessionStartParams())
        )
        with pytest.raises(AppServerResponseError) as excinfo:
            await client.request(
                "plugin/reload", {"sessionId": started.state.session.id}
            )
    finally:
        await server.close()

    # Reporting success would tell a client the roots were rescanned.
    assert excinfo.value.error.code is ProtocolErrorCode.NOT_IMPLEMENTED


@pytest.mark.asyncio
@pytest.mark.parametrize("reload_runtime", [False, True])
async def test_unified_config_reload_refreshes_the_layer_stack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reload_runtime: bool
) -> None:
    """A light reload exists to pick up file-backed layers edited outside the
    session, so it must reload the orchestrator just like a full one does.
    ``reload_runtime`` gates the runtime rebuild, not the layer stack.
    """
    pytest.importorskip("mistralai_rust_harness.vibe")
    from vibe.app_server import _unified_harness_backend_adapter as adapter_module
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedRuntimeDerivation,
        UnifiedSessionContext,
    )
    from vibe.app_server.protocol import ConfigReloadParams

    class CountingOrchestrator(FakeConfigOrchestrator[VibeConfigSchema]):
        reloads = 0

        async def reload(self) -> None:
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
        core_config=cast(Any, None),
        plugin_lock=cast(Any, None),
        adapter_config=cast(Any, object()),
    )
    context = UnifiedSessionContext(
        storage_root=str(tmp_path),
        legacy_source_loader=cast(Any, None),
        legacy_source_resolver=cast(Any, None),
        plugins=cast(Any, object()),
        config_orchestrator=cast(Any, orchestrator),
        harness_files=harness_files,
        derive=lambda _settings: derivation,
        mcp_catalog=ResolvedMCPCatalog(revision="test", servers=()),
        mcp_authorization_provider=MCPAuthenticationService(),
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
    pytest.importorskip("mistralai_rust_harness.vibe")
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
        core_config=cast(Any, None),
        plugin_lock=cast(Any, None),
        adapter_config=cast(Any, object()),
    )
    context = UnifiedSessionContext(
        storage_root=str(tmp_path),
        legacy_source_loader=cast(Any, None),
        legacy_source_resolver=cast(Any, None),
        plugins=cast(Any, object()),
        config_orchestrator=cast(Any, orchestrator),
        harness_files=harness_files,
        derive=lambda _settings: derivation,
        mcp_catalog=ResolvedMCPCatalog(revision="test", servers=()),
        mcp_authorization_provider=MCPAuthenticationService(),
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
    pytest.importorskip("mistralai_rust_harness.vibe")
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
