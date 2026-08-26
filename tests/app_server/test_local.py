from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

from git import Repo
import pytest
import tomli_w

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.app_server import legacy_backend
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server import _runtime as runtime
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._execution import SessionExecutionKind
import vibe.app_server._host as host_module
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._legacy_composition import create_legacy_app_server
from vibe.app_server._legacy_session_backend import LegacySessionBackendHost
import vibe.app_server._legacy_session_runtime as legacy_runtime_module
from vibe.app_server._legacy_session_runtime import LegacySessionRuntimeController
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._projection import (
    project_config,
    project_config_view,
    project_message_history,
)
from vibe.app_server._projector import ProjectedUpdate
import vibe.app_server._sessions as _sessions
from vibe.app_server._turns import TurnController
from vibe.app_server.client import AppServerClient
from vibe.app_server.models import PublicHistoryEntry
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
    HistoryEntryUpdatedParams,
    NewWorktreeInput,
    ProtocolErrorCode,
    RuntimeReadParams,
    RuntimeReadResponse,
    SessionDeleteParams,
    SessionHistoryListParams,
    SessionListParams,
    SessionListResponse,
    SessionMCPStdioServer,
    SessionOptions,
    SessionReadParams,
    SessionResumeParams,
    SessionStartParams,
    SessionTitleUpdateParams,
    SessionTitleUpdateResponse,
    WorkspaceGitCheckoutsParams,
    WorkspaceGitCheckoutsResponse,
    WorkspaceTrustStatusParams,
    WorkspaceWorktreeListParams,
    WorkspaceWorktreeListResponse,
    WorkspaceWorktreeRemoveResponse,
)
from vibe.app_server.session import AppServerSession
from vibe.app_server.transport import memory_transport_pair
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import ModelConfig, SessionLoggingConfig, VibeConfigSchema
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.git.errors import GitUnavailableError
from vibe.core.git.worktree import (
    LinkedWorktree,
    ManagedWorktree,
    PreparedWorktree,
    WorktreeReleaseOutcome,
    WorktreeRepository,
)
import vibe.core.git.worktree.record as worktree_record
import vibe.core.git.worktree.repository as worktree_module
from vibe.core.hooks.config import HookConfigResult
from vibe.core.session.resume_sessions import ResumeSessionInfo
from vibe.core.session.session_lease import SessionBusyError, SessionLease
from vibe.core.session.session_loader import SessionLoader
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.utils.terminal import TerminalEmulator


class _FakeSessionBackendServices:
    def client_info(self) -> ClientInfo:
        return ClientInfo(name="test", version="1")

    def client_capabilities(self) -> ClientCapabilities:
        return ClientCapabilities()

    def current_session_id(self) -> str:
        return "root"

    def event_watermark(self, session_id: str) -> int:
        return 0

    def account_gateway(self) -> None:
        return None

    def identity_gateway(self) -> None:
        return None

    @asynccontextmanager
    async def lifecycle_transition(self):
        yield

    def task_finished(self, task: asyncio.Task[None]) -> None:
        pass

    async def notify(self, method: str, params: Any) -> None:
        pass

    async def publish_callback(self, callback: Any) -> None:
        pass

    async def record_child_notification(self, method: str, params: Any) -> None:
        pass

    async def request_client_result[ResultT: ProtocolModel](
        self, method: str, params: ProtocolModel, response_type: type[ResultT]
    ) -> ResultT:
        raise AssertionError("test services do not serve client requests")


def test_local_harness_options_preserves_client_positional_argument() -> None:
    client = runtime.ClientDescriptor(info=ClientInfo(name="test", version="1"))

    options = runtime.LocalHarnessOptions(client)

    assert options.client is client
    assert options.experimental_harness is False


@pytest.mark.asyncio
async def test_first_session_open_holds_the_lifecycle_lock() -> None:
    root = build_test_agent_loop()
    lock_states: list[bool] = []

    async def open_root(_request: runtime.RootOpenRequest) -> AgentLoop:
        lock_states.append(server._lifecycle_lock.locked())
        return root

    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="test", version="1"))
    await client.notify("initialized")
    try:
        await client.request("session/start", SessionStartParams())
        host = server._session_backend_host
        assert isinstance(host, LegacySessionBackendHost)
        original_resume = host._resume
        assert original_resume is not None
        event_task = server._backend_event_task

        async def resume(params: SessionResumeParams):
            lock_states.append(server._lifecycle_lock.locked())
            return await original_resume(params)

        host._resume = resume
        await client.request(
            "session/resume", SessionResumeParams(session_id=root.session_id)
        )

        assert lock_states == [True, True]
        assert server._backend_event_task is event_task
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["config/write", "config/reload"])
async def test_busy_config_mutations_return_conflict(method: str) -> None:
    root = build_test_agent_loop()

    async def open_root(_request: runtime.RootOpenRequest) -> AgentLoop:
        return root

    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="test", version="1"))
    await client.notify("initialized")
    try:
        await client.request("session/start", SessionStartParams())
        backend = legacy_backend(server)
        params: dict[str, Any] = {"sessionId": root.session_id}
        if method == "config/write":
            params["ops"] = []
        with backend.session.execution.reserve(SessionExecutionKind.TURN, "turn-1"):
            with pytest.raises(AppServerResponseError) as exc_info:
                await client.request(method, params)
        assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
    finally:
        await client.close()


# The API is repository-scoped; these keep a test that only cares about the
# resulting worktree down to a single call.


def _prepare(name: str, base: Path, *, branch: str | None = None) -> PreparedWorktree:
    with WorktreeRepository.open(base) as repository:
        return repository.prepare(name, branch=branch)


def _prepare_auto(base: Path, *, prompt: str | None = None) -> PreparedWorktree:
    with WorktreeRepository.open(base) as repository:
        return repository.prepare_auto(prompt=prompt)


def _linked(base: Path) -> tuple[LinkedWorktree, ...]:
    with WorktreeRepository.open(base) as repository:
        return repository.linked()


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

    resolution = legacy_runtime_module.resolve_worktree(
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

    resolution = legacy_runtime_module.resolve_worktree(
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

    resolution = legacy_runtime_module.resolve_worktree(
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

    monkeypatch.setattr(legacy_runtime_module, "suggest_worktree_name", suggest)

    suggested = await LegacySessionRuntimeController._suggest_worktree_name(
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

    monkeypatch.setattr(legacy_runtime_module, "suggest_worktree_name", explode)

    options = SessionOptions(cwd=str(tmp_path), worktree=worktree)

    assert await LegacySessionRuntimeController._suggest_worktree_name(options) is None


def test_session_start_worktree_auto_selection_is_unique_per_call(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)

    def resolve() -> str:
        resolution = legacy_runtime_module.resolve_worktree(
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

    resolution = legacy_runtime_module.resolve_worktree(
        SessionOptions(cwd=str(tmp_path), worktree=AutoWorktreeInput())
    )

    assert resolution.prepared_worktree is not None
    assert resolution.prepared_worktree.name == "brave-quiet-otter"


def test_session_start_worktree_existing_selection_resolves_cwd(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = _prepare("existing-worktree", tmp_path, branch="feat/existing-worktree")

    resolution = legacy_runtime_module.resolve_worktree(
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


def _stub_session_list_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        host_module,
        "list_local_resume_sessions",
        lambda _config, _cwd: [
            ResumeSessionInfo(
                session_id="newer", cwd="/workspace", updated_at="2026-01-02"
            ),
            ResumeSessionInfo(
                session_id="older", cwd="/workspace", updated_at="2026-01-01"
            ),
        ],
    )
    monkeypatch.setattr(
        host_module.SessionLoader, "get_first_user_message", lambda *_args: ""
    )


def test_session_list_continue_id_prefers_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_vibe_config()
    _stub_session_list_sources(monkeypatch)
    monkeypatch.setattr(
        host_module.last_session_pointer, "load", lambda _config: "older"
    )

    response = host_module.project_session_list(config, SessionListParams())

    assert response.continue_session_id == "older"


def test_session_list_continue_id_falls_back_to_newest_when_pointer_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_vibe_config()
    _stub_session_list_sources(monkeypatch)
    monkeypatch.setattr(host_module.last_session_pointer, "load", lambda _config: None)

    response = host_module.project_session_list(config, SessionListParams())

    assert response.continue_session_id == "newer"


def test_session_list_continue_id_ignores_stale_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_vibe_config()
    _stub_session_list_sources(monkeypatch)
    monkeypatch.setattr(
        host_module.last_session_pointer, "load", lambda _config: "does-not-exist"
    )

    response = host_module.project_session_list(config, SessionListParams())

    assert response.continue_session_id == "newer"


def test_session_list_continue_id_accepts_forked_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_vibe_config()
    monkeypatch.setattr(
        host_module,
        "list_local_resume_sessions",
        lambda _config, _cwd: [
            ResumeSessionInfo(
                session_id="root", cwd="/workspace", updated_at="2026-01-02"
            ),
            ResumeSessionInfo(
                session_id="fork",
                cwd="/workspace",
                updated_at="2026-01-01",
                parent_session_id="root",
            ),
        ],
    )
    monkeypatch.setattr(
        host_module.SessionLoader, "get_first_user_message", lambda *_args: ""
    )
    monkeypatch.setattr(
        host_module.last_session_pointer, "load", lambda _config: "fork"
    )

    response = host_module.project_session_list(config, SessionListParams())

    assert response.continue_session_id == "fork"


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
    server = create_legacy_app_server(server_transport, open_root=open_root)
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
    server = create_legacy_app_server(server_transport, open_root=open_root)
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
    assert _linked(tmp_path) == ()
    assert "feat/rejected-selection" not in [head.name for head in repo.heads]


async def _open_local_session(
    name: str, session_options: SessionOptions
) -> tuple[AppServerSession, AppServerClient]:
    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        assert request.options.cwd is not None
        return build_test_agent_loop(cwd=Path(request.options.cwd))

    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)
    session = await AppServerSession.start(
        client,
        client_info=ClientInfo(name=name, version="1"),
        capabilities=ClientCapabilities(),
        session_options=session_options,
    )
    return session, client


@pytest.mark.asyncio
async def test_session_stop_removes_a_clean_auto_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    session, client = await _open_local_session(
        "release-client",
        SessionOptions(
            cwd=str(tmp_path), worktree=AutoWorktreeInput(prompt="fix the login bug")
        ),
    )
    worktree_root = Path(session.cwd)
    assert worktree_root.is_dir()

    try:
        await session.close()
    finally:
        await client.close()

    assert not worktree_root.exists()
    assert _linked(tmp_path) == ()
    assert "vibe/fix-the-login-bug" not in [head.name for head in repo.heads]


@pytest.mark.asyncio
async def test_created_worktree_is_reported_in_the_transcript(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    session, client = await _open_local_session(
        "reporting-client",
        SessionOptions(
            cwd=str(tmp_path), worktree=AutoWorktreeInput(prompt="fix the login bug")
        ),
    )
    try:
        effects = [entry for entry in session.history if entry.type == "effect"]
        assert len(effects) == 1
        detail = effects[0].detail
        assert detail.kind == "worktree"
        assert detail.input is not None
        # Name, branch and path together: a managed worktree lives under
        # $VIBE_HOME, so the name alone does not say where it landed.
        assert detail.input.name == "fix-the-login-bug"
        assert detail.input.branch == "vibe/fix-the-login-bug"
        assert detail.input.path == session.cwd
        assert effects[0].state.status == "completed"
    finally:
        await session.close()
        await client.close()


@pytest.mark.asyncio
async def test_a_dropped_notification_still_retires_the_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    emit = TurnController._emit_projected

    async def drop_the_completion(
        self: TurnController, update: ProjectedUpdate
    ) -> None:
        # Starting an effect adds an entry; only completing one patches it.
        if isinstance(update.params, HistoryEntryUpdatedParams):
            raise RuntimeError("the client went away")
        await emit(self, update)

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        assert request.options.cwd is not None
        return build_test_agent_loop(cwd=Path(request.options.cwd))

    monkeypatch.setattr(TurnController, "_emit_projected", drop_the_completion)
    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)
    session = await AppServerSession.start(
        client,
        client_info=ClientInfo(name="dropped-client", version="1"),
        capabilities=ClientCapabilities(),
        session_options=SessionOptions(
            cwd=str(tmp_path), worktree=AutoWorktreeInput(prompt="fix the login bug")
        ),
    )
    try:
        effects = [entry for entry in session.history if entry.type == "effect"]
        assert len(effects) == 1
        assert effects[0].state.status == "completed"
        # Losing the notification costs the live update and nothing else. Still
        # registered, the effect would be served from the live bucket, which
        # sorts after every entry that follows - so the creation would drift to
        # the bottom of the transcript as soon as the user said anything.
        assert legacy_backend(server).session.turns._harness_effects == {}
    finally:
        await session.close()
        await client.close()


@pytest.mark.asyncio
async def test_a_dropped_notification_still_survives_a_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    emit = TurnController._emit_projected
    loops: list[AgentLoop] = []
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path / "sessions")
        )
    )

    async def drop_the_completion(
        self: TurnController, update: ProjectedUpdate
    ) -> None:
        if isinstance(update.params, HistoryEntryUpdatedParams):
            raise RuntimeError("the client went away")
        await emit(self, update)

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        assert request.options.cwd is not None
        agent_loop = build_test_agent_loop(cwd=Path(request.options.cwd), config=config)
        loops.append(agent_loop)
        return agent_loop

    monkeypatch.setattr(TurnController, "_emit_projected", drop_the_completion)
    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)
    session = await AppServerSession.start(
        client,
        client_info=ClientInfo(name="dropped-resume-client", version="1"),
        capabilities=ClientCapabilities(),
        session_options=SessionOptions(
            cwd=str(tmp_path), worktree=AutoWorktreeInput(prompt="fix the login bug")
        ),
    )
    try:
        # The live entry is cosmetic; the persisted metadata is the only thing a
        # resume rebuilds from. Losing the completion notification must not cost
        # the durable record too, or the worktree disappears from the transcript
        # in exactly the failure this notification path is allowed to have.
        agent_loop = loops[0]
        await agent_loop.persist_empty_session()
        session_dir = agent_loop.session_logger.session_dir
        assert session_dir is not None
    finally:
        await session.close()
        await client.close()

    rebuilt = _rebuild_from_disk(agent_loop.session_id, session_dir)
    effects = [entry for entry in rebuilt if entry.type == "effect"]
    assert len(effects) == 1
    assert effects[0].detail.kind == "worktree"


def _rebuild_from_disk(session_id: str, session_dir: Path) -> list[PublicHistoryEntry]:
    """Rebuild a transcript the way a resume does: from the files, not memory."""
    messages, _ = SessionLoader.load_session(session_dir)
    return project_message_history(
        session_id, messages, SessionLoader.load_metadata(session_dir)
    )


@pytest.mark.asyncio
async def test_the_worktree_entry_survives_a_resume(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    loops: list[AgentLoop] = []
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path / "sessions")
        )
    )

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        assert request.options.cwd is not None
        agent_loop = build_test_agent_loop(cwd=Path(request.options.cwd), config=config)
        loops.append(agent_loop)
        return agent_loop

    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)
    session = await AppServerSession.start(
        client,
        client_info=ClientInfo(name="resume-client", version="1"),
        capabilities=ClientCapabilities(),
        session_options=SessionOptions(
            cwd=str(tmp_path), worktree=AutoWorktreeInput(prompt="fix the login bug")
        ),
    )
    try:
        # The metadata file does not exist yet when the worktree is recorded, so
        # the field patch has nothing to write into: this full save is what
        # first carries the worktree to disk.
        agent_loop = loops[0]
        await agent_loop.persist_empty_session()
        session_dir = agent_loop.session_logger.session_dir
        assert session_dir is not None
    finally:
        await session.close()
        await client.close()

    # A harness effect living only in the TurnController is gone by here, and
    # the runner reclaims the process a second after every turn - so the round
    # trip through the file is the whole guarantee.
    assert SessionLoader.load_metadata(session_dir).created_worktree is not None

    rebuilt = _rebuild_from_disk(agent_loop.session_id, session_dir)
    effects = [entry for entry in rebuilt if entry.type == "effect"]
    assert len(effects) == 1
    assert effects[0].detail.kind == "worktree"
    assert effects[0].detail.input is not None
    assert effects[0].detail.input.name == "fix-the-login-bug"
    # The worktree exists before anything else the transcript can show.
    assert rebuilt[0] is effects[0]


@pytest.mark.asyncio
async def test_the_worktree_is_not_the_session_preview(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    session, client = await _open_local_session(
        "preview-client",
        SessionOptions(
            cwd=str(tmp_path), worktree=AutoWorktreeInput(prompt="fix the login bug")
        ),
    )
    try:
        # The preview is the first thing the user typed. Recording the worktree
        # must never put a message in front of that - the previous attempt did,
        # by injecting a user-role notice the user never wrote.
        assert session.state.session.preview == ""
    finally:
        await session.close()
        await client.close()


@pytest.mark.asyncio
async def test_a_session_without_a_worktree_reports_nothing(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    session, client = await _open_local_session(
        "plain-client", SessionOptions(cwd=str(tmp_path))
    )
    try:
        assert [entry for entry in session.history if entry.type == "effect"] == []
    finally:
        await session.close()
        await client.close()


@pytest.mark.asyncio
async def test_opening_an_existing_worktree_reports_nothing(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = _prepare("existing-worktree", tmp_path, branch="feat/existing-worktree")
    session, client = await _open_local_session(
        "existing-client",
        SessionOptions(
            cwd=str(tmp_path), worktree=ExistingWorktreeInput(cwd=str(worktree.path))
        ),
    )
    try:
        # Only a worktree this session brought into being is worth a transcript
        # entry. Moving into one that was already there is not a mutation, and
        # reporting it would claim credit for the user's own directory.
        assert [entry for entry in session.history if entry.type == "effect"] == []
    finally:
        await session.close()
        await client.close()


@pytest.mark.asyncio
async def test_session_stop_keeps_an_auto_worktree_holding_work(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    session, client = await _open_local_session(
        "dirty-client",
        SessionOptions(
            cwd=str(tmp_path), worktree=AutoWorktreeInput(prompt="fix the login bug")
        ),
    )
    worktree_root = Path(session.cwd)
    (worktree_root / "written-by-the-agent.txt").write_text("work\n")

    try:
        await session.close()
    finally:
        await client.close()

    assert worktree_root.is_dir()
    assert "vibe/fix-the-login-bug" in [head.name for head in repo.heads]


@pytest.mark.asyncio
async def test_session_stop_releases_the_holder_before_shutting_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    loops: list[AgentLoop] = []
    order: list[str] = []

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        assert request.options.cwd is not None
        agent_loop = build_test_agent_loop(cwd=Path(request.options.cwd))
        loops.append(agent_loop)
        return agent_loop

    release_holder = ManagedWorktree.release_holder
    close_agent_loop = _sessions.close_agent_loop

    def record_release(self: ManagedWorktree, session_id: str) -> None:
        order.append("holder")
        release_holder(self, session_id)

    async def record_close(agent_loop: AgentLoop) -> None:
        order.append("close")
        await close_agent_loop(agent_loop)

    monkeypatch.setattr(ManagedWorktree, "release_holder", record_release)
    monkeypatch.setattr(_sessions, "close_agent_loop", record_close)

    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)
    session = await AppServerSession.start(
        client,
        client_info=ClientInfo(name="idle-client", version="1"),
        capabilities=ClientCapabilities(),
        session_options=SessionOptions(
            cwd=str(tmp_path), worktree=AutoWorktreeInput(prompt="fix the login bug")
        ),
    )
    loops[0].session_logger._persisted = True

    try:
        await session.close()
    finally:
        await client.close()

    # The runner caps session/stop at two seconds and then deletes regardless.
    # A holder released after a slow shutdown is still on disk when the delete
    # asks for removal, which reads the worktree as in use and strands it with
    # its session already gone.
    assert order == ["holder", "close"]


@pytest.mark.asyncio
async def test_a_failed_holder_release_still_shuts_the_session_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    closed: list[str] = []

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        assert request.options.cwd is not None
        return build_test_agent_loop(cwd=Path(request.options.cwd))

    close_agent_loop = _sessions.close_agent_loop

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise OSError("the holder marker could not be unlinked")

    async def record_close(agent_loop: AgentLoop) -> None:
        closed.append(agent_loop.session_id)
        await close_agent_loop(agent_loop)

    monkeypatch.setattr(ManagedWorktree, "release_holder", refuse)
    monkeypatch.setattr(_sessions, "close_agent_loop", record_close)

    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)
    session = await AppServerSession.start(
        client,
        client_info=ClientInfo(name="idle-client", version="1"),
        capabilities=ClientCapabilities(),
        session_options=SessionOptions(
            cwd=str(tmp_path), worktree=AutoWorktreeInput(prompt="fix the login bug")
        ),
    )

    try:
        await session.close()
    finally:
        await client.close()

    # Releasing the holder is the first step of the close now, so letting it
    # throw would skip every step after it - the MCP clients and terminals
    # session.close() shuts down, and the rollback of a failed create.
    assert closed


@pytest.mark.asyncio
async def test_session_stop_keeps_the_worktree_of_a_session_that_ran_a_turn(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    loops: list[AgentLoop] = []

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        assert request.options.cwd is not None
        agent_loop = build_test_agent_loop(cwd=Path(request.options.cwd))
        loops.append(agent_loop)
        return agent_loop

    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)
    session = await AppServerSession.start(
        client,
        client_info=ClientInfo(name="idle-client", version="1"),
        capabilities=ClientCapabilities(),
        session_options=SessionOptions(
            cwd=str(tmp_path), worktree=AutoWorktreeInput(prompt="fix the login bug")
        ),
    )
    worktree_root = Path(session.cwd)
    # The runner sends session/stop a second after every turn to reclaim the
    # app-server process; the session stays live and resumable. persisted is
    # what records that a turn ran, and it is the only thing standing between an
    # idle session and losing the worktree it is still working in.
    loops[0].session_logger._persisted = True

    try:
        await session.close()
    finally:
        await client.close()

    assert worktree_root.is_dir()
    assert "vibe/fix-the-login-bug" in [head.name for head in repo.heads]


@pytest.mark.asyncio
async def test_session_stop_keeps_a_worktree_the_session_did_not_create(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    # Opened in a worktree that already existed. Rolling back a failed create
    # must never reach a worktree this session has no claim on.
    worktree = _prepare_auto(tmp_path)
    session, client = await _open_local_session(
        "adopting-client", SessionOptions(cwd=str(worktree.path))
    )

    try:
        await session.close()
    finally:
        await client.close()

    assert worktree.root.is_dir()


@pytest.mark.asyncio
async def test_session_start_does_not_sweep_a_stale_claim_it_is_using(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    claim = worktree_record.WorktreeClaim.locate(worktree.root)
    assert claim is not None
    record = claim.read()
    assert record is not None
    claim.write(
        record.model_copy(
            update={"claimed_at": record.claimed_at - timedelta(minutes=30)}
        )
    )

    session, client = await _open_local_session(
        "stale-claim-client", SessionOptions(cwd=str(worktree.path))
    )
    try:
        await asyncio.sleep(0.1)
    finally:
        await session.close()
        await client.close()

    assert worktree.root.is_dir()


async def _remove_worktree_over_rpc(
    cwd: Path, *, attach_root: Path | None = None
) -> WorkspaceWorktreeRemoveResponse:
    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        assert request.options.cwd is not None
        return build_test_agent_loop(cwd=Path(request.options.cwd))

    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)
    session = None
    if attach_root is None:
        await client.initialize(ClientInfo(name="remove-client", version="1"))
        await client.notify("initialized")
    else:
        session = await AppServerSession.start(
            client,
            client_info=ClientInfo(name="attached-remove-client", version="1"),
            capabilities=ClientCapabilities(),
            session_options=SessionOptions(cwd=str(attach_root)),
        )

    try:
        raw = await client.request("workspace/git/worktrees/remove", {"cwd": str(cwd)})
    finally:
        if session is not None:
            await session.close()
        await client.close()
    return validate_wire(WorkspaceWorktreeRemoveResponse, raw)


@pytest.mark.asyncio
async def test_worktree_remove_deletes_a_clean_managed_worktree(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)

    response = await _remove_worktree_over_rpc(worktree.root)

    assert response.outcome == WorktreeReleaseOutcome.REMOVED
    assert response.branch_deleted is True
    assert response.root == str(worktree.root)
    assert not worktree.root.exists()
    assert worktree.branch not in [head.name for head in repo.heads]


@pytest.mark.asyncio
async def test_worktree_remove_keeps_a_worktree_holding_work(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    (worktree.root / "unsaved.txt").write_text("work\n")

    response = await _remove_worktree_over_rpc(worktree.root)

    assert response.outcome == WorktreeReleaseOutcome.KEPT_DIRTY
    assert response.reasons == ["untracked files"]
    assert worktree.root.is_dir()


@pytest.mark.asyncio
async def test_worktree_remove_ignores_an_unmanaged_worktree(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    response = await _remove_worktree_over_rpc(tmp_path)

    assert response.outcome == WorktreeReleaseOutcome.KEPT_UNMANAGED
    assert tmp_path.is_dir()


@pytest.mark.asyncio
async def test_worktree_remove_forgets_a_worktree_already_gone(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    repo.git.worktree("remove", "--force", str(worktree.root))

    response = await _remove_worktree_over_rpc(worktree.root)

    assert response.outcome == WorktreeReleaseOutcome.NOT_FOUND


@pytest.mark.asyncio
async def test_worktree_remove_is_reachable_while_a_root_is_attached(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)

    response = await _remove_worktree_over_rpc(worktree.root, attach_root=tmp_path)

    assert response.outcome == WorktreeReleaseOutcome.REMOVED
    assert not worktree.root.exists()


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
    server = create_legacy_app_server(server_transport, open_root=open_root)
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
    assert _linked(tmp_path) == ()
    assert "feat/startup-fails" not in [head.name for head in repo.heads]
    # The record has to go with the worktree. The sweep only reclaims claims
    # with no base commit, so one left here would outlive what it describes.
    bucket = worktree_record.managed_bucket_name(tmp_path, tmp_path / ".git")
    assert (
        worktree_record.WorktreeClaim(bucket=bucket, name="startup-fails").read()
        is None
    )


@pytest.mark.asyncio
async def test_session_start_cleans_created_worktree_when_cancelled_mid_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _init_repo(tmp_path)
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    real_resolve = legacy_runtime_module.resolve_worktree

    def slow_resolve(options: SessionOptions, suggested_name: str | None = None) -> Any:
        started.set()
        release.wait(5)
        try:
            return real_resolve(options, suggested_name)
        finally:
            finished.set()

    monkeypatch.setattr(legacy_runtime_module, "resolve_worktree", slow_resolve)

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        raise AssertionError("runtime should not open after cancellation")

    services = _FakeSessionBackendServices()
    controller = LegacySessionRuntimeController(
        open_root=open_root,
        runtime_factory=runtime.AgentRuntimeFactory(),
        host_handler=HostRequestHandler(HarnessFilesManager(sources=())),
        stage_root=None,
        services=services,
    )

    task = asyncio.create_task(
        controller._open_runtime(
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

    assert _linked(tmp_path) == ()
    assert "feat/cancelled" not in [head.name for head in repo.heads]


@pytest.mark.asyncio
async def test_open_runtime_maps_git_errors_to_invalid_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        raise AssertionError("runtime should not open after worktree failure")

    services = _FakeSessionBackendServices()
    controller = LegacySessionRuntimeController(
        open_root=open_root,
        runtime_factory=runtime.AgentRuntimeFactory(),
        host_handler=HostRequestHandler(HarnessFilesManager(sources=())),
        stage_root=None,
        services=services,
    )

    async def fail_resolve(_options: SessionOptions) -> Any:
        raise GitUnavailableError("git unavailable")

    monkeypatch.setattr(controller, "_resolve_worktree", fail_resolve)

    with pytest.raises(RequestFailure) as exc_info:
        await controller._open_runtime(
            SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path))), None
        )

    assert exc_info.value.code is ProtocolErrorCode.INVALID_PARAMS
    assert str(exc_info.value) == "git unavailable"


@pytest.mark.asyncio
async def test_scheduler_failure_does_not_reach_app_server_task_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Services(_FakeSessionBackendServices):
        def __init__(self) -> None:
            self.finished_tasks: list[asyncio.Task[None]] = []

        def task_finished(self, task: asyncio.Task[None]) -> None:
            self.finished_tasks.append(task)

    async def open_root(request: runtime.RootOpenRequest) -> AgentLoop:
        raise AssertionError("runtime should not open")

    services = Services()
    controller = LegacySessionRuntimeController(
        open_root=open_root,
        runtime_factory=runtime.AgentRuntimeFactory(),
        host_handler=HostRequestHandler(HarnessFilesManager(sources=())),
        stage_root=None,
        services=services,
    )

    async def fail_scheduler() -> None:
        raise RuntimeError("scheduler failed")

    monkeypatch.setattr(controller, "_run_scheduler", fail_scheduler)

    controller._ensure_scheduler()
    await asyncio.sleep(0)

    assert services.finished_tasks == []
    assert controller._scheduler_task is not None
    assert controller._scheduler_task.done()


@pytest.mark.asyncio
async def test_passive_host_lists_linked_worktrees(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = _prepare("host-listed", tmp_path, branch="feat/host-listed")
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "workspace/git/worktrees/list",
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
            # Absent unless the caller asked for details, which this did not.
            "branchChanges": None,
        }
    ]


@pytest.mark.asyncio
async def test_passive_host_reports_the_repository_counterpart(tmp_path: Path) -> None:
    """Where a session listing from a subdirectory would stand in the main checkout."""
    _init_repo(tmp_path)
    worktree = _prepare("counterpart", tmp_path, branch="feat/counterpart")
    (tmp_path / "packages" / "api").mkdir(parents=True)
    (worktree.root / "packages" / "api").mkdir(parents=True)
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "workspace/git/worktrees/list",
        WorkspaceWorktreeListParams(
            cwd=str(worktree.root / "packages" / "api")
        ).model_dump(mode="json", by_alias=True),
    )

    response = cast(WorkspaceWorktreeListResponse, result.response)
    assert response.repository_cwd == str(
        (tmp_path / "packages" / "api").resolve(strict=True)
    )


@pytest.mark.asyncio
async def test_passive_host_reports_no_counterpart_outside_the_main_checkout(
    tmp_path: Path,
) -> None:
    """A directory that exists only on a feature branch is not a destination.

    The worktree holding it is still listed; it is the main checkout that has
    nowhere for the session to stand, and saying so is what keeps a caller from
    offering a move the relocation would refuse.
    """
    _init_repo(tmp_path)
    worktree = _prepare("only-here", tmp_path, branch="feat/only-here")
    (worktree.root / "packages" / "api").mkdir(parents=True)
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "workspace/git/worktrees/list",
        WorkspaceWorktreeListParams(
            cwd=str(worktree.root / "packages" / "api")
        ).model_dump(mode="json", by_alias=True),
    )

    response = cast(WorkspaceWorktreeListResponse, result.response)
    assert response.repository_cwd is None
    assert [entry.cwd for entry in response.worktrees] == [
        str(worktree.root / "packages" / "api")
    ]


@pytest.mark.asyncio
async def test_passive_host_lists_no_worktrees_without_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _init_repo(tmp_path)
    _prepare("gitless", tmp_path, branch="feat/gitless")

    def missing_git(_repository: WorktreeRepository) -> tuple[object, ...]:
        raise GitUnavailableError("Git worktree operations require git.")

    monkeypatch.setattr(WorktreeRepository, "linked", missing_git)
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "workspace/git/worktrees/list",
        WorkspaceWorktreeListParams(cwd=str(tmp_path)).model_dump(
            mode="json", by_alias=True
        ),
    )

    response = cast(WorkspaceWorktreeListResponse, result.response)
    assert response.model_dump(mode="json", by_alias=True) == {
        "worktrees": [],
        "repositoryBranch": None,
        "repositoryCwd": None,
    }


@pytest.mark.asyncio
async def test_passive_host_reads_the_main_checkout(tmp_path: Path) -> None:
    """The main checkout is absent from the worktree listing by design."""
    _init_repo(tmp_path)
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "workspace/git/checkouts",
        WorkspaceGitCheckoutsParams(
            repo_local_paths=[str(tmp_path)], session_cwd=str(tmp_path)
        ).model_dump(mode="json", by_alias=True),
    )

    response = cast(WorkspaceGitCheckoutsResponse, result.response)
    assert response.model_dump(mode="json", by_alias=True) == {
        "checkouts": [
            {
                "repoLocalPath": str(tmp_path),
                "ok": True,
                "isPrimary": True,
                # No worktree: the session sits in the repository's own
                # checkout, which the listing does not report.
                "worktree": None,
                "root": str(tmp_path.resolve()),
                "branch": "main",
                "baseBranch": "main",
                # The fixture repository has no remote, which is what a checkout
                # made locally and never pushed looks like.
                "repoUrl": None,
                "message": None,
            }
        ]
    }


@pytest.mark.asyncio
async def test_passive_host_reads_the_worktree_holding_the_session(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    worktree = _prepare("status-listed", tmp_path, branch="feat/status-listed")
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "workspace/git/checkouts",
        WorkspaceGitCheckoutsParams(
            repo_local_paths=[str(tmp_path)], session_cwd=str(worktree.path)
        ).model_dump(mode="json", by_alias=True),
    )

    response = cast(WorkspaceGitCheckoutsResponse, result.response)
    [checkout] = response.checkouts
    # A managed worktree lives outside the repository, so the repository is
    # still the one holding the session.
    assert checkout.is_primary is True
    assert checkout.worktree == "status-listed"
    assert checkout.branch == "feat/status-listed"
    assert checkout.base_branch == "main"
    assert checkout.root == str(worktree.root)


@pytest.mark.asyncio
async def test_passive_host_keeps_an_unreadable_repository_as_the_holder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Containment is a question about paths, so a repository git could not be
    # read from still takes the session from its ancestor. Handing it to the
    # ancestor instead would report that ancestor's branch for a session that
    # is not in it.
    outer = tmp_path / "outer"
    outer.mkdir()
    _init_repo(outer)
    inner = outer / "nested"
    inner.mkdir()
    _init_repo(inner)

    listed = WorktreeRepository.linked

    def unreadable_inner(repository: WorktreeRepository) -> tuple[object, ...]:
        if repository.root == inner.resolve():
            raise GitUnavailableError("Git worktree operations require git.")
        return listed(repository)

    monkeypatch.setattr(WorktreeRepository, "linked", unreadable_inner)
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "workspace/git/checkouts",
        WorkspaceGitCheckoutsParams(
            repo_local_paths=[str(outer), str(inner)], session_cwd=str(inner)
        ).model_dump(mode="json", by_alias=True),
    )

    response = cast(WorkspaceGitCheckoutsResponse, result.response)
    primary = [
        checkout.repo_local_path
        for checkout in response.checkouts
        if checkout.is_primary
    ]
    assert primary == [str(inner)]


@pytest.mark.asyncio
async def test_passive_host_reads_the_branch_of_a_worktree_the_listing_skips(
    tmp_path: Path,
) -> None:
    # A detached worktree is absent from the listing, but the session can still
    # be standing in one. Falling back to the repository would report the main
    # checkout's branch for a session that is not on it.
    _init_repo(tmp_path)
    worktree = _prepare("detached-listed", tmp_path, branch="feat/detached-listed")
    Repo(worktree.root).git.checkout("--detach")
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "workspace/git/checkouts",
        WorkspaceGitCheckoutsParams(
            repo_local_paths=[str(tmp_path)], session_cwd=str(worktree.path)
        ).model_dump(mode="json", by_alias=True),
    )

    response = cast(WorkspaceGitCheckoutsResponse, result.response)
    [checkout] = response.checkouts
    assert checkout.root == str(worktree.root)
    # Detached: on no branch at all, which is not the repository's branch.
    assert checkout.branch is None


@pytest.mark.asyncio
async def test_passive_host_leaves_out_a_path_git_knows_nothing_about(
    tmp_path: Path,
) -> None:
    # Not a checkout is not a failure to report. A project may link a directory
    # git knows nothing about, and an entry for it would put a permanent error
    # in the header rather than simply nothing.
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "workspace/git/checkouts",
        WorkspaceGitCheckoutsParams(
            repo_local_paths=[str(tmp_path)], session_cwd=None
        ).model_dump(mode="json", by_alias=True),
    )

    response = cast(WorkspaceGitCheckoutsResponse, result.response)
    assert response.checkouts == []


@pytest.mark.asyncio
async def test_passive_host_lists_no_worktrees_for_non_git_root(tmp_path: Path) -> None:
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "workspace/git/worktrees/list",
        WorkspaceWorktreeListParams(cwd=str(tmp_path)).model_dump(
            mode="json", by_alias=True
        ),
    )

    response = cast(WorkspaceWorktreeListResponse, result.response)
    assert response.model_dump(mode="json", by_alias=True) == {
        "worktrees": [],
        "repositoryBranch": None,
        "repositoryCwd": None,
    }


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
async def test_rename_on_corrupt_metadata_reports_internal_error(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    root = build_test_agent_loop(config=config)
    await root.persist_empty_session()

    async def open_root(_request: runtime.RootOpenRequest) -> AgentLoop:
        return root

    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="test", version="1"))
    await client.notify("initialized")
    try:
        await client.request("session/start", SessionStartParams())
        session_path = SessionLoader.find_session_by_id(
            root.session_id, config.session_logging
        )
        assert session_path is not None
        # Corrupt the metadata after the session is attached, so the rename hits
        # apply_manual_title's read rather than failing session discovery first.
        (session_path / "meta.json").write_text("{not json", encoding="utf-8")

        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                "session/rename",
                SessionTitleUpdateParams(
                    session_id=root.session_id, title="New title"
                ).model_dump(mode="json", by_alias=True),
            )
    finally:
        await client.close()

    assert exc_info.value.error.code is ProtocolErrorCode.INTERNAL_ERROR
    assert "Failed to read session metadata" in exc_info.value.error.message


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
    server = create_legacy_app_server(server_transport, open_root=open_root)
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
    assert blueprint.mcp_registry is not None
    assert blueprint.mcp_registry._authorization_provider is not None
    assert blueprint.mcp_registry._descriptor_cache_root == (
        Path(blueprint.config.session_logging.save_dir).expanduser().resolve().parent
        / "mcp-descriptors"
        / "legacy"
    )


def test_experimental_harness_process_selects_the_unified_harness_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("mistralai_rust_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendHostAdapter,
    )

    selected = SimpleNamespace(harness_kind="rust")
    monkeypatch.setattr(runtime, "create_experimental_harness_host", lambda: selected)

    process = runtime.HarnessProcess(experimental_harness=True)
    host = process.create_session_backend_host(_FakeSessionBackendServices())

    # The process hands the app server the Vibe-side adapter, not the raw
    # Harness Host, so the two protocol shapes only meet in one place.
    assert isinstance(host, UnifiedHarnessBackendHostAdapter)
    assert host.harness_kind == selected.harness_kind


def test_default_process_selects_the_legacy_session_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode() -> object:
        raise AssertionError("the default path must not import the Harness")

    monkeypatch.setattr(runtime, "create_experimental_harness_host", explode)

    host = runtime.HarnessProcess().create_session_backend_host(
        _FakeSessionBackendServices()
    )

    assert isinstance(host, LegacySessionBackendHost)


def test_unavailable_experimental_harness_fails_with_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable() -> object:
        raise runtime.ExperimentalHarnessUnavailableError("not installed")

    monkeypatch.setattr(runtime, "create_experimental_harness_host", unavailable)
    process = runtime.HarnessProcess(experimental_harness=True)

    with pytest.raises(runtime.RuntimeConfigurationError, match="not installed"):
        process.create_session_backend_host(_FakeSessionBackendServices())


@pytest.mark.asyncio
async def test_experimental_harness_process_never_opens_a_legacy_runtime(
    tmp_path: Path,
) -> None:
    process = runtime.HarnessProcess(experimental_harness=True)
    request = runtime.RootOpenRequest(
        options=SessionOptions(cwd=str(tmp_path)),
        client_info=ClientInfo(name="test", version="1"),
    )

    with pytest.raises(
        runtime.RuntimeConfigurationError, match="never opens a legacy runtime"
    ):
        await process.open_root(request)


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
    blueprint.config = agent_loop.config

    def build_root(*, session_id: str, session_lease: SessionLease) -> AgentLoop:
        del session_id
        agent_loop.replace_session_lease(session_lease)
        return agent_loop

    blueprint.build.side_effect = build_root
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
        blueprint.build.assert_called_once()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_production_legacy_root_holds_lease_until_runtime_shutdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(
            enabled=True, save_dir=str(tmp_path), session_prefix="session"
        )
    )
    saved = build_test_agent_loop(config=config)
    await saved.persist_empty_session()
    session_id = saved.session_id
    await saved.aclose()

    process = runtime.HarnessProcess()
    blueprint = Mock()
    blueprint.config = config
    blueprint.cwd = tmp_path
    blueprint.build.side_effect = lambda **kwargs: build_test_agent_loop(
        config=config, **kwargs
    )
    monkeypatch.setattr(
        process, "build_root_blueprint", AsyncMock(return_value=blueprint)
    )
    request = runtime.RootOpenRequest(
        options=SessionOptions(cwd=str(tmp_path)),
        client_info=ClientInfo(name="test", version="1"),
        session_id=session_id,
    )

    root = await process.open_root(request)
    with pytest.raises(SessionBusyError):
        SessionLease(tmp_path, session_id).acquire()

    await root.aclose()
    SessionLease(tmp_path, session_id).acquire().release()


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
    server = create_legacy_app_server(
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
    server = create_legacy_app_server(
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

        # Session-scoped config/read must populate the banner count fields from
        # the live agent loop (regression for the session _config_read path that
        # previously returned only `config=...` and left counts at default 0).
        runtime_snapshot = validate_wire(
            RuntimeReadResponse,
            await client.request(
                "runtime/read", RuntimeReadParams(session_id=root.session_id)
            ),
        ).runtime
        assert attached.hooks_count == runtime_snapshot.hooks_count
        assert attached.skills_count == sum(
            1 for skill in runtime_snapshot.skills if skill.source != "builtin"
        )
        runtime_servers = [
            src for src in runtime_snapshot.mcp.sources if src.kind.value == "server"
        ]
        assert attached.mcp_servers_total == len(runtime_servers)
        assert attached.mcp_servers_enabled == sum(
            1 for src in runtime_servers if src.status.value != "disabled"
        )

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
    server = create_legacy_app_server(server_transport, open_root=open_root)
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
    server = create_legacy_app_server(
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
    server = create_legacy_app_server(server_transport, open_root=open_root)
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
    server = create_legacy_app_server(server_transport, open_root=open_root)

    with pytest.raises(AppServerResponseError) as exc_info:
        await AppServerSession.start(
            AppServerClient(client_transport, run_peer=server.serve),
            client_info=ClientInfo(name="auth-client", version="1"),
            capabilities=ClientCapabilities(),
        )

    assert exc_info.value.error.code is ProtocolErrorCode.UNAUTHORIZED
    assert exc_info.value.error.data == {"provider": "mistral"}


@pytest.mark.asyncio
async def test_resume_rebinds_session_in_place_preserving_backend(
    tmp_path: Path,
) -> None:
    logging = SessionLoggingConfig(
        enabled=True, save_dir=str(tmp_path / "sessions"), session_prefix="session"
    )
    source_backend = ClosingBackend()
    source = build_test_agent_loop(
        config=build_test_vibe_config(session_logging=logging), backend=source_backend
    )
    source.stats.session_prompt_tokens = 11
    source.stats.session_completion_tokens = 7
    source.stats.context_tokens = 18
    await source.persist_empty_session()

    original_session_id = source.session_id
    await runtime.AgentRuntimeFactory().resume_root(source, original_session_id)

    try:
        # resume_root mutates in-place: same object, same backend, stats preserved.
        assert source.session_id == original_session_id
        assert source.backend is source_backend
        assert source.stats.session_prompt_tokens == 11
        assert source.stats.session_completion_tokens == 7
        assert not source_backend.closed
    finally:
        await source.aclose()
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
