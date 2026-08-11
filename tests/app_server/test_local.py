from __future__ import annotations

import asyncio
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

from git import Repo
import pytest
import tomli_w

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server import _runtime as runtime
import vibe.app_server._host as host_module
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._model import validate_wire
from vibe.app_server._projection import project_config, project_config_view
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import (
    AppServerResponseError,
    AutoWorktreeInput,
    ClientCapabilities,
    ClientInfo,
    ConfigFieldsReadParams,
    ConfigFieldsReadResponse,
    ConfigReadParams,
    ConfigReadResponse,
    ConfigSchemaReadParams,
    EmptyResponse,
    ExistingWorktreeInput,
    NewWorktreeInput,
    ProtocolErrorCode,
    SessionDeleteParams,
    SessionHistoryListParams,
    SessionListParams,
    SessionListResponse,
    SessionMCPStdioServer,
    SessionOptions,
    SessionReadParams,
    SessionStartParams,
    SessionTitleUpdateParams,
    SessionTitleUpdateResponse,
    WorkspaceTrustStatusParams,
    WorkspaceWorktreeListParams,
    WorkspaceWorktreeListResponse,
)
import vibe.app_server.server as server_module
from vibe.app_server.server import AppServer, resolve_worktree
from vibe.app_server.session import AppServerSession
from vibe.app_server.transport import memory_transport_pair
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import ModelConfig, SessionLoggingConfig, VibeConfigSchema
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.hooks.config import HookConfigResult
from vibe.core.session.resume_sessions import ResumeSessionInfo
from vibe.core.session.session_loader import SessionLoader
from vibe.core.trusted_folders import trusted_folders_manager
import vibe.core.worktree as worktree_module
from vibe.core.worktree import (
    GitUnavailableError,
    list_linked_worktrees,
    prepare_worktree_session,
)
from vibe.utils.terminal import TerminalEmulator


class ClosingBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.closed = True


def _init_repo(root: Path) -> Repo:
    repo = Repo.init(root, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (root / "file.txt").write_text("hello\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    return repo


def test_session_start_worktree_create_selection_resolves_cwd(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    resolution = resolve_worktree(
        SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(
                branch="feat/app-server-worktree", name="app-server-worktree"
            ),
        )
    )

    options = resolution.options
    assert options.worktree is None
    assert options.cwd is not None
    assert options.workspace_roots == [options.cwd]
    assert Repo(options.cwd).active_branch.name == "feat/app-server-worktree"
    assert resolution.prepared_worktree is not None
    assert resolution.prepared_worktree.created is True


def test_session_start_worktree_auto_selection_names_from_prompt(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)

    resolution = resolve_worktree(
        SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=AutoWorktreeInput(prompt="Fix the login bug"),
        )
    )

    options = resolution.options
    assert options.worktree is None
    assert options.cwd is not None
    assert options.workspace_roots == [options.cwd]
    assert resolution.prepared_worktree is not None
    assert resolution.prepared_worktree.name == "fix-the-login-bug"
    assert resolution.prepared_worktree.branch == "vibe/fix-the-login-bug"
    assert resolution.prepared_worktree.created is True
    assert resolution.prepared_worktree.branch_created is True


def test_session_start_worktree_auto_selection_uses_a_suggested_name(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)

    resolution = resolve_worktree(
        SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=AutoWorktreeInput(prompt="Fix the login bug"),
        ),
        "repair-oauth-redirect",
    )

    assert resolution.prepared_worktree is not None
    assert resolution.prepared_worktree.name == "repair-oauth-redirect"
    assert resolution.prepared_worktree.branch == "vibe/repair-oauth-redirect"


@pytest.mark.asyncio
async def test_auto_worktree_asks_the_model_for_a_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    asked: list[str | None] = []

    async def suggest(prompt: str | None, **_kwargs: Any) -> str:
        asked.append(prompt)
        return "repair-oauth-redirect"

    monkeypatch.setattr(server_module, "suggest_worktree_name", suggest)

    suggested = await AppServer._suggest_worktree_name(
        SessionOptions(
            cwd=str(tmp_path), worktree=AutoWorktreeInput(prompt="Fix the login bug")
        )
    )

    assert suggested == "repair-oauth-redirect"
    assert asked == ["Fix the login bug"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "worktree",
    [
        None,
        ExistingWorktreeInput(cwd="/tmp/somewhere"),
        NewWorktreeInput(branch="feat/x", name="feat-x"),
    ],
)
async def test_only_an_auto_worktree_pays_for_a_model_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, worktree: Any
) -> None:
    async def explode(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("a named worktree must not wait on the model")

    monkeypatch.setattr(server_module, "suggest_worktree_name", explode)

    options = SessionOptions(cwd=str(tmp_path), worktree=worktree)

    assert await AppServer._suggest_worktree_name(options) is None


def test_session_start_worktree_auto_selection_is_unique_per_call(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)

    def resolve() -> str:
        resolution = resolve_worktree(
            SessionOptions(
                cwd=str(tmp_path),
                worktree=AutoWorktreeInput(prompt="Fix the login bug"),
            )
        )
        assert resolution.prepared_worktree is not None
        return resolution.prepared_worktree.name

    assert resolve() == "fix-the-login-bug"
    assert resolve() == "fix-the-login-bug-2"


def test_session_start_worktree_auto_selection_without_prompt_uses_a_slug(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    monkeypatch.setattr(worktree_module, "create_slug", lambda: "brave-quiet-otter")

    resolution = resolve_worktree(
        SessionOptions(cwd=str(tmp_path), worktree=AutoWorktreeInput())
    )

    assert resolution.prepared_worktree is not None
    assert resolution.prepared_worktree.name == "brave-quiet-otter"


def test_session_start_worktree_existing_selection_resolves_cwd(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = prepare_worktree_session(
        "existing-worktree", tmp_path, branch="feat/existing-worktree"
    )

    resolution = resolve_worktree(
        SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=ExistingWorktreeInput(cwd=str(worktree.path)),
        )
    )

    options = resolution.options
    assert options.worktree is None
    assert options.cwd == str(worktree.path)
    assert options.workspace_roots == [str(worktree.path)]
    assert resolution.prepared_worktree is None


def test_session_list_falls_back_for_malformed_saved_timestamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_vibe_config()
    monkeypatch.setattr(
        host_module,
        "list_local_resume_sessions",
        lambda _config, _cwd: [
            ResumeSessionInfo(
                session_id="session-1",
                cwd="/workspace",
                start_time="not-a-timestamp",
                updated_at="",
            )
        ],
    )
    monkeypatch.setattr(host_module, "now_ms", lambda: 123_456)
    monkeypatch.setattr(
        host_module.SessionLoader, "get_first_user_message", lambda *_args: ""
    )

    response = host_module.project_session_list(config, SessionListParams())

    assert response.items[0].created_at == 123_456
    assert response.items[0].updated_at == 123_456


@pytest.mark.asyncio
async def test_session_start_rejects_existing_selection_outside_linked_worktrees(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    unlinked = tmp_path / "unlinked"
    unlinked.mkdir()

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        del request
        raise AssertionError("runtime must not open for an unlinked worktree")

    client_transport, server_transport = memory_transport_pair()
    server = AppServer(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)

    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await AppServerSession.start(
                client,
                client_info=ClientInfo(name="unlinked-worktree-client", version="1"),
                capabilities=ClientCapabilities(),
                session_options=SessionOptions(
                    cwd=str(tmp_path), worktree=ExistingWorktreeInput(cwd=str(unlinked))
                ),
            )
    finally:
        await client.close()

    assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert "not linked to the local project" in exc_info.value.error.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "worktree",
    [
        {
            "kind": "create",
            "branch": "feat/rejected-selection",
            "name": "rejected-selection",
        },
        {"kind": "auto"},
    ],
)
@pytest.mark.parametrize(
    ("method", "extra_params"),
    [("session/resume", {"sessionId": "saved-session"}), ("session/continue", {})],
)
async def test_worktree_selection_is_rejected_outside_session_start(
    tmp_path: Path, method: str, extra_params: dict[str, str], worktree: dict[str, str]
) -> None:
    repo = _init_repo(tmp_path)

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        del request
        raise AssertionError(f"{method} must not open a runtime")

    client_transport, server_transport = memory_transport_pair()
    server = AppServer(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="selection-guard-client", version="1"))
    await client.notify("initialized")

    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                method,
                {
                    "agentConfig": {"cwd": str(tmp_path), "worktree": worktree},
                    **extra_params,
                },
            )
    finally:
        await client.close()

    assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert "only supported when starting a session" in exc_info.value.error.message
    assert list_linked_worktrees(tmp_path) == ()
    assert "feat/rejected-selection" not in [head.name for head in repo.heads]


@pytest.mark.asyncio
async def test_session_start_cleans_created_worktree_when_runtime_open_fails(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        assert request.options.cwd is not None
        assert request.options.cwd != str(tmp_path)
        raise runtime.RuntimeConfigurationError("startup failed")

    client_transport, server_transport = memory_transport_pair()
    server = AppServer(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)

    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await AppServerSession.start(
                client,
                client_info=ClientInfo(name="worktree-cleanup-client", version="1"),
                capabilities=ClientCapabilities(),
                session_options=SessionOptions(
                    cwd=str(tmp_path),
                    worktree=NewWorktreeInput(
                        branch="feat/startup-fails", name="startup-fails"
                    ),
                ),
            )
    finally:
        await client.close()

    assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert list_linked_worktrees(tmp_path) == ()
    assert "feat/startup-fails" not in [head.name for head in repo.heads]


@pytest.mark.asyncio
async def test_session_start_cleans_created_worktree_when_cancelled_mid_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    real_resolve = server_module.resolve_worktree

    def slow_resolve(options: SessionOptions, suggested_name: str | None = None) -> Any:
        started.set()
        release.wait(5)
        try:
            return real_resolve(options, suggested_name)
        finally:
            finished.set()

    monkeypatch.setattr(server_module, "resolve_worktree", slow_resolve)

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        raise AssertionError("runtime should not open after cancellation")

    _, server_transport = memory_transport_pair()
    server = AppServer(server_transport, open_root=open_root)
    server._client_info = ClientInfo(name="cancel-client", version="1")

    task = asyncio.create_task(
        server._open_runtime(
            open_root,
            SessionStartParams(
                agent_config=SessionOptions(
                    cwd=str(tmp_path),
                    worktree=NewWorktreeInput(
                        branch="feat/cancelled", name="cancelled"
                    ),
                )
            ),
            None,
        )
    )
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(finished.wait, 5)

    assert list_linked_worktrees(tmp_path) == ()
    assert "feat/cancelled" not in [head.name for head in repo.heads]


@pytest.mark.asyncio
async def test_passive_host_lists_linked_worktrees(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = prepare_worktree_session(
        "host-listed", tmp_path, branch="feat/host-listed"
    )
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "workspace/worktrees/list",
        WorkspaceWorktreeListParams(cwd=str(tmp_path)).model_dump(
            mode="json", by_alias=True
        ),
    )

    response = cast(WorkspaceWorktreeListResponse, result.response)
    assert response.model_dump(mode="json", by_alias=True)["worktrees"] == [
        {
            "name": worktree.name,
            "branch": worktree.branch,
            "cwd": str(worktree.path),
            "root": str(worktree.root),
            "repoRoot": str(worktree.repo_root),
        }
    ]


@pytest.mark.asyncio
async def test_passive_host_lists_no_worktrees_without_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _init_repo(tmp_path)
    prepare_worktree_session("gitless", tmp_path, branch="feat/gitless")

    def missing_git(_base: Path) -> tuple[object, ...]:
        raise GitUnavailableError("Git worktree operations require git.")

    monkeypatch.setattr(host_module, "list_linked_worktrees", missing_git)
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "workspace/worktrees/list",
        WorkspaceWorktreeListParams(cwd=str(tmp_path)).model_dump(
            mode="json", by_alias=True
        ),
    )

    response = cast(WorkspaceWorktreeListResponse, result.response)
    assert response.model_dump(mode="json", by_alias=True) == {"worktrees": []}


@pytest.mark.asyncio
async def test_passive_host_lists_no_worktrees_for_non_git_root(tmp_path: Path) -> None:
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "workspace/worktrees/list",
        WorkspaceWorktreeListParams(cwd=str(tmp_path)).model_dump(
            mode="json", by_alias=True
        ),
    )

    response = cast(WorkspaceWorktreeListResponse, result.response)
    assert response.model_dump(mode="json", by_alias=True) == {"worktrees": []}


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
            "session/rename",
            SessionTitleUpdateParams(
                session_id=saved.session_id, title="  Reviewed session  "
            ).model_dump(mode="json", by_alias=True),
        )
    finally:
        await saved.aclose()
        await saved.telemetry_client.aclose()

    assert isinstance(result.response, SessionTitleUpdateResponse)
    session_path = SessionLoader.find_session_by_id(
        saved.session_id, config.session_logging
    )
    assert session_path is not None
    _, metadata = SessionLoader.load_session(session_path)
    assert result.response.title == "Reviewed session"
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
async def test_invalid_request_returns_validation_issues() -> None:
    async def open_root(_request: runtime.RootOpenRequest) -> AgentLoop:
        raise AssertionError("The request must not open a session")

    client_transport, server_transport = memory_transport_pair()
    server = AppServer(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="validation-test", version="1"))
    await client.notify("initialized")

    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request("session/read", {})
    finally:
        await client.close()

    assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert exc_info.value.error.data == {
        "errorCount": 1,
        "issues": [{"path": ["sessionId"], "message": "Field required"}],
    }


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
    overrides_data: list[dict[str, Any] | None] = []
    local_managed_shell_runtime_policies: list[bool] = []
    sentinel = cast(AgentLoop, object())

    async def load_config(
        data: dict[str, Any] | None = None, *, harness_files: HarnessFilesManager
    ) -> ConfigOrchestrator[VibeConfigSchema]:
        del harness_files
        overrides_data.append(data)
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
        local_managed_shell_runtime_policies.append(
            kwargs["local_managed_shell_runtime_enabled"]
        )
        return sentinel

    monkeypatch.setattr(runtime, "AgentLoop", build_agent_loop)
    process = runtime.HarnessProcess()

    client = ClientInfo(name="test", version="1")
    capabilities = ClientCapabilities(client_tools=["terminal"])
    (await process.build_root_blueprint(SessionOptions(), client, capabilities)).build()
    (await process.build_root_blueprint(SessionOptions(), client)).build()

    assert len(configure_calls) == 1
    assert cache_stores == [process.cache_store, process.cache_store]
    assert local_managed_shell_runtime_policies == [False, True]
    assert overrides_data[0] == {}
    assert overrides_data[1] == {}


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
        assert SessionListResponse.model_validate(listed).data
        await client.request(
            "session/read", SessionReadParams(session_id=saved.session_id)
        )
        await client.request(
            "session/history/list",
            SessionHistoryListParams(session_id=saved.session_id),
        )
        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request("history/list", {"sessionId": saved.session_id})
        assert exc_info.value.error.code is ProtocolErrorCode.METHOD_NOT_FOUND
        await client.request(
            "workspace/trust/status", WorkspaceTrustStatusParams(cwd=str(Path.cwd()))
        )
        assert opened == []
        assert config_loads and all(required is False for required in config_loads)

        deleted = await client.request(
            "session/delete", SessionDeleteParams(session_id=saved.session_id)
        )
        assert isinstance(EmptyResponse.model_validate(deleted), EmptyResponse)
        assert opened == []

        await client.request(
            "session/start",
            SessionStartParams(agent_config=SessionOptions(cwd=str(Path.cwd()))),
        )
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
async def test_config_read_serves_the_catalogue_with_and_without_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host_config = build_test_vibe_config(
        models=[
            ModelConfig(
                name="devstral-medium-latest",
                provider="mistral",
                alias="medium",
                thinking="max",
            ),
            ModelConfig(
                name="devstral-small-latest", provider="mistral", alias="small"
            ),
        ]
    )
    root = build_test_agent_loop()
    workspace = Path("/workspace/project").resolve()
    layer_roots: list[Path | None] = []

    async def load_config(
        data: dict[str, Any] | None = None,
        *,
        harness_files: HarnessFilesManager,
        require_api_key: bool,
    ) -> ConfigOrchestrator[VibeConfigSchema]:
        del data, require_api_key
        layer_roots.append(harness_files.cwd)
        return FakeConfigOrchestrator(host_config)

    monkeypatch.setattr("vibe.app_server._host.build_default_orchestrator", load_config)
    opened: list[runtime.RootOpenRequest] = []

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        opened.append(request)
        return root

    client_transport, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport,
        open_root=open_root,
        host_handler=HostRequestHandler(HarnessFilesManager(sources=())),
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="config-client", version="1"))
    await client.notify("initialized")

    try:
        passive = validate_wire(
            ConfigReadResponse,
            await client.request("config/read", ConfigReadParams(cwd=str(workspace))),
        )
        assert opened == []
        assert passive.config == project_config_view(
            host_config, active_model_pinned=bool(host_config.active_model)
        )
        assert passive.stripped_history_images == 0
        assert [model.alias for model in passive.config.models] == ["medium", "small"]
        assert passive.config.active_model.thinking == "max"
        assert layer_roots == [workspace]

        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                "config/read", ConfigReadParams(session_id="not-this-session")
            )
        assert exc_info.value.error.code is ProtocolErrorCode.NOT_FOUND
        assert opened == []

        await client.request(
            "session/start",
            SessionStartParams(agent_config=SessionOptions(cwd=str(Path.cwd()))),
        )
        attached = validate_wire(
            ConfigReadResponse,
            await client.request(
                "config/read", ConfigReadParams(session_id=root.session_id)
            ),
        )
        assert attached.config == project_config(root)
        assert [model.alias for model in attached.config.models] != ["medium", "small"]

        omitted = validate_wire(
            ConfigReadResponse,
            await client.request("config/read", ConfigReadParams(cwd=str(workspace))),
        )
        assert omitted == passive
        assert omitted.config != attached.config
        assert layer_roots == [workspace, workspace]

        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                "config/read", ConfigReadParams(session_id="not-this-session")
            )
        assert exc_info.value.error.code is ProtocolErrorCode.NOT_FOUND
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_config_fields_read_hides_internal_settings() -> None:
    root = build_test_agent_loop(
        config=build_test_vibe_config(managed_shell_tools_enabled=True)
    )

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        return root

    client_transport, server_transport = memory_transport_pair()
    server = AppServer(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="config-fields-client", version="1"))
    await client.notify("initialized")

    try:
        await client.request(
            "session/start",
            SessionStartParams(agent_config=SessionOptions(cwd=str(Path.cwd()))),
        )
        response = validate_wire(
            ConfigFieldsReadResponse,
            await client.request(
                "config/fields/read", ConfigFieldsReadParams(session_id=root.session_id)
            ),
        )
    finally:
        await client.close()

    names = {field.name for field in response.fields}
    assert "default_agent" in names
    assert "managed_shell_tools_enabled" not in names
    assert "tools" not in names


@pytest.mark.asyncio
async def test_config_mutations_are_rejected_without_a_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_vibe_config()

    async def load_config(
        data: dict[str, Any] | None = None,
        *,
        harness_files: HarnessFilesManager,
        require_api_key: bool,
    ) -> ConfigOrchestrator[VibeConfigSchema]:
        del data, harness_files, require_api_key
        return FakeConfigOrchestrator(config)

    monkeypatch.setattr("vibe.app_server._host.build_default_orchestrator", load_config)
    opened: list[runtime.RootOpenRequest] = []

    root = build_test_agent_loop()

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        opened.append(request)
        return root

    client_transport, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport,
        open_root=open_root,
        host_handler=HostRequestHandler(HarnessFilesManager(sources=())),
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="mutation-client", version="1"))
    await client.notify("initialized")
    session_scoped = (
        ("config/write", {"ops": []}),
        ("config/reload", {}),
        ("config/proxy/read", {}),
        ("config/proxy/write", {"changes": {}}),
        ("config/fields/read", {}),
    )

    try:
        for method, params in session_scoped:
            with pytest.raises(AppServerResponseError) as exc_info:
                await client.request(method, params)
            assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT, method
        assert opened == []

        await client.request(
            "session/start",
            SessionStartParams(agent_config=SessionOptions(cwd=str(Path.cwd()))),
        )
        for method, params in session_scoped:
            with pytest.raises(AppServerResponseError) as exc_info:
                await client.request(method, params)
            assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS, method
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
