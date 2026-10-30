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

from tests.app_server.backend_contract.conftest import connect_backend_contract_host
from vibe.app_server._model import validate_wire
from vibe.app_server._process_services import ProcessServices
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
