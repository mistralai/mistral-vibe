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
import subprocess
from typing import cast

from git import Repo
import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import connect_backend_contract_host
from vibe.app_server._model import validate_wire
from vibe.app_server._worktree_session import SessionWorktrees, WorktreeResolution
from vibe.app_server.events import CallbackRequested
from vibe.app_server.models import (
    CompletedEffectState,
    PublicEffectEntry,
    PublicMessageEntry,
    TextContentBlock,
    WorktreeEffectDetail,
    WorktreeEffectInput,
)
from vibe.app_server.protocol import (
    AutoWorktreeInput,
    ClientCapabilities,
    NewWorktreeInput,
    SessionOptions,
    TurnStartParams,
    TurnStartResponse,
)
from vibe.app_server.session import AppServerSession, AppServerTurnError
from vibe.core.git.worktree import ManagedWorktree


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
    tmp_path: Path, experimental_harness: bool
) -> None:
    _init_repo(tmp_path)
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(branch="jun/contract", name="contract"),
        ),
        capabilities=ClientCapabilities(),
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
    tmp_path: Path, experimental_harness: bool
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
        experimental_harness,
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(branch="jun/hooked", name="hooked"),
        ),
        capabilities=ClientCapabilities(),
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
    tmp_path: Path, experimental_harness: bool
) -> None:
    # The composer's default. The server names it, which is the ordinary case
    # rather than an edge one.
    _init_repo(tmp_path)
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=AutoWorktreeInput(),
        ),
        capabilities=ClientCapabilities(),
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
    tmp_path: Path, experimental_harness: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not experimental_harness:
        pytest.skip("legacy creates the worktree before session/start")

    _init_repo(tmp_path)
    setup_started = asyncio.Event()
    release_setup = asyncio.Event()
    original = SessionWorktrees.resolve_for_start

    async def blocked_resolve(
        worktrees: SessionWorktrees, options: SessionOptions
    ) -> WorktreeResolution:
        if options.worktree is None:
            return await original(worktrees, options)
        setup_started.set()
        await release_setup.wait()
        return await original(worktrees, options)

    monkeypatch.setattr(SessionWorktrees, "resolve_for_start", blocked_resolve)
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(branch="jun/deferred", name="deferred"),
        ),
        capabilities=ClientCapabilities(),
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
async def test_a_session_without_a_worktree_runs_where_it_was_told(
    tmp_path: Path, experimental_harness: bool
) -> None:
    # The other half of the contract: naming no worktree still has to leave the
    # session where the caller put it, on both backends.
    _init_repo(tmp_path)
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(
            cwd=str(tmp_path), workspace_roots=[str(tmp_path)]
        ),
        capabilities=ClientCapabilities(),
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
    experimental_harness: bool,
    backend_contract_mistral_api: respx.Route,
    backend_contract_mistral_response: Callable[..., httpx.Response],
    file_location: str,
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
                            "arguments": json.dumps({
                                "path" if experimental_harness else "file_path": str(
                                    document
                                )
                            }),
                        },
                    }
                ],
            ),
            backend_contract_mistral_response("done"),
        ]
    )
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(
            cwd=str(checkout),
            workspace_roots=[str(checkout), str(extra)],
            worktree=AutoWorktreeInput(),
            enabled_tools=["read_file"],
        ),
        capabilities=ClientCapabilities(callback_kinds=["approval"]),
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
    tmp_path: Path, experimental_harness: bool
) -> None:
    # The hold is what retention pruning reads to avoid deleting a live
    # worktree. A backend that never marks its own can lose its active checkout.
    _init_repo(tmp_path)
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(branch="jun/held", name="held"),
        ),
        capabilities=ClientCapabilities(),
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
    experimental_harness: bool,
    tmp_path: Path,
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
        experimental_harness,
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(branch="jun/gated", name="gated"),
            enabled_tools=["bash"],
            auto_approve=True,
        ),
        capabilities=ClientCapabilities(),
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
    tmp_path: Path, experimental_harness: bool
) -> None:
    """A session that asked to be isolated does not quietly run unisolated.

    Skipped on legacy, which raises the worktree before the session exists and
    so fails the start instead -- the same refusal, one step earlier.
    """
    if not experimental_harness:
        pytest.skip("the legacy runtime fails the start instead")

    # No repository here, so there is nothing to raise a worktree from.
    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(branch="jun/nowhere", name="nowhere"),
        ),
        capabilities=ClientCapabilities(),
    )
    try:
        session = await connection.host.start_session()

        assert _cwd(session) == tmp_path
        # The turn is accepted before deferred setup finishes, then fails as a
        # visible turn instead of making session creation wait for the checkout.
        with pytest.raises(AppServerTurnError):
            _ = [event async for event in session.act("write something")]
    finally:
        await connection.host.close()
