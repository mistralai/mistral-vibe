from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
import re
from typing import Any

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.app_server import build_test_app_server
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server import _runtime as runtime_module
from vibe.app_server._runtime import AgentRuntimeFactory, build_runtime_snapshot
from vibe.app_server._session_backend_port import (
    SessionBackendError,
    SessionBackendHost,
    SessionBackendRuntimeView,
)
from vibe.app_server.client import AppServerClient
from vibe.app_server.models import (
    CompletedEffectState,
    FailedEffectState,
    PublicEffectEntry,
    TextContentBlock,
    TurnErrorCode,
    validate_history_entry,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientCapabilities,
    ClientInfo,
    FeedbackShouldShowParams,
    FeedbackShouldShowResponse,
    PageRequest,
    ProtocolErrorCode,
    RuntimeReadParams,
    RuntimeReadResponse,
    SessionContinueParams,
    SessionForkParams,
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
from vibe.core.config import SessionLoggingConfig, VibeConfigSchema
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.session.session_interop import (
    InvalidLegacyInteropSourceError,
    export_legacy_committed_history,
    resolve_legacy_session_reference,
)
from vibe.core.session.session_lease import SessionBusyError, SessionLease
from vibe.core.types import LLMMessage, Role

_SESSION_CREATED = re.compile(
    r"^Session created: harness=(?P<harness>\w+) session_id=(?P<session_id>\S+)$"
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
    assert response.prompt.auto_title is None


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
    assert response.last_event_id == 0


@pytest.mark.asyncio
async def test_unified_harness_rejects_unsupported_turn_methods() -> None:
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

    assert exc_info.value.error.code is ProtocolErrorCode.INTERNAL_ERROR
    assert "steer_turn" in exc_info.value.error.message


@pytest.mark.asyncio
async def test_unified_harness_start_returns_distinct_session_identities() -> None:
    host = _harness_backend_host()

    first = await host.start(SessionStartParams())
    second = await host.start(SessionStartParams())
    await host.shutdown()

    assert host.harness_kind == "rust"
    assert first.backend.session_id != second.backend.session_id


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
    legacy = build_test_agent_loop(config=config)
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
    read = await resumed.backend.read(
        SessionReadParams(session_id=session_id, history=PageRequest(limit=10))
    )
    await host.shutdown()

    assert read.state.history is not None
    assert [entry.model_dump()["role"] for entry in read.state.history] == [
        "user",
        "assistant",
    ]
    assert resumed.backend.session_id == session_id
    from mistralai_rust_harness.vibe._storage import (  # pyright: ignore[reportMissingImports]
        LegacyInteropSourceV1,
        UnifiedSessionStore,
    )

    stored = UnifiedSessionStore(tmp_path, session_id).load()
    assert (
        not (tmp_path / "unified" / requested_id).is_dir() or requested_id == session_id
    )
    provenance = stored.runtime_state.import_provenance
    assert provenance is not None
    assert isinstance(provenance.source, LegacyInteropSourceV1)


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

    assert resumed.state.session.id == session_id
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

        from vibe.app_server._unified_harness_backend_adapter import (
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
                session_id=reference.session_id,
                cwd=reference.cwd,
                root_session_id=reference.root_session_id,
                parent_session_id=reference.parent_session_id,
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
                    session_id=export.reference.session_id,
                    cwd=export.reference.cwd,
                    root_session_id=export.reference.root_session_id,
                    parent_session_id=export.reference.parent_session_id,
                ),
                store_revision=export.store_revision,
                history=export.history,
            )

        return UnifiedSessionContext(
            runtime=build_runtime_snapshot(
                options, orchestrator, get_harness_files_manager()
            ),
            storage_root=orchestrator.config.session_logging.save_dir,
            legacy_source_loader=load_legacy_source,
            legacy_source_resolver=resolve_legacy_source,
            core_config=_core_config("runtime-template"),
            plugin_lock=_empty_plugin_lock(),
            adapter_config=LocalRuntimeAdapterConfig(),
        )

    return build


def _connect_harness_host(
    config: VibeConfigSchema | None = None,
) -> tuple[AppServerClient, AppServer]:
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
