"""Where a session runs when the caller asked for a worktree, and who holds it.

Contract-level rather than backend-level on purpose. Both halves of this used
to live inside the legacy runtime, so the unified harness silently ignored
every `worktree` a client sent and never marked one as occupied -- the
composer's pick reached a backend that had never heard of it, and the sweep
that reclaims abandoned worktrees could not tell a live one from a stale one.
These run against whichever backend the suite is pointed at, so that cannot
come back.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection
from pathlib import Path

from git import Repo
import pytest

from tests.app_server.backend_contract.conftest import connect_backend_contract_host
from vibe.app_server.protocol import (
    AutoWorktreeInput,
    ClientCapabilities,
    NewWorktreeInput,
    SessionOptions,
)
from vibe.app_server.session import AppServerSession
from vibe.core.git.worktree import ManagedWorktree, WorktreeRepository


def _cwd(session: AppServerSession) -> Path:
    # A started session always has one; the protocol type allows None because
    # the field is optional on the way in, not on the way out.
    cwd = session.state.session.cwd
    assert cwd is not None
    return Path(cwd)


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
        cwd = _cwd(session)

        # Somewhere else entirely, not the directory the request named.
        assert cwd != tmp_path
        assert cwd.is_dir()
        assert Repo(cwd).active_branch.name == "jun/contract"
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
        cwd = _cwd(session)

        assert cwd != tmp_path
        assert cwd.is_dir()
    finally:
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
async def test_a_session_holds_its_worktree_and_lets_go_on_close(
    tmp_path: Path, experimental_harness: bool
) -> None:
    # The hold is what the claim sweep reads to tell a worktree in use from one
    # abandoned. A backend that never marks its own is a backend whose live
    # checkouts the sweep is free to delete.
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
        managed = ManagedWorktree.at(_cwd(session))
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
async def test_attaching_a_session_sweeps_the_repository_for_abandoned_worktrees(
    tmp_path: Path, experimental_harness: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whether the sweep runs, not what it removes.

    What it removes is `tests/core/test_worktree.py`'s subject and needs a claim
    aged past the grace period to say anything. What differs between backends is
    simply whether attaching a session runs it at all: legacy did and the
    unified harness did not, so worktrees accumulated there forever.
    """
    _init_repo(tmp_path)
    swept: asyncio.Queue[tuple[Path, tuple[Path, ...]]] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def record(base: Path, *, in_use: Collection[Path]) -> None:
        # Called on a worker thread, so the queue is fed from the loop's side.
        loop.call_soon_threadsafe(swept.put_nowait, (base, tuple(in_use)))

    monkeypatch.setattr(WorktreeRepository, "sweep_claims", record)

    connection = await connect_backend_contract_host(
        experimental_harness,
        session_options=SessionOptions(
            cwd=str(tmp_path),
            workspace_roots=[str(tmp_path)],
            worktree=NewWorktreeInput(branch="jun/swept", name="swept"),
        ),
        capabilities=ClientCapabilities(),
    )
    try:
        session = await connection.host.start_session()

        # The sweep is scheduled rather than awaited, so the session is usable
        # while it runs. Waiting on it is the test's problem, not the caller's.
        base, in_use = await asyncio.wait_for(swept.get(), timeout=5)

        assert base == _cwd(session)
        # Passed rather than defaulted: a sweep told nothing is in use is free
        # to delete every worktree a saved session would have resumed into.
        assert isinstance(in_use, tuple)
    finally:
        await connection.host.close()
