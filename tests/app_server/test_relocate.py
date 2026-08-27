from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from git import Repo
import pytest

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.stubs.app_server import create_test_app_server_session
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server.models import PublicCheckpointEntry, PublicMessageEntry
from vibe.app_server.protocol import AppServerResponseError, ProtocolErrorCode
from vibe.app_server.session import AppServerSession
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.git.worktree import ManagedWorktree, WorktreeRepository
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.core.types import LLMChunk


class _StalledBackend(FakeBackend):
    """A backend whose turn never finishes until the test lets it.

    Holding the turn open is the point. A backend that answers immediately
    releases the execution slot before the next request arrives, so the race
    the test is trying to describe never happens.
    """

    def __init__(self) -> None:
        super().__init__([mock_llm_chunk(content="working")])
        self.released = asyncio.Event()

    async def complete_streaming(self, **kwargs: Any) -> AsyncGenerator[LLMChunk]:
        await self.released.wait()
        async for chunk in super().complete_streaming(**kwargs):
            yield chunk


def _init_repo(root: Path) -> None:
    repo = Repo.init(root, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (root / "file.txt").write_text("hello\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")


def _repo_and_worktree(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    with WorktreeRepository.open(repo) as repository:
        prepared = repository.prepare("feature")
    return repo, prepared.path.resolve()


async def _session_in(cwd: Path, *, backend: FakeBackend | None = None):
    """A session opened the way the app server opens one.

    The working directory is also a workspace root, as `_build_session_config`
    makes it, so the departure directory is in the set a move has to revoke.
    """
    return await create_test_app_server_session(
        build_test_agent_loop(
            cwd=cwd,
            backend=backend or FakeBackend(),
            enable_streaming=backend is not None,
            harness_files=HarnessFilesManager(sources=("project",)).for_session(
                cwd, workspace_roots=[cwd]
            ),
        )
    )


async def _close(session: AppServerSession, *granted: Path) -> None:
    await session.close()
    for path in granted:
        trusted_folders_manager.revoke_session_trust(path)


@pytest.mark.asyncio
async def test_a_move_reports_the_destination_as_the_session_directory(
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_and_worktree(tmp_path)
    session = await _session_in(repo)

    try:
        response = await session.resources.sessions.relocate(str(worktree))

        assert response.state.session.cwd == str(worktree)
        assert str(worktree) in response.state.session.workspace_roots
        assert str(repo) not in response.state.session.workspace_roots
    finally:
        await _close(session, worktree)


@pytest.mark.asyncio
async def test_a_move_keeps_the_session_and_records_a_checkpoint(
    tmp_path: Path,
) -> None:
    # A move is not a handoff. Minting a new id would make the desktop treat the
    # moved session as a different one and lose the conversation it is showing,
    # which is the thing relocation exists to preserve.
    repo, worktree = _repo_and_worktree(tmp_path)
    session = await _session_in(repo)
    original_session_id = session.session_id

    try:
        response = await session.resources.sessions.relocate(str(worktree))

        assert response.state.session.id == original_session_id
        history = response.state.history or []
        assert isinstance(history[-1], PublicCheckpointEntry)
        assert history[-1].kind == "relocation"
        assert history[-1].details == {"cwd": str(worktree), "previousCwd": str(repo)}
    finally:
        await _close(session, worktree)


@pytest.mark.asyncio
async def test_a_move_does_not_repeat_the_conversation_it_preserves(
    tmp_path: Path,
) -> None:
    # The checkpoint folds the turns into stored history. Leaving them on the
    # controller as well means the next read concatenates the two copies, and
    # the session the move exists to preserve comes back saying everything
    # twice with the relocation mark in between.
    repo, worktree = _repo_and_worktree(tmp_path)
    session = await _session_in(repo)

    try:
        _ = [event async for event in session.act("hello", client_message_id="u1")]

        await session.resources.sessions.relocate(str(worktree))
        page = await session.resources.sessions.list_history()

        assert [
            entry.text
            for entry in page.items
            if isinstance(entry, PublicMessageEntry) and entry.role == "user"
        ] == ["hello"]
    finally:
        await _close(session, worktree)


@pytest.mark.asyncio
async def test_a_move_carries_the_worktree_holder_with_it(tmp_path: Path) -> None:
    # A session is held only where it was opened. Without the transfer the
    # destination reads as idle, so another session may remove the checkout this
    # one is standing in, and the departure keeps a marker nobody will clear.
    repo, worktree = _repo_and_worktree(tmp_path)
    session = await _session_in(repo)

    try:
        await session.resources.sessions.relocate(str(worktree))

        held = ManagedWorktree.at(worktree)
        assert held is not None
        assert held.holders() == frozenset({session.session_id})
    finally:
        trusted_folders_manager.revoke_session_trust(worktree)
        await session.close()


@pytest.mark.asyncio
async def test_a_move_that_fails_outright_gives_the_destination_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Refusal is not the only failure. Re-rooting the workspace raises config
    # and IO errors, and the hold is taken before the move: left behind it
    # outlives the session, because close gives back the directory the loop
    # rolled back to rather than the one it was reaching for.
    repo, worktree = _repo_and_worktree(tmp_path)
    agent_loop = build_test_agent_loop(
        cwd=repo,
        backend=FakeBackend(),
        harness_files=HarnessFilesManager(sources=("project",)).for_session(
            repo, workspace_roots=[repo]
        ),
    )
    session = await create_test_app_server_session(agent_loop)

    async def _explode(_cwd: Path) -> None:
        raise OSError("re-rooting the workspace failed")

    monkeypatch.setattr(agent_loop, "relocate", _explode)

    try:
        with pytest.raises(AppServerResponseError):
            await session.resources.sessions.relocate(str(worktree))

        held = ManagedWorktree.at(worktree)
        assert held is None or held.holders() == frozenset()
    finally:
        await _close(session, worktree)


@pytest.mark.asyncio
async def test_a_refused_move_inside_one_worktree_keeps_the_hold(
    tmp_path: Path,
) -> None:
    # The destination resolves to the claim the session already holds, so
    # taking the hold is a no-op. Giving it back on refusal would drop the one
    # the session is standing on, leaving the checkout readable as idle for
    # another session to remove.
    repo, worktree = _repo_and_worktree(tmp_path)
    session = await _session_in(repo)

    try:
        await session.resources.sessions.relocate(str(worktree))
        with pytest.raises(AppServerResponseError):
            await session.resources.sessions.relocate(str(worktree / "absent"))

        held = ManagedWorktree.at(worktree)
        assert held is not None
        assert held.holders() == frozenset({session.session_id})
    finally:
        trusted_folders_manager.revoke_session_trust(worktree)
        await session.close()


@pytest.mark.asyncio
async def test_a_move_to_a_home_relative_path_still_takes_the_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The loop expands before it moves, so a `~` target relocates the session
    # either way. Left unexpanded here the holder lands on a path that never
    # existed, and the checkout the session is now in reads as idle.
    repo, worktree = _repo_and_worktree(tmp_path)
    # Managed worktrees live under the vibe directory rather than the fixture's
    # own, so the home the target is written against has to be theirs.
    monkeypatch.setenv("HOME", str(worktree.parent))
    session = await _session_in(repo)
    relative = Path("~") / worktree.name

    try:
        await session.resources.sessions.relocate(str(relative))

        held = ManagedWorktree.at(worktree)
        assert held is not None
        assert held.holders() == frozenset({session.session_id})
    finally:
        trusted_folders_manager.revoke_session_trust(worktree)
        await session.close()


@pytest.mark.asyncio
async def test_moving_to_the_current_directory_records_nothing(tmp_path: Path) -> None:
    # Selecting the checkout a session is already in is a normal thing to do
    # from a list, and it should read as nothing having happened rather than as
    # a move to where it already was.
    repo, _ = _repo_and_worktree(tmp_path)
    session = await _session_in(repo)

    try:
        response = await session.resources.sessions.relocate(str(repo))

        assert response.state.session.cwd == str(repo)
        assert not [
            entry
            for entry in (response.state.history or [])
            if isinstance(entry, PublicCheckpointEntry) and entry.kind == "relocation"
        ]
    finally:
        await _close(session)


@pytest.mark.asyncio
async def test_a_directory_outside_the_repository_is_a_bad_request(
    tmp_path: Path,
) -> None:
    repo, _ = _repo_and_worktree(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    session = await _session_in(repo)

    try:
        with pytest.raises(AppServerResponseError) as excinfo:
            await session.resources.sessions.relocate(str(elsewhere))

        assert excinfo.value.error.code is ProtocolErrorCode.INVALID_PARAMS
        assert session.state.session.cwd == str(repo)
    finally:
        await _close(session)


@pytest.mark.asyncio
async def test_a_path_that_does_not_exist_is_a_bad_request(tmp_path: Path) -> None:
    repo, _ = _repo_and_worktree(tmp_path)
    session = await _session_in(repo)

    try:
        with pytest.raises(AppServerResponseError) as excinfo:
            await session.resources.sessions.relocate(str(tmp_path / "missing"))

        assert excinfo.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    finally:
        await _close(session)


@pytest.mark.asyncio
async def test_a_busy_session_refuses_the_move(tmp_path: Path) -> None:
    # Refusing rather than queueing, and as a conflict rather than a bad
    # request: the caller learns at once that nothing moved, and that trying
    # again later is the answer.
    repo, worktree = _repo_and_worktree(tmp_path)
    backend = _StalledBackend()
    session = await _session_in(repo, backend=backend)
    turn = session.act("keep the session busy")

    try:
        await anext(turn)

        with pytest.raises(AppServerResponseError) as excinfo:
            await session.resources.sessions.relocate(str(worktree))

        assert excinfo.value.error.code is ProtocolErrorCode.CONFLICT
        assert session.state.session.cwd == str(repo)
    finally:
        backend.released.set()
        await turn.aclose()
        await _close(session)
