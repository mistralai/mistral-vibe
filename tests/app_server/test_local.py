from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
import tomli_w

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server import _runtime as runtime
from vibe.app_server._host import HostRequestHandler
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientCapabilities,
    ClientInfo,
    ConfigSchemaReadParams,
    HistoryListParams,
    ProtocolErrorCode,
    SessionDeleteParams,
    SessionListParams,
    SessionMCPStdioServer,
    SessionOptions,
    SessionReadParams,
    SessionStartParams,
    SessionTitleUpdateParams,
    SessionTitleUpdateResponse,
    WorkspaceTrustStatusParams,
)
from vibe.app_server.server import AppServer
from vibe.app_server.session import AppServerSession
from vibe.app_server.transport import memory_transport_pair
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import SessionLoggingConfig, VibeConfigSchema
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.hooks.config import HookConfigResult
from vibe.core.session.session_loader import SessionLoader
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.utils.terminal import TerminalEmulator


class ClosingBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.closed = True


@pytest.mark.asyncio
async def test_passive_host_renames_saved_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    saved = build_test_agent_loop(config=config)
    await saved.persist_empty_session()
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))
    monkeypatch.setattr(handler, "_load_config", AsyncMock(return_value=config))

    try:
        result = await handler.dispatch(
            "session/title/update",
            SessionTitleUpdateParams(
                session_id=saved.session_id, title="  Reviewed session  "
            ).model_dump(mode="json", by_alias=True),
        )
    finally:
        await saved.aclose()
        await saved.telemetry_client.aclose()

    response = cast(SessionTitleUpdateResponse, result.response)
    session_path = SessionLoader.find_session_by_id(
        saved.session_id, config.session_logging
    )
    assert session_path is not None
    _, metadata = SessionLoader.load_session(session_path)
    assert response.title == "Reviewed session"
    assert response.updated_at == metadata["end_time"]
    assert metadata["title"] == "Reviewed session"


@pytest.mark.asyncio
async def test_config_load_is_bound_to_harness_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    config_dir = project / ".vibe"
    config_dir.mkdir(parents=True)
    with (config_dir / "config.toml").open("wb") as file:
        tomli_w.dump({"default_agent": "plan"}, file)
    trusted_folders_manager.trust_for_session(project)
    monkeypatch.chdir(tmp_path)

    harness_files = HarnessFilesManager(sources=("project",), cwd=project)

    loaded = await runtime.build_default_orchestrator(harness_files=harness_files)
    assert loaded.config.default_agent == "plan"

    monkeypatch.setenv("VIBE_DEFAULT_AGENT", "lean")
    loaded = await runtime.build_default_orchestrator(harness_files=harness_files)
    assert loaded.config.default_agent == "lean"


@pytest.mark.asyncio
async def test_root_config_discovery_uses_session_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = tmp_path / "launcher"
    session_cwd = tmp_path / "session"
    launcher.mkdir()
    config_file = session_cwd / ".vibe" / "config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text('default_agent = "plan"\n', encoding="utf-8")
    monkeypatch.chdir(launcher)
    monkeypatch.setattr(runtime, "setup_tracing", lambda _: None)

    process = runtime.HarnessProcess(HarnessFilesManager(sources=("project",)))
    blueprint = await process.build_root_blueprint(
        SessionOptions(cwd=str(session_cwd), trust_workspace=True),
        ClientInfo(name="cwd-test", version="1"),
    )

    assert blueprint.cwd == session_cwd
    assert blueprint.config.default_agent == "plan"


@pytest.mark.asyncio
async def test_build_runtime_applies_cli_overrides_inside_harness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = build_test_vibe_config(
        default_agent="plan",
        disabled_tools=["configured"],
        session_logging=SessionLoggingConfig(enabled=True),
    )
    hook_config = HookConfigResult(hooks=[], issues=[])
    sentinel = cast(AgentLoop, object())
    captured: dict[str, Any] = {}

    workspace_root = tmp_path / "extra"
    workspace_root.mkdir()

    async def load_config(
        data: dict[str, Any] | None = None, *, harness_files: HarnessFilesManager
    ) -> ConfigOrchestrator[VibeConfigSchema]:
        captured["harness_files"] = harness_files
        base = OverridesLayer(data=config.model_dump(mode="json"), name="base")
        overrides = OverridesLayer(data=data or {})
        return await ConfigOrchestrator.create(
            schema=VibeConfigSchema,
            layers=[base, overrides],
            default_layer_resolver=lambda: base,
        )

    monkeypatch.setattr(runtime, "build_default_orchestrator", load_config)
    monkeypatch.setattr(
        runtime, "load_hooks_from_fs", lambda *, harness_files: hook_config
    )
    monkeypatch.setattr(runtime, "setup_tracing", lambda value: None)

    def build_agent_loop(config_orchestrator: object, **kwargs: Any) -> AgentLoop:
        captured["orchestrator"] = config_orchestrator
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(runtime, "AgentLoop", build_agent_loop)

    blueprint = await runtime.HarnessProcess().build_root_blueprint(
        SessionOptions(
            agent="lean",
            auto_approve=True,
            enabled_tools=["read_file"],
            disabled_tools=["bash"],
            max_turns=2,
            max_price=1.5,
            max_session_tokens=100,
            headless=True,
            trust_workspace=True,
            workspace_roots=[str(workspace_root)],
            mcp_servers=[
                SessionMCPStdioServer(
                    name="ephemeral",
                    command="server",
                    args=["--stdio"],
                    env={"TOKEN": "value"},
                )
            ],
        ),
        ClientInfo(
            name="test",
            version="1",
            entrypoint="programmatic",
            terminal_emulator=TerminalEmulator.GHOSTTY,
        ),
    )

    assert blueprint.build() is sentinel
    orchestrator = cast(ConfigOrchestrator[VibeConfigSchema], captured["orchestrator"])
    assert orchestrator.config.enabled_tools == ["read_file"]
    assert orchestrator.config.disabled_tools == ["configured", "bash"]
    assert [server.name for server in orchestrator.config.mcp_servers] == ["ephemeral"]
    assert orchestrator.config.mcp_servers[0].transport == "stdio"
    assert captured["agent_name"] == "lean"
    assert captured["enable_streaming"] is True
    assert captured["max_turns"] == 2
    assert captured["max_price"] == 1.5
    assert captured["max_session_tokens"] == 100
    assert captured["headless"] is True
    assert captured["defer_heavy_init"] is True
    assert captured["hook_config_result"] is hook_config
    assert captured["force_bypass_tool_permissions"] is True
    assert captured["launch_context"].terminal_emulator is TerminalEmulator.GHOSTTY
    assert captured["launch_context"].agent_entrypoint == "programmatic"
    harness_files = cast(HarnessFilesManager, captured["harness_files"])
    assert harness_files.workspace_roots == [Path.cwd().resolve(), workspace_root]
    assert harness_files.trust_store.is_trusted(Path.cwd()) is True

    await orchestrator.reload()
    assert orchestrator.config.enabled_tools == ["read_file"]
    assert orchestrator.config.disabled_tools == ["configured", "bash"]
    assert [server.name for server in orchestrator.config.mcp_servers] == ["ephemeral"]


@pytest.mark.asyncio
async def test_harness_process_configures_globals_once_and_shares_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_vibe_config()
    configure_calls: list[VibeConfigSchema] = []
    cache_stores: list[object] = []
    local_managed_shell_policies: list[bool] = []
    sentinel = cast(AgentLoop, object())

    async def load_config(
        data: dict[str, Any] | None = None, *, harness_files: HarnessFilesManager
    ) -> ConfigOrchestrator[VibeConfigSchema]:
        del data, harness_files
        layer = OverridesLayer(data=config.model_dump(mode="json"))
        return await ConfigOrchestrator.create(
            schema=VibeConfigSchema,
            layers=[layer],
            default_layer_resolver=lambda: layer,
        )

    monkeypatch.setattr(runtime, "build_default_orchestrator", load_config)
    monkeypatch.setattr(
        runtime,
        "load_hooks_from_fs",
        lambda *, harness_files: HookConfigResult(hooks=[], issues=[]),
    )
    monkeypatch.setattr(runtime, "setup_tracing", configure_calls.append)

    def build_agent_loop(config_orchestrator: object, **kwargs: Any) -> AgentLoop:
        cache_stores.append(kwargs["cache_store"])
        local_managed_shell_policies.append(kwargs["local_managed_shell_tools_enabled"])
        return sentinel

    monkeypatch.setattr(runtime, "AgentLoop", build_agent_loop)
    process = runtime.HarnessProcess()

    client = ClientInfo(name="test", version="1")
    capabilities = ClientCapabilities(client_tools=["terminal"])
    (await process.build_root_blueprint(SessionOptions(), client, capabilities)).build()
    (await process.build_root_blueprint(SessionOptions(), client)).build()

    assert len(configure_calls) == 1
    assert cache_stores == [process.cache_store, process.cache_store]
    assert local_managed_shell_policies == [False, True]


@pytest.mark.asyncio
async def test_runtime_is_built_only_when_session_start_crosses_json_rpc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = runtime.HarnessProcess()
    agent_loop = build_test_agent_loop()
    blueprint = Mock()
    blueprint.build.return_value = agent_loop
    build_blueprint = AsyncMock(return_value=blueprint)
    monkeypatch.setattr(process, "build_root_blueprint", build_blueprint)
    client_transport, server_transport = memory_transport_pair()
    harness = await runtime.create_harness_server(
        server_transport, transport_kind="in_process", process=process
    )
    client = AppServerClient(client_transport, run_peer=harness.serve)
    client_info = ClientInfo(name="wire-client", version="1", entrypoint="programmatic")
    options = SessionOptions(agent="plan", headless=True)
    capabilities = ClientCapabilities()

    assert build_blueprint.call_count == 0
    session = await AppServerSession.start(
        client,
        client_info=client_info,
        capabilities=capabilities,
        session_options=options,
    )
    try:
        build_blueprint.assert_called_once_with(options, client_info, capabilities)
        blueprint.build.assert_called_once_with()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_passive_host_requests_do_not_open_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path / "sessions"), session_prefix="session"
        )
    )
    saved = build_test_agent_loop(config=config)
    await saved.persist_empty_session()
    config_loads: list[bool] = []

    async def load_config(
        data: dict[str, Any] | None = None,
        *,
        harness_files: HarnessFilesManager,
        require_api_key: bool,
    ) -> ConfigOrchestrator[VibeConfigSchema]:
        del data, harness_files
        config_loads.append(require_api_key)
        return FakeConfigOrchestrator(config)

    monkeypatch.setattr("vibe.app_server._host.build_default_orchestrator", load_config)
    opened: list[runtime.RootOpenRequest] = []

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        opened.append(request)
        return saved

    harness_files = HarnessFilesManager(sources=())
    client_transport, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport,
        open_root=open_root,
        host_handler=HostRequestHandler(harness_files),
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="host-client", version="1"))
    await client.notify("initialized")

    try:
        await client.request("config/schema", ConfigSchemaReadParams())
        listed = await client.request(
            "session/list", SessionListParams(cwd=str(Path.cwd()))
        )
        assert listed["sessions"]
        await client.request(
            "session/read", SessionReadParams(session_id=saved.session_id)
        )
        await client.request(
            "history/list", HistoryListParams(session_id=saved.session_id)
        )
        await client.request(
            "workspace/trust/status", WorkspaceTrustStatusParams(cwd=str(Path.cwd()))
        )
        assert opened == []
        assert config_loads and all(required is False for required in config_loads)

        await client.request(
            "session/delete", SessionDeleteParams(session_id=saved.session_id)
        )
        assert opened == []

        await client.request("session/start", SessionStartParams(cwd=str(Path.cwd())))
        assert len(opened) == 1
        config_load_count = len(config_loads)
        await client.request("session/list", SessionListParams(cwd=str(Path.cwd())))
        assert len(config_loads) == config_load_count
        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                "session/delete", SessionDeleteParams(session_id=saved.session_id)
            )
        assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_session_continue_opens_the_resumed_root_once_over_json_rpc() -> None:
    resumed = build_test_agent_loop()
    requests: list[runtime.RootOpenRequest] = []

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        requests.append(request)
        return resumed

    client_transport, server_transport = memory_transport_pair()
    server = AppServer(server_transport, open_root=open_root)
    session = await AppServerSession.start(
        AppServerClient(client_transport, run_peer=server.serve),
        client_info=ClientInfo(name="continue-client", version="1"),
        capabilities=ClientCapabilities(),
        session_options=SessionOptions(cwd=str(Path.cwd())),
        continue_session=True,
    )
    try:
        assert session.session_id == resumed.session_id
        assert len(requests) == 1
        assert requests[0].continue_latest is True
        assert requests[0].session_id is None
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_session_start_reports_authentication_failure_as_typed_rpc_error() -> (
    None
):
    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        del request
        raise runtime.RuntimeAuthenticationError("mistral")

    client_transport, server_transport = memory_transport_pair()
    server = AppServer(server_transport, open_root=open_root)

    with pytest.raises(AppServerResponseError) as exc_info:
        await AppServerSession.start(
            AppServerClient(client_transport, run_peer=server.serve),
            client_info=ClientInfo(name="auth-client", version="1"),
            capabilities=ClientCapabilities(),
        )

    assert exc_info.value.error.code is ProtocolErrorCode.UNAUTHORIZED
    assert exc_info.value.error.data == {"provider": "mistral"}


@pytest.mark.asyncio
async def test_resume_builds_an_independent_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    logging = SessionLoggingConfig(
        enabled=True, save_dir=str(tmp_path / "sessions"), session_prefix="session"
    )
    source_backend = ClosingBackend()
    replacement_backend = ClosingBackend()
    source = build_test_agent_loop(
        config=build_test_vibe_config(session_logging=logging), backend=source_backend
    )
    source.stats.session_prompt_tokens = 11
    source.stats.session_completion_tokens = 7
    source.stats.context_tokens = 18
    await source.persist_empty_session()
    monkeypatch.setattr(
        "vibe.core.agent_loop._loop.create_backend", lambda **_: replacement_backend
    )

    replacement = await runtime.AgentRuntimeFactory().resume_root(
        source, source.session_id
    )
    try:
        assert replacement.backend is replacement_backend
        assert replacement.backend is not source.backend
        assert replacement.stats == source.stats
        await source.aclose()
        assert source_backend.closed
        assert not replacement_backend.closed
    finally:
        await replacement.aclose()
        await replacement.telemetry_client.aclose()
        await source.telemetry_client.aclose()


def test_continue_prefers_valid_terminal_session_pointer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    session_path = tmp_path / "session"
    monkeypatch.setattr(runtime.last_session_pointer, "load", lambda _config: "saved")
    monkeypatch.setattr(
        runtime.SessionLoader,
        "find_session_by_id",
        lambda *_args, **_kwargs: session_path,
    )

    source = cast(AgentLoop, SimpleNamespace(config=config))
    assert runtime.AgentRuntimeFactory().resolve_latest(source, Path.cwd()) == "saved"


def test_continue_falls_back_to_latest_session_for_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    session_path = tmp_path / "latest"
    monkeypatch.setattr(runtime.last_session_pointer, "load", lambda _config: "stale")
    monkeypatch.setattr(
        runtime.SessionLoader, "find_session_by_id", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        runtime.SessionLoader,
        "find_latest_session",
        lambda *_args, **_kwargs: session_path,
    )
    monkeypatch.setattr(
        runtime.SessionLoader,
        "load_session",
        lambda _path: ([], {"session_id": "latest-id"}),
    )

    source = cast(AgentLoop, SimpleNamespace(config=config))
    assert (
        runtime.AgentRuntimeFactory().resolve_latest(source, Path.cwd()) == "latest-id"
    )


def test_continue_requires_session_logging() -> None:
    config = build_test_vibe_config(session_logging=SessionLoggingConfig(enabled=False))
    source = cast(AgentLoop, SimpleNamespace(config=config))

    with pytest.raises(
        runtime.RuntimeSessionNotFoundError, match="Session logging is disabled"
    ):
        runtime.AgentRuntimeFactory().resolve_latest(source, Path.cwd())
