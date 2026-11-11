"""Where a session runs when the caller asked for a worktree, and who holds it.

Contract-level rather than backend-level on purpose. Both halves of this used
to live inside the legacy runtime, so the unified harness silently ignored
every `worktree` a client sent and never marked one as occupied -- the
composer's pick reached a backend that had never heard of it, and retention
cleanup could not tell a live checkout from an inactive one.
These run against whichever backend the suite is pointed at, so that cannot
come back.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from pathlib import Path
import re
import subprocess
from typing import cast

from git import Repo
import httpx
import pytest
import respx

from mistralai_vibe_local_harness.vibe import UnifiedHarnessSessionBackend
from tests.app_server.backend_contract.conftest import connect_backend_contract_host
from vibe.app_server._model import validate_wire
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._session_artifacts import SessionArtifactService
from vibe.app_server._unified_harness_backend_adapter import (
    UnifiedHarnessBackendAdapter,
)
from vibe.app_server._unified_permissions import UnifiedPermissionResolver
from vibe.app_server._worktree_session import SessionWorktrees, WorktreeResolution
from vibe.app_server.events import CallbackRequested
from vibe.app_server.models import (
    CompletedEffectState,
    FailedEffectState,
    PublicEffectEntry,
    PublicMessageEntry,
    TextContentBlock,
    WorktreeEffectDetail,
    WorktreeEffectInput,
)
from vibe.app_server.protocol import (
    AgentSwitchParams,
    AutoWorktreeInput,
    ClientCapabilities,
    NewWorktreeInput,
    RuntimeMutationResponse,
    SessionOptions,
    TurnStartParams,
    TurnStartResponse,
)
from vibe.app_server.session import AppServerSession, AppServerTurnError
from vibe.core.git.worktree import (
    ManagedWorktree,
    WorktreeProgressCallback,
    WorktreeRepository,
)
import vibe.core.session.worktrees as worktree_lifecycle_module
from vibe.core.trusted_folders import TrustedFoldersManager


def _cwd(session: AppServerSession) -> Path:
    # A started session always has one; the protocol type allows None because
    # the field is optional on the way in, not on the way out.
    cwd = session.state.session.cwd
    assert cwd is not None
    return Path(cwd)


async def _settled_cwd(session: AppServerSession, base: Path) -> Path:
    async with asyncio.timeout(5):
        while (cwd := _cwd(session)) == base:
            await asyncio.sleep(0.01)
    return cwd


def _init_repo(root: Path) -> Repo:
    repo = Repo.init(root, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (root / "file.txt").write_text("hello\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    return repo


@pytest.mark.asyncio
async def test_a_session_starts_in_the_worktree_it_asked_for(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    _init_repo(tmp_path)
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(branch="jun/contract", name="contract"),
        ),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        session = await connection.host.start_session()
        cwd = await _settled_cwd(session, tmp_path)

        # Somewhere else entirely, not the directory the request named.
        assert cwd != tmp_path
        assert cwd.is_dir()
        assert Repo(cwd).active_branch.name == "jun/contract"
    finally:
        await connection.host.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("trusted", [False, True])
@pytest.mark.parametrize("relocate", [False, True])
async def test_session_git_filters_survive_managed_move_and_live_fork(
    tmp_path: Path,
    trusted: bool,
    relocate: bool,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    import threading

    from vibe.app_server._runtime import HarnessProcess
    import vibe.app_server._unified_harness_backend_adapter as adapter_module
    from vibe.core.config.harness_files import HarnessFilesManager
    from vibe.core.trusted_folders import trusted_folders_manager

    prepare_trust = adapter_module._relocated_worktree_git_trust
    event_loop_thread = threading.get_ident()

    def checked_prepare_trust(
        previous_cwd: Path,
        cwd: Path,
        previous_store: TrustedFoldersManager,
        store: TrustedFoldersManager,
    ) -> TrustedFoldersManager:
        assert threading.get_ident() != event_loop_thread
        return prepare_trust(previous_cwd, cwd, previous_store, store)

    monkeypatch.setattr(
        adapter_module, "_relocated_worktree_git_trust", checked_prepare_trust
    )
    repo = _init_repo(tmp_path)
    (tmp_path / ".gitattributes").write_text("*.txt filter=probe\n")
    repo.index.add([".gitattributes"])
    repo.index.commit("filter")
    repo.git.config("filter.probe.smudge", "tr a-z A-Z")
    trust_store = trusted_folders_manager.for_session()
    harness_files = HarnessFilesManager(
        sources=("user", "project"), trust_store=trust_store
    )
    destination = None
    if relocate:
        with WorktreeRepository.open(tmp_path, trust_store=trust_store) as worktrees:
            destination = worktrees.prepare("relocated-filter").path
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(
                name="session-filter", branch="vibe/session-filter"
            ),
            trust_workspace=trusted,
        ),
        capabilities=ClientCapabilities(),
        process_services=process_services,
        process=HarnessProcess(harness_files, services=process_services),
    )
    try:
        session = await connection.host.start_session()
        cwd = await _settled_cwd(session, tmp_path)
        assert trust_store is not trusted_folders_manager
        assert trusted_folders_manager.is_trusted(tmp_path) is not True
        assert (cwd / "file.txt").read_text() == ("HELLO\n" if trusted else "hello\n")
        await session.resources.sessions.fork()
        fork_cwd = _cwd(session)
        assert fork_cwd != cwd
        assert (fork_cwd / "file.txt").read_text() == (
            "HELLO\n" if trusted else "hello\n"
        )
        if destination is not None:
            # The first fork awaits initial deferred setup, including source
            # grant revocation. A cwd notification alone precedes that boundary.
            assert trust_store.is_trusted(tmp_path) is not True
            await session.resources.sessions.relocate(str(destination))
            assert _cwd(session) == destination
            await session.resources.sessions.fork()
            relocated_child = _cwd(session)
            assert relocated_child != destination
            assert (relocated_child / "file.txt").read_text() == (
                "HELLO\n" if trusted else "hello\n"
            )
        assert trusted_folders_manager.is_trusted(tmp_path) is not True
    finally:
        await connection.host.close()


def _write_post_checkout_hook(repo: Repo, marker: Path) -> None:
    # The marker has to be absolute: the hook runs with the new worktree as
    # its cwd, so a relative marker would be written inside the worktree and
    # the test would pass even when the hook runs.
    hooks = Path(repo.git_dir) / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "post-checkout"
    hook.write_text(f'#!/bin/sh\necho ran > "{marker}"\n')
    hook.chmod(0o755)


@pytest.mark.asyncio
async def test_a_session_starting_a_worktree_does_not_run_repository_hooks(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    # Worktree creation happens before any trust prompt, so a hostile
    # post-checkout hook would run with the user's full privileges. Both
    # backends create worktrees through the same GitRepo.add_worktree, so this
    # holds for either harness the suite is pointed at.
    repo = _init_repo(tmp_path)
    marker = tmp_path / "hook-ran"
    _write_post_checkout_hook(repo, marker)
    # Control: `git worktree add` is what runs post-checkout, so confirm this
    # environment executes the hook at all. Without it the assertion below
    # could pass on a machine that never runs hooks.
    subprocess.run(
        ["git", "worktree", "add", str(tmp_path / "control"), "-b", "control"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    if not marker.exists():
        pytest.skip("git hooks do not execute in this environment")
    marker.unlink()

    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(branch="jun/hooked", name="hooked"),
        ),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        session = await connection.host.start_session()
        cwd = await _settled_cwd(session, tmp_path)

        assert cwd != tmp_path
        assert not marker.exists()
    finally:
        await connection.host.close()


@pytest.mark.asyncio
async def test_an_auto_worktree_is_still_a_worktree(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    # The composer's default. The server names it, which is the ordinary case
    # rather than an edge one.
    _init_repo(tmp_path)
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=AutoWorktreeInput(),
        ),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        session = await connection.host.start_session()
        cwd = await _settled_cwd(session, tmp_path)

        assert cwd != tmp_path
        assert cwd.is_dir()
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_first_turn_response_does_not_wait_for_worktree_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:

    _init_repo(tmp_path)
    setup_started = asyncio.Event()
    release_setup = asyncio.Event()
    original = SessionWorktrees.resolve_for_start

    async def blocked_resolve(
        worktrees: SessionWorktrees,
        options: SessionOptions,
        on_progress: WorktreeProgressCallback | None = None,
        *,
        trust_store: TrustedFoldersManager | None = None,
        copy_on_write: bool = False,
    ) -> WorktreeResolution:
        if options.worktree is None:
            return await original(
                worktrees,
                options,
                on_progress,
                trust_store=trust_store,
                copy_on_write=copy_on_write,
            )
        setup_started.set()
        await release_setup.wait()
        return await original(
            worktrees,
            options,
            on_progress,
            trust_store=trust_store,
            copy_on_write=copy_on_write,
        )

    monkeypatch.setattr(SessionWorktrees, "resolve_for_start", blocked_resolve)
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(branch="jun/deferred", name="deferred"),
        ),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        session = await connection.host.start_session()
        await setup_started.wait()

        response = validate_wire(
            TurnStartResponse,
            await asyncio.wait_for(
                connection.client.request(
                    "turn/start",
                    TurnStartParams(
                        session_id=session.session_id,
                        message=[TextContentBlock(text="start now")],
                        client_user_message_id="message-1",
                    ),
                ),
                timeout=1,
            ),
        )

        assert response.turn.status.value == "in_progress"
        assert not release_setup.is_set()
    finally:
        release_setup.set()
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_an_agent_switch_does_not_wait_for_worktree_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """A client picks the agent before its first message, and that pick must
    not hold the message back for the whole checkout.
    """
    _init_repo(tmp_path)
    setup_started = asyncio.Event()
    release_setup = asyncio.Event()
    original = SessionWorktrees.resolve_for_start

    async def blocked_resolve(
        worktrees: SessionWorktrees,
        options: SessionOptions,
        on_progress: WorktreeProgressCallback | None = None,
        *,
        trust_store: TrustedFoldersManager | None = None,
        copy_on_write: bool = False,
    ) -> WorktreeResolution:
        if options.worktree is None:
            return await original(
                worktrees,
                options,
                on_progress,
                trust_store=trust_store,
                copy_on_write=copy_on_write,
            )
        setup_started.set()
        await release_setup.wait()
        return await original(
            worktrees,
            options,
            on_progress,
            trust_store=trust_store,
            copy_on_write=copy_on_write,
        )

    monkeypatch.setattr(SessionWorktrees, "resolve_for_start", blocked_resolve)
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(branch="jun/agent", name="agent"),
        ),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        session = await connection.host.start_session()
        await setup_started.wait()

        switched = validate_wire(
            RuntimeMutationResponse,
            await asyncio.wait_for(
                connection.client.request(
                    "session/agent/update",
                    AgentSwitchParams(
                        session_id=session.session_id, agent_name="auto-approve"
                    ),
                ),
                timeout=1,
            ),
        )

        assert switched.runtime.active_agent.name == "auto-approve"
        assert not release_setup.is_set()
    finally:
        release_setup.set()
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_turn_claimed_before_a_failed_worktree_reports_it_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """A turn claimed while the worktree prepares still learns of a failure once.

    The claimed turn and the setup task both observe a failed preparation; a
    client must see one failed worktree entry, not one per observer.
    """
    _init_repo(tmp_path)
    setup_started = asyncio.Event()
    release_setup = asyncio.Event()
    original = SessionWorktrees.resolve_for_start

    async def failing_resolve(
        worktrees: SessionWorktrees,
        options: SessionOptions,
        on_progress: WorktreeProgressCallback | None = None,
        *,
        trust_store: TrustedFoldersManager | None = None,
        copy_on_write: bool = False,
    ) -> WorktreeResolution:
        if options.worktree is None:
            return await original(
                worktrees,
                options,
                on_progress,
                trust_store=trust_store,
                copy_on_write=copy_on_write,
            )
        setup_started.set()
        await release_setup.wait()
        raise RuntimeError("worktree preparation failed")

    monkeypatch.setattr(SessionWorktrees, "resolve_for_start", failing_resolve)
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(branch="jun/claimfail", name="claimfail"),
        ),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        session = await connection.host.start_session()
        await setup_started.wait()

        # Claim the turn while the preparation is still blocked, then fail it.
        response = validate_wire(
            TurnStartResponse,
            await asyncio.wait_for(
                connection.client.request(
                    "turn/start",
                    TurnStartParams(
                        session_id=session.session_id,
                        message=[TextContentBlock(text="start now")],
                        client_user_message_id="message-1",
                    ),
                ),
                timeout=5,
            ),
        )
        assert response.turn.status.value == "in_progress"
        release_setup.set()

        def _worktree_entries() -> list[PublicEffectEntry]:
            return [
                entry
                for entry in session.state.history or []
                if isinstance(entry, PublicEffectEntry)
                and isinstance(entry.detail, WorktreeEffectDetail)
            ]

        async with asyncio.timeout(5):
            while not _worktree_entries():
                await asyncio.sleep(0.01)
        # A duplicate, if the single-emit guard regressed, rides the same
        # failure wave; let the wave finish before counting.
        await asyncio.sleep(0.5)
        entries = _worktree_entries()
        assert len(entries) == 1
        assert isinstance(entries[0].state, FailedEffectState)
    finally:
        release_setup.set()
        await connection.host.close()


@pytest.mark.asyncio
async def test_a_session_without_a_worktree_runs_where_it_was_told(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    # The other half of the contract: naming no worktree still has to leave the
    # session where the caller put it, on both backends.
    _init_repo(tmp_path)
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path), workspace_roots=[str(tmp_path)]
        ),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        session = await connection.host.start_session()

        assert _cwd(session) == tmp_path
    finally:
        await connection.host.close()


@pytest.mark.one_path
@pytest.mark.asyncio
@pytest.mark.parametrize("file_location", ["extra", "checkout"])
async def test_a_worktree_preserves_extra_roots_but_not_the_original_checkout(
    tmp_path: Path,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    file_location: str,
    process_services: ProcessServices,
) -> None:
    checkout = tmp_path / "checkout"
    _init_repo(checkout)
    extra = tmp_path / "extra"
    extra.mkdir()
    document = tmp_path / file_location / "notes.md"
    document.write_text("The release name is copper-finch.")
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    {
                        "id": "read-1",
                        "index": 0,
                        "function": {
                            "name": "read_file",
                            "arguments": json.dumps({"path": str(document)}),
                        },
                    }
                ],
            ),
            backend_contract_mistral_response("done"),
        ]
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(checkout),
            workspace_roots=[str(checkout), str(extra)],
            worktree=AutoWorktreeInput(),
            enabled_tools=["read_file"],
        ),
        capabilities=ClientCapabilities(callback_kinds=["approval"]),
        process_services=process_services,
    )
    try:
        session = await connection.host.start_session()
        approvals = []
        # The first turn also waits for deferred worktree setup on Unified.
        async for event in session.act("Read the file"):
            if isinstance(event, CallbackRequested):
                approvals.append(event.callback)
                await session.deny_callback(event.callback)

        assert _cwd(session) != checkout
        if file_location == "checkout":
            assert len(approvals) == 1
        else:
            assert not approvals
        payload = json.loads(backend_contract_mistral_api.calls[-1].request.content)
        read_contents = any(
            message["role"] == "tool"
            and "copper-finch" in json.dumps(message["content"])
            for message in payload["messages"]
        )
        assert read_contents == (file_location == "extra")
    finally:
        await connection.host.close()


@pytest.mark.asyncio
async def test_a_session_holds_its_worktree_and_lets_go_on_close(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    # The hold is what retention pruning reads to avoid deleting a live
    # worktree. A backend that never marks its own can lose its active checkout.
    _init_repo(tmp_path)
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(branch="jun/held", name="held"),
        ),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    session = await connection.host.start_session()
    try:
        session_id = session.state.session.id
        managed = ManagedWorktree.at(await _settled_cwd(session, tmp_path))
        assert managed is not None

        assert session_id in managed.holders()
    finally:
        # Through `session/stop` rather than by dropping the transport, because
        # letting go is part of stopping and a torn-down connection would say
        # nothing about whether the backend does it.
        await session.close()

    # A marker that outlives its process keeps that worktree undeletable for
    # good, so the release is as much of the contract as the hold.
    assert session_id not in managed.holders()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_the_first_tool_call_runs_inside_the_worktree(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    _init_repo(tmp_path)
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    {
                        "id": "bash-1",
                        "index": 0,
                        "function": {
                            "name": "bash",
                            "arguments": json.dumps({
                                "command": "touch marker",
                                "timeout_seconds": 5,
                            }),
                        },
                    }
                ],
            ),
            backend_contract_mistral_response("done"),
        ]
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(branch="jun/gated", name="gated"),
            enabled_tools=["bash"],
            auto_approve=True,
        ),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        session = await connection.host.open_session()

        _ = [event async for event in session.act("leave a marker")]

        cwd = _cwd(session)
        history = session.state.history or []
        worktree = next(
            entry
            for entry in history
            if isinstance(entry, PublicEffectEntry)
            and isinstance(entry.detail, WorktreeEffectDetail)
        )
        user_messages = [
            entry
            for entry in history
            if isinstance(entry, PublicMessageEntry) and entry.role == "user"
        ]
        assert cwd != tmp_path
        assert (cwd / "marker").exists()
        assert not (tmp_path / "marker").exists()
        assert isinstance(worktree.state, CompletedEffectState)
        assert worktree.detail.input is not None
        worktree_input = cast(WorktreeEffectInput, worktree.detail.input)
        assert worktree_input.path == str(cwd)
        assert len(user_messages) == 1
        payload = json.loads(backend_contract_mistral_api.calls[0].request.content)
        system_message = next(
            message["content"]
            for message in payload["messages"]
            if message["role"] == "system"
        )
        assert f"Absolute path: {cwd}" in system_message
        assert f"Absolute path: {tmp_path}" not in system_message
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_worktree_that_cannot_be_raised_refuses_the_turn(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    """A session that asked to be isolated does not quietly run unisolated.

    Skipped on legacy, which raises the worktree before the session exists and
    so fails the start instead -- the same refusal, one step earlier.
    """
    # No repository here, so there is nothing to raise a worktree from.
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(branch="jun/nowhere", name="nowhere"),
        ),
        capabilities=ClientCapabilities(),
        process_services=process_services,
    )
    try:
        session = await connection.host.start_session()

        assert _cwd(session) == tmp_path
        # The turn is accepted before deferred setup finishes, then fails as a
        # visible turn instead of making session creation wait for the checkout.
        with pytest.raises(AppServerTurnError):
            _ = [event async for event in session.act("write something")]

        # Exactly one failed worktree entry: the failure reaches the client
        # once, not once per observer of the failed preparation.
        worktree_entries = [
            entry
            for entry in session.state.history or []
            if isinstance(entry, PublicEffectEntry)
            and isinstance(entry.detail, WorktreeEffectDetail)
        ]
        assert len(worktree_entries) == 1
        assert isinstance(worktree_entries[0].state, FailedEffectState)
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_smart_approve_does_not_treat_the_worktree_as_agent_config(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    """Calls naming files inside a managed worktree run when the classifier allows.

    Managed worktrees live under ``.vibe/worktrees/``, and the model is told the
    worktree's absolute path. Reading that ``.vibe`` segment as the agent's own
    config forced a human prompt on every classified command and edit there.
    """
    _init_repo(tmp_path)
    classifier_requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "tools" not in body:
            classifier_requests.append(request)
            return backend_contract_mistral_response(
                json.dumps({
                    "safe": True,
                    "risk_level": "low",
                    "user_authorization": "high",
                    "reason": "requested by the user",
                })
            )
        if body["messages"][-1]["role"] == "tool":
            return backend_contract_mistral_response("done")
        system = next(m["content"] for m in body["messages"] if m["role"] == "system")
        worktree = re.search(r"Absolute path: (\S+)", system)
        assert worktree is not None
        root = worktree.group(1)
        return backend_contract_mistral_response(
            "",
            tool_calls=[
                {
                    "id": "bash-1",
                    "index": 0,
                    "function": {
                        "name": "bash",
                        "arguments": json.dumps({
                            "command": f"python3 -c 'print(open(\"{root}/file.txt\").read())'",
                            "timeout_seconds": 5,
                        }),
                    },
                },
                {
                    "id": "write-1",
                    "index": 1,
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({
                            "path": f"{root}/notes.md",
                            "content": "written\n",
                        }),
                    },
                },
            ],
        )

    backend_contract_mistral_api.mock(side_effect=respond)
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(branch="jun/smart", name="smart"),
            enabled_tools=["bash", "write_file"],
            agent="smart-approve",
            trust_workspace=True,
        ),
        capabilities=ClientCapabilities(callback_kinds=["approval"]),
        process_services=process_services,
    )
    approvals: list[CallbackRequested] = []
    try:
        session = await connection.host.open_session()
        async for event in session.act("print file.txt and write notes.md"):
            if isinstance(event, CallbackRequested):
                approvals.append(event)
                await session.deny_callback(event.callback)

        cwd = _cwd(session)
        assert ".vibe" in cwd.parts
        assert classifier_requests
        assert not approvals
        assert (cwd / "notes.md").read_text() == "written\n"
    finally:
        await connection.host.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("trusted", [False, True])
async def test_live_worktree_removal_uses_session_source_git_trust(
    tmp_path: Path, trusted: bool, process_services: ProcessServices
) -> None:
    from vibe.app_server._runtime import HarnessProcess
    from vibe.core.config.harness_files import HarnessFilesManager
    from vibe.core.trusted_folders import trusted_folders_manager

    repo = _init_repo(tmp_path)
    repo.git.config("filter.cleanup.clean", "sed 's/^S://'")
    repo.git.config("filter.cleanup.smudge", "sed 's/^/S:/'")
    (tmp_path / ".gitattributes").write_text("*.txt filter=cleanup\n")
    repo.index.add([".gitattributes"])
    repo.index.commit("filter")
    process_store = trusted_folders_manager.for_session()
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(name="live-cleanup", branch="vibe/live-cleanup"),
            trust_workspace=trusted,
        ),
        capabilities=ClientCapabilities(),
        process_services=process_services,
        process=HarnessProcess(
            HarnessFilesManager(sources=("user", "project"), trust_store=process_store),
            services=process_services,
        ),
    )
    try:
        session = await connection.host.start_session()
        await _settled_cwd(session, tmp_path)
        await session.resources.sessions.fork()
        assert process_store.is_trusted(tmp_path) is not True
        assert trusted_folders_manager.is_trusted(tmp_path) is not True
        with WorktreeRepository.open(tmp_path) as repository:
            target = repository.prepare_auto(suggested_name="closed-cleanup")
        managed = ManagedWorktree.at(target.root)
        assert managed is not None
        managed.hold("completed-start")
        managed.release_holder("completed-start")
        (target.root / "file.txt").write_text("S:changed\n")
        response = await connection.client.request(
            "workspace/git/worktrees/remove", {"cwd": str(target.root)}
        )
        if trusted:
            assert response["outcome"] == "removed"
            assert not target.root.exists()
            assert (
                repo.git.show(f"refs/vibe/reaped/{target.name}:file.txt") == "changed"
            )
        else:
            assert response["outcome"] == "kept_dirty"
            assert (target.root / "file.txt").read_text() == "S:changed\n"
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_the_agent_enters_a_worktree_mid_turn(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    _init_repo(tmp_path)

    def call(call_id: str, index: int, name: str, arguments: dict[str, object]):
        return {
            "id": call_id,
            "index": index,
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }

    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    call("enter-1", 0, "enter_worktree", {"name": "fix login"}),
                    call("bash-1", 1, "bash", {"command": "touch before"}),
                ],
            ),
            backend_contract_mistral_response(
                "", tool_calls=[call("bash-2", 0, "bash", {"command": "touch after"})]
            ),
            backend_contract_mistral_response("done"),
        ]
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            enabled_tools=["bash", "enter_worktree"],
            auto_approve=True,
        ),
        capabilities=ClientCapabilities(agent_worktrees=True),
        process_services=process_services,
    )
    try:
        session = await connection.host.open_session()

        _ = [event async for event in session.act("fix the login bug")]

        with WorktreeRepository.open(tmp_path) as repository:
            linked = repository.linked()
        assert len(linked) == 1
        worktree = linked[0].path
        managed = ManagedWorktree.at(worktree)
        # The call planned beside the move ran where it was planned; the next
        # step ran in the worktree.
        assert (tmp_path / "before").exists()
        assert not (tmp_path / "after").exists()
        assert (worktree / "after").exists()
        assert not (worktree / "before").exists()
        assert Repo(worktree).active_branch.name.startswith("vibe/fix-login")
        assert managed is not None
        assert session.state.session.id in managed.holders()
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_an_entered_worktree_is_the_sessions_own_directory(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    _init_repo(tmp_path)
    written: list[Path] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        tool_messages = [m for m in payload["messages"] if m["role"] == "tool"]
        if not tool_messages:
            return backend_contract_mistral_response(
                "",
                tool_calls=[
                    {
                        "id": "enter-1",
                        "index": 0,
                        "function": {
                            "name": "enter_worktree",
                            "arguments": json.dumps({"name": "notes"}),
                        },
                    }
                ],
            )
        if len(tool_messages) == 1:
            entered = json.loads(tool_messages[0]["content"])
            target = Path(entered["path"]) / "notes.md"
            written.append(target)
            return backend_contract_mistral_response(
                "",
                tool_calls=[
                    {
                        "id": "write-1",
                        "index": 0,
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({
                                "path": str(target),
                                "content": "written\n",
                            }),
                        },
                    }
                ],
            )
        return backend_contract_mistral_response("done")

    backend_contract_mistral_api.mock(side_effect=respond)
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            enabled_tools=["write_file", "enter_worktree"],
        ),
        capabilities=ClientCapabilities(
            agent_worktrees=True, callback_kinds=["approval"]
        ),
        process_services=process_services,
    )
    approvals: list[CallbackRequested] = []
    try:
        session = await connection.host.open_session()
        async for event in session.act("write notes"):
            if isinstance(event, CallbackRequested):
                approvals.append(event)
                await session.deny_callback(event.callback)

        assert len(written) == 1
        worktree = written[0].parent
        # Writing in the worktree is writing in the session's own directory:
        # no approval, as for a session started there.
        assert not approvals
        assert written[0].read_text() == "written\n"
        # Clients learn where the session now runs, and so does the model.
        assert _cwd(session) == worktree
        final = json.loads(backend_contract_mistral_api.calls[-1].request.content)
        entered = json.loads(
            next(m for m in final["messages"] if m["role"] == "tool")["content"]
        )
        assert str(worktree) in entered["message"]
        # The worktree is one of the session's artifacts, listed beside its PRs.
        listed = await connection.client.request(
            "artifacts/list", {"sessionId": session.state.session.id}
        )
        assert [
            (artifact["path"], artifact["branch"]) for artifact in listed["worktrees"]
        ] == [(str(worktree), entered["branch"])]
    finally:
        await connection.host.close()


def _tool_call(call_id: str, name: str, arguments: dict[str, object]):
    return {
        "id": call_id,
        "index": 0,
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_ask_mode_still_asks_before_writing_in_an_entered_worktree(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    _init_repo(tmp_path)
    written: list[Path] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        tool_messages = [m for m in payload["messages"] if m["role"] == "tool"]
        if not tool_messages:
            return backend_contract_mistral_response(
                "",
                tool_calls=[
                    {
                        "id": "enter-1",
                        "index": 0,
                        "function": {
                            "name": "enter_worktree",
                            "arguments": json.dumps({"name": "notes"}),
                        },
                    }
                ],
            )
        if len(tool_messages) == 1:
            entered = json.loads(tool_messages[0]["content"])
            target = Path(entered["path"]) / "notes.md"
            written.append(target)
            return backend_contract_mistral_response(
                "",
                tool_calls=[
                    {
                        "id": "write-1",
                        "index": 0,
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps({
                                "path": str(target),
                                "content": "written\n",
                            }),
                        },
                    }
                ],
            )
        return backend_contract_mistral_response("done")

    backend_contract_mistral_api.mock(side_effect=respond)
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            enabled_tools=["write_file", "enter_worktree"],
            agent="ask",
        ),
        capabilities=ClientCapabilities(
            agent_worktrees=True, callback_kinds=["approval"]
        ),
        process_services=process_services,
    )
    approvals: list[CallbackRequested] = []
    try:
        session = await connection.host.open_session()
        async for event in session.act("write notes"):
            if isinstance(event, CallbackRequested):
                approvals.append(event)
                await session.deny_callback(event.callback)

        assert len(written) == 1
        # Entering is no approval to edit: Ask mode asks inside the worktree
        # exactly as it does inside the checkout the session started in.
        assert len(approvals) == 1
        assert not written[0].exists()
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_fork_branches_from_the_worktree_the_session_is_in(
    backend_contract_persistent_config: None,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    checkout = tmp_path / "checkout"
    _init_repo(checkout)
    entered: list[Path] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        tools = [m for m in payload["messages"] if m["role"] == "tool"]
        if len(tools) < 2:
            if tools:
                entered.append(Path(json.loads(tools[0]["content"])["path"]))
            return backend_contract_mistral_response(
                "",
                tool_calls=[
                    _tool_call(
                        f"enter-{len(tools)}",
                        "enter_worktree",
                        {"name": f"task {len(tools)}"},
                    )
                ],
            )
        if len(entered) == 1:
            entered.append(Path(json.loads(tools[1]["content"])["path"]))
            (entered[1] / "work.md").write_text("work\n")
        return backend_contract_mistral_response("done")

    backend_contract_mistral_api.mock(side_effect=respond)
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(checkout),
            workspace_roots=[str(checkout)],
            enabled_tools=["write_file", "enter_worktree"],
        ),
        capabilities=ClientCapabilities(
            agent_worktrees=True, callback_kinds=["approval"]
        ),
        process_services=process_services,
    )
    try:
        session = await connection.host.open_session()
        source_id = session.state.session.id
        _ = [event async for event in session.act("enter two worktrees")]
        first, current = entered
        assert _cwd(session) == current

        await session.resources.sessions.fork()

        fork_cwd = _cwd(session)
        assert session.state.session.id != source_id
        # As a fork of a worktree session on main: a fresh worktree from the
        # source's commit, not the source's own directory.
        assert fork_cwd not in (current, first, checkout)
        assert ManagedWorktree.at(fork_cwd) is not None
        assert fork_cwd.name == f"{current.name}-fork"
        assert first.is_dir() and current.is_dir()
        # The source's worktrees stay the source's: archiving the fork must not
        # remove them.
        listed = await connection.client.request(
            "artifacts/list", {"sessionId": session.state.session.id}
        )
        assert listed["worktrees"] == []
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_resumed_session_keeps_the_worktrees_it_entered(
    backend_contract_persistent_config: None,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    checkout = tmp_path / "checkout"
    _init_repo(checkout)
    entered: list[Path] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        tools = [m for m in payload["messages"] if m["role"] == "tool"]
        users = [m for m in payload["messages"] if m["role"] == "user"]
        if len(users) == 1 and len(tools) < 2:
            if tools:
                entered.append(Path(json.loads(tools[0]["content"])["path"]))
            return backend_contract_mistral_response(
                "",
                tool_calls=[
                    _tool_call(
                        f"enter-{len(tools)}",
                        "enter_worktree",
                        {"name": f"task {len(tools)}"},
                    )
                ],
            )
        if len(users) == 1:
            entered.append(Path(json.loads(tools[1]["content"])["path"]))
            return backend_contract_mistral_response("entered")
        if len(tools) == 2:
            return backend_contract_mistral_response(
                "",
                tool_calls=[
                    _tool_call(
                        "write-1",
                        "write_file",
                        {"path": str(entered[0] / "later.md"), "content": "x\n"},
                    )
                ],
            )
        return backend_contract_mistral_response("done")

    backend_contract_mistral_api.mock(side_effect=respond)
    options = SessionOptions(
        cwd=str(checkout),
        workspace_roots=[str(checkout)],
        enabled_tools=["write_file", "enter_worktree"],
    )
    first = await connect_backend_contract_host(
        session_options=options,
        capabilities=ClientCapabilities(
            agent_worktrees=True, callback_kinds=["approval"]
        ),
        process_services=process_services,
    )
    try:
        session = await first.host.open_session()
        session_id = session.state.session.id
        _ = [event async for event in session.act("start a worktree")]
        await session.close()
    finally:
        await first.host.close()
    first_worktree, worktree = entered
    managed = ManagedWorktree.at(first_worktree)
    assert managed is not None
    assert session_id not in managed.holders()

    second = await connect_backend_contract_host(
        session_options=options,
        capabilities=ClientCapabilities(
            agent_worktrees=True, callback_kinds=["approval"]
        ),
        process_services=process_services,
    )
    approvals: list[CallbackRequested] = []
    try:
        # Listed from the stored session, before any runtime resumes it.
        stored = await second.client.request(
            "artifacts/list", {"sessionId": session_id}
        )
        assert sorted(w["path"] for w in stored["worktrees"]) == sorted(
            str(path) for path in entered
        )
        resumed = await second.host.open_session()
        await resumed.resume(session_id)
        assert session_id in managed.holders()
        async for event in resumed.act("keep writing"):
            if isinstance(event, CallbackRequested):
                approvals.append(event)
                await resumed.deny_callback(event.callback)

        # Resumed where it left off, with the earlier worktree still its own.
        assert _cwd(resumed) == worktree
        assert "did not take effect" not in json.dumps(
            json.loads(backend_contract_mistral_api.calls.last.request.content)
        )
        assert not approvals
        assert (first_worktree / "later.md").read_text() == "x\n"
    finally:
        await second.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_worktree_that_fails_to_be_recorded_is_let_go(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    checkout = tmp_path / "checkout"
    _init_repo(checkout)
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "", tool_calls=[_tool_call("enter-1", "enter_worktree", {"name": "x"})]
            ),
            backend_contract_mistral_response("failed"),
        ]
    )

    async def failing_record(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(SessionArtifactService, "record_worktree", failing_record)
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(checkout),
            workspace_roots=[str(checkout)],
            enabled_tools=["enter_worktree"],
        ),
        capabilities=ClientCapabilities(agent_worktrees=True),
        process_services=process_services,
    )
    try:
        session = await connection.host.open_session()
        session_id = session.state.session.id
        _ = [event async for event in session.act("fix it")]

        with WorktreeRepository.open(checkout) as repository:
            created = ManagedWorktree.at(repository.worktree_root / "x")
        assert _cwd(session) == checkout.resolve()
        assert created is not None
        assert session_id not in created.holders()
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
@pytest.mark.parametrize("forget_fails", [False, True])
async def test_a_move_that_never_landed_is_forgotten_on_close(
    backend_contract_persistent_config: None,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    forget_fails: bool,
) -> None:
    checkout = tmp_path / "checkout"
    _init_repo(checkout)
    if forget_fails:

        async def failing_forget(*_args: object, **_kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(SessionArtifactService, "forget_worktree", failing_forget)
    # The queue drain a finished turn schedules lands a staged move; the close
    # must arrive first.
    drain = UnifiedHarnessSessionBackend._schedule_turn_queue_drain
    monkeypatch.setattr(
        UnifiedHarnessSessionBackend, "_schedule_turn_queue_drain", lambda _self: None
    )
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "", tool_calls=[_tool_call("enter-1", "enter_worktree", {"name": "x"})]
            ),
            backend_contract_mistral_response("still here"),
        ]
    )
    options = SessionOptions(
        cwd=str(checkout),
        workspace_roots=[str(checkout)],
        enabled_tools=["enter_worktree"],
        # The turn ends on the call, so the move is still waiting to land.
        max_turns=1,
    )
    first = await connect_backend_contract_host(
        session_options=options,
        capabilities=ClientCapabilities(agent_worktrees=True),
        process_services=process_services,
    )
    try:
        session = await first.host.open_session()
        session_id = session.state.session.id
        _ = [event async for event in session.act("fix it")]
        await session.close()
    finally:
        await first.host.close()

    monkeypatch.setattr(
        UnifiedHarnessSessionBackend, "_schedule_turn_queue_drain", drain
    )
    second = await connect_backend_contract_host(
        session_options=options.model_copy(update={"max_turns": None}),
        capabilities=ClientCapabilities(agent_worktrees=True),
        process_services=process_services,
    )
    try:
        resumed = await second.host.open_session()
        await resumed.resume(session_id)
        listed = await second.client.request(
            "artifacts/list", {"sessionId": session_id}
        )

        _ = [event async for event in resumed.act("go on")]

        prompt = json.dumps(
            json.loads(backend_contract_mistral_api.calls.last.request.content)[
                "messages"
            ]
        )
        # A record the close failed to forget stays listed, for archive to reap.
        assert (listed["worktrees"] == []) is not forget_fails
        assert _cwd(resumed) == checkout.resolve()
        # The enter_worktree result said it moved: the next turn corrects it.
        assert "did not take effect" in prompt
    finally:
        await second.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_rewind_past_the_move_tells_the_agent_where_it_runs(
    backend_contract_persistent_config: None,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    checkout = tmp_path / "checkout"
    _init_repo(checkout)
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "", tool_calls=[_tool_call("enter-1", "enter_worktree", {"name": "x"})]
            ),
            backend_contract_mistral_response("moved"),
            backend_contract_mistral_response("after the rewind"),
        ]
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(checkout),
            workspace_roots=[str(checkout)],
            enabled_tools=["enter_worktree"],
        ),
        capabilities=ClientCapabilities(agent_worktrees=True),
        process_services=process_services,
    )
    try:
        session = await connection.host.open_session()
        _ = [event async for event in session.act("fix it", client_message_id="u1")]
        worktree = _cwd(session)
        assert any(
            isinstance(entry, PublicEffectEntry)
            and entry.detail.tool_name == "vibe.enter_worktree"
            for entry in session.history
        )
        [entry_id] = [
            entry.id
            for entry in session.history
            if isinstance(entry, PublicMessageEntry) and entry.role == "user"
        ]
        await session.resources.sessions.rewind(
            entry_id, restore_files=False, inplace=True
        )

        _ = [event async for event in session.act("again", client_message_id="u2")]

        prompt = json.dumps(
            json.loads(backend_contract_mistral_api.calls.last.request.content)[
                "messages"
            ]
        )
        assert worktree != checkout.resolve()
        assert _cwd(session) == worktree
        assert str(worktree) in prompt
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_rewind_that_keeps_the_move_adds_no_note(
    backend_contract_persistent_config: None,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    checkout = tmp_path / "checkout"
    _init_repo(checkout)
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "", tool_calls=[_tool_call("enter-1", "enter_worktree", {"name": "x"})]
            ),
            backend_contract_mistral_response("moved"),
            backend_contract_mistral_response("second"),
            backend_contract_mistral_response("after the rewind"),
        ]
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(checkout),
            workspace_roots=[str(checkout)],
            enabled_tools=["enter_worktree"],
        ),
        capabilities=ClientCapabilities(agent_worktrees=True),
        process_services=process_services,
    )
    try:
        session = await connection.host.open_session()
        _ = [event async for event in session.act("fix it", client_message_id="u1")]
        _ = [event async for event in session.act("more", client_message_id="u2")]
        [_, second] = [
            entry.id
            for entry in session.history
            if isinstance(entry, PublicMessageEntry) and entry.role == "user"
        ]
        await session.resources.sessions.rewind(
            second, restore_files=False, inplace=True
        )

        _ = [event async for event in session.act("again", client_message_id="u3")]

        prompt = json.dumps(
            json.loads(backend_contract_mistral_api.calls.last.request.content)[
                "messages"
            ]
        )
        assert "You are working in the git worktree" not in prompt
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_configuration_change_keeps_the_session_in_its_worktree(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    _init_repo(tmp_path)

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        tools = [m for m in payload["messages"] if m["role"] == "tool"]
        users = [m for m in payload["messages"] if m["role"] == "user"]
        if len(users) == 1:
            if tools:
                return backend_contract_mistral_response("entered")
            return backend_contract_mistral_response(
                "", tool_calls=[_tool_call("enter-1", "enter_worktree", {})]
            )
        if len(tools) == 1:
            return backend_contract_mistral_response(
                "",
                tool_calls=[
                    _tool_call(
                        "write-1", "write_file", {"path": "here", "content": "x\n"}
                    )
                ],
            )
        return backend_contract_mistral_response("done")

    backend_contract_mistral_api.mock(side_effect=respond)
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            enabled_tools=["write_file", "enter_worktree"],
            auto_approve=True,
        ),
        capabilities=ClientCapabilities(agent_worktrees=True),
        process_services=process_services,
    )
    try:
        session = await connection.host.open_session()
        _ = [event async for event in session.act("enter a worktree")]
        worktree = _cwd(session)
        assert worktree != tmp_path

        await connection.client.request(
            "session/agent/update",
            AgentSwitchParams(session_id=session.session_id, agent_name="accept-edits"),
        )
        _ = [event async for event in session.act("leave a mark")]

        assert _cwd(session) == worktree
        assert (worktree / "here").exists()
        assert not (tmp_path / "here").exists()
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_an_unnamed_worktree_is_named_after_the_turns_message(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_repo(tmp_path)
    asked: list[str | None] = []

    async def suggest(prompt: str | None, **_kwargs: object) -> str:
        asked.append(prompt)
        return "repair-oauth-redirect"

    monkeypatch.setattr(worktree_lifecycle_module, "suggest_worktree_name", suggest)
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "", tool_calls=[_tool_call("enter-1", "enter_worktree", {})]
            ),
            backend_contract_mistral_response("done"),
        ]
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            enabled_tools=["enter_worktree"],
            auto_approve=True,
        ),
        capabilities=ClientCapabilities(agent_worktrees=True),
        process_services=process_services,
    )
    try:
        session = await connection.host.open_session()
        _ = [event async for event in session.act("fix the oauth redirect")]

        assert asked == ["fix the oauth redirect"]
        assert Repo(_cwd(session)).active_branch.name == "vibe/repair-oauth-redirect"
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_the_agent_continues_an_existing_branch_in_a_worktree(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    repo = _init_repo(tmp_path)
    repo.git.branch("feat/login")
    tip = repo.commit("feat/login").hexsha
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[
                    _tool_call(
                        "enter-1",
                        "enter_worktree",
                        {"name": "login", "branch": "feat/login"},
                    )
                ],
            ),
            backend_contract_mistral_response("done"),
        ]
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            enabled_tools=["enter_worktree"],
            auto_approve=True,
        ),
        capabilities=ClientCapabilities(agent_worktrees=True),
        process_services=process_services,
    )
    try:
        session = await connection.host.open_session()
        _ = [event async for event in session.act("continue the login branch")]

        worktree = Repo(_cwd(session))
        assert _cwd(session) != tmp_path
        assert worktree.active_branch.name == "feat/login"
        assert worktree.head.commit.hexsha == tip
    finally:
        await connection.host.close()
    # The branch was the user's before the session: archiving or closing must
    # never delete it.
    assert "feat/login" in (head.name for head in repo.heads)


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_second_worktree_in_the_same_step_is_refused(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    _init_repo(tmp_path)
    results: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        tools = [m for m in payload["messages"] if m["role"] == "tool"]
        if not tools:
            first = _tool_call("enter-a", "enter_worktree", {"name": "first"})
            second = _tool_call("enter-b", "enter_worktree", {"name": "second"})
            second["index"] = 1
            return backend_contract_mistral_response("", tool_calls=[first, second])
        if len(tools) == 2:
            results.extend(json.loads(t["content"]) for t in tools)
            entered = next(r for r in results if "path" in r)
            return backend_contract_mistral_response(
                "",
                tool_calls=[
                    _tool_call(
                        "write-1",
                        "write_file",
                        {
                            "path": str(Path(str(entered["path"])) / "a.md"),
                            "content": "a\n",
                        },
                    )
                ],
            )
        return backend_contract_mistral_response("done")

    backend_contract_mistral_api.mock(side_effect=respond)
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            enabled_tools=["write_file", "enter_worktree"],
        ),
        capabilities=ClientCapabilities(
            agent_worktrees=True, callback_kinds=["approval"]
        ),
        process_services=process_services,
    )
    approvals: list[CallbackRequested] = []
    try:
        session = await connection.host.open_session()
        async for event in session.act("two tasks"):
            if isinstance(event, CallbackRequested):
                approvals.append(event)
                await session.deny_callback(event.callback)

        entered = [Path(str(r["path"])) for r in results if "path" in r]
        refused = [r for r in results if "path" not in r]
        assert len(entered) == 1
        assert "next step" in json.dumps(refused)
        assert _cwd(session) == entered[0]
        assert not approvals
        assert (entered[0] / "a.md").read_text() == "a\n"
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_landed_move_looks_for_pull_requests_again(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    _init_repo(tmp_path)
    full_passes: list[Path] = []

    def record(
        self: UnifiedHarnessBackendAdapter, *, every_worktree: bool = True
    ) -> None:
        if every_worktree and self.cwd is not None:
            full_passes.append(Path(self.cwd))

    monkeypatch.setattr(UnifiedHarnessBackendAdapter, "schedule_pr_discovery", record)
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "",
                tool_calls=[_tool_call("enter-1", "enter_worktree", {"name": "fix"})],
            ),
            backend_contract_mistral_response("done"),
        ]
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            enabled_tools=["enter_worktree"],
        ),
        capabilities=ClientCapabilities(agent_worktrees=True),
        process_services=process_services,
    )
    try:
        session = await connection.host.open_session()
        async for _event in session.act("fix it"):
            pass

        worktree = _cwd(session)
        assert worktree != tmp_path
        assert worktree in full_passes
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_worktree_the_session_could_not_move_into_is_not_kept(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    _init_repo(tmp_path)

    def refuse(*_args: object) -> None:
        raise RuntimeError("could not follow")

    monkeypatch.setattr(UnifiedPermissionResolver, "move_to", refuse)
    backend_contract_mistral_api.mock(
        side_effect=[
            backend_contract_mistral_response(
                "", tool_calls=[_tool_call("enter-1", "enter_worktree", {})]
            ),
            backend_contract_mistral_response("done"),
        ]
    )
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            enabled_tools=["enter_worktree"],
        ),
        capabilities=ClientCapabilities(agent_worktrees=True),
        process_services=process_services,
    )
    try:
        session = await connection.host.open_session()
        with pytest.raises(AppServerTurnError):
            async for _event in session.act("fix it"):
                pass

        listed = await connection.client.request(
            "artifacts/list", {"sessionId": session.state.session.id}
        )
        assert listed["worktrees"] == []
        assert _cwd(session) == tmp_path
    finally:
        await connection.host.close()


@pytest.mark.both_paths
@pytest.mark.asyncio
async def test_a_worktree_that_is_gone_cannot_be_re_entered(
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    tmp_path: Path,
    process_services: ProcessServices,
) -> None:
    _init_repo(tmp_path)
    entered: list[Path] = []
    results: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        tools = [m for m in payload["messages"] if m["role"] == "tool"]
        users = [m for m in payload["messages"] if m["role"] == "user"]
        if len(users) == 1:
            if tools:
                entered.append(Path(json.loads(tools[0]["content"])["path"]))
                return backend_contract_mistral_response("entered")
            return backend_contract_mistral_response(
                "", tool_calls=[_tool_call("enter-1", "enter_worktree", {})]
            )
        if len(tools) == 1:
            return backend_contract_mistral_response(
                "",
                tool_calls=[
                    _tool_call("enter-2", "enter_worktree", {"path": str(entered[0])})
                ],
            )
        results.append({"content": tools[-1]["content"]})
        return backend_contract_mistral_response("done")

    backend_contract_mistral_api.mock(side_effect=respond)
    connection = await connect_backend_contract_host(
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            enabled_tools=["enter_worktree"],
        ),
        capabilities=ClientCapabilities(agent_worktrees=True),
        process_services=process_services,
    )
    try:
        session = await connection.host.open_session()
        _ = [event async for event in session.act("enter")]
        Repo(tmp_path).git.worktree("remove", "--force", str(entered[0]))

        _ = [event async for event in session.act("go back")]

        assert "this session can re-enter" in str(results[-1]["content"])
        assert _cwd(session) == entered[0]
    finally:
        await connection.host.close()
