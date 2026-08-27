from __future__ import annotations

from pathlib import Path
import shutil

from git import Repo
import pytest

from tests.conftest import build_test_agent_loop
from vibe.core.agent_loop._loop import AgentLoopStateError, _ActiveTurn
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.git.worktree import WorktreeRepository
from vibe.core.trusted_folders import WorkspaceTrustStatus, trusted_folders_manager


def _init_repo(root: Path) -> None:
    repo = Repo.init(root, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (root / "file.txt").write_text("hello\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")


def _repo_and_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A repository and a real linked worktree of it.

    Relocation only accepts a checkout derived from the session's own
    repository, so a pair of bare directories cannot exercise it.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    with WorktreeRepository.open(repo) as repository:
        prepared = repository.prepare("feature")
    return repo, prepared.path.resolve()


def _session_in(cwd: Path):
    """A loop opened the way the app server opens one.

    The working directory is also a workspace root, as `_build_session_config`
    makes it. Without that the departure directory is not in the set a move has
    to revoke, and the interesting case cannot happen.
    """
    return build_test_agent_loop(
        cwd=cwd,
        harness_files=HarnessFilesManager(sources=("project",)).for_session(
            cwd, workspace_roots=[cwd]
        ),
    )


@pytest.mark.asyncio
async def test_a_move_carries_the_write_boundary_with_it(tmp_path: Path) -> None:
    # The boundary a user feels: after the move the new directory is writable
    # and the one left behind is not.
    repo, worktree = _repo_and_worktree(tmp_path)
    agent_loop = _session_in(repo)

    try:
        await agent_loop.relocate(worktree)

        assert agent_loop.cwd == worktree
        workspace = agent_loop.tool_manager.get("read_file").workspace
        assert workspace.allows(worktree / "file.py")
        assert not workspace.allows(repo / "file.py")
    finally:
        trusted_folders_manager.revoke_session_trust(worktree)
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_a_directory_outside_the_repository_is_refused(tmp_path: Path) -> None:
    # The move grants the destination session trust, which outranks an explicit
    # untrust and reaches every descendant. Accepting any directory would hand
    # the session a tree the user never opened, so the target has to be derived
    # from the repository it is already working in.
    repo, _ = _repo_and_worktree(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    agent_loop = _session_in(repo)

    try:
        with pytest.raises(AgentLoopStateError):
            await agent_loop.relocate(elsewhere)

        assert agent_loop.cwd == repo
        assert (
            trusted_folders_manager.trust_status(elsewhere)
            is not WorkspaceTrustStatus.SESSION
        )
    finally:
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_a_session_can_return_to_its_repository(tmp_path: Path) -> None:
    # Leaving a worktree is a move like any other, so the repository root has to
    # count as a destination.
    repo, worktree = _repo_and_worktree(tmp_path)
    agent_loop = _session_in(worktree)

    try:
        await agent_loop.relocate(repo)

        assert agent_loop.cwd == repo
    finally:
        trusted_folders_manager.revoke_session_trust(repo)
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_a_move_is_refused_while_a_turn_is_active(tmp_path: Path) -> None:
    # A tool call emitted alongside the move would run against the old
    # directory, and swapping the objects it holds cannot stop it.
    repo, worktree = _repo_and_worktree(tmp_path)
    agent_loop = _session_in(repo)
    agent_loop._active_turn = _ActiveTurn()

    try:
        with pytest.raises(AgentLoopStateError):
            await agent_loop.relocate(worktree)

        assert agent_loop.cwd == repo
        assert agent_loop.harness_files.cwd == repo
    finally:
        agent_loop._active_turn = None
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_a_failed_rebuild_leaves_the_session_where_it_was(
    tmp_path: Path, monkeypatch
) -> None:
    repo, worktree = _repo_and_worktree(tmp_path)
    agent_loop = _session_in(repo)

    def explode(*args, **kwargs):
        raise RuntimeError("rebuild failed")

    monkeypatch.setattr(agent_loop, "_prepare_reload", explode)

    try:
        with pytest.raises(RuntimeError):
            await agent_loop.relocate(worktree)

        assert agent_loop.cwd == repo
        assert agent_loop.harness_files.cwd == repo
    finally:
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_moving_somewhere_that_is_not_a_directory_is_refused(
    tmp_path: Path,
) -> None:
    repo, _ = _repo_and_worktree(tmp_path)
    agent_loop = _session_in(repo)

    try:
        with pytest.raises(AgentLoopStateError):
            await agent_loop.relocate(tmp_path / "does-not-exist")

        assert agent_loop.cwd == repo
    finally:
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_agent_discovery_follows_the_move(tmp_path: Path) -> None:
    # Search paths are resolved once when the registry is built, so without
    # rebinding the session keeps offering the agents of the directory it left.
    repo, worktree = _repo_and_worktree(tmp_path)
    (repo / ".vibe" / "agents").mkdir(parents=True)
    (worktree / ".vibe" / "agents").mkdir(parents=True)
    agent_loop = _session_in(repo)

    try:
        assert (
            repo / ".vibe" / "agents" in agent_loop.agent_manager._registry.search_paths
        )

        await agent_loop.relocate(worktree)

        paths = agent_loop.agent_manager._registry.search_paths
        assert worktree / ".vibe" / "agents" in paths
        assert repo / ".vibe" / "agents" not in paths
    finally:
        trusted_folders_manager.revoke_session_trust(worktree)
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_a_move_trusts_the_destination_for_the_session(tmp_path: Path) -> None:
    # Required by the config re-root: an untrusted layer contributes nothing,
    # so re-rooting without this would leave the session with no project config
    # rather than the destination's.
    repo, worktree = _repo_and_worktree(tmp_path)
    agent_loop = _session_in(repo)

    try:
        await agent_loop.relocate(worktree)

        assert (
            trusted_folders_manager.trust_status(worktree)
            is WorkspaceTrustStatus.SESSION
        )
    finally:
        trusted_folders_manager.revoke_session_trust(worktree)
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_a_failed_move_leaves_no_trust_behind(
    tmp_path: Path, monkeypatch
) -> None:
    # A grant outliving the move it belonged to would widen the session for a
    # directory it never entered, and raise the trust root of the one it is
    # still in whenever the destination sits above it.
    repo, worktree = _repo_and_worktree(tmp_path)
    agent_loop = _session_in(repo)

    def explode(*args, **kwargs):
        raise RuntimeError("rebuild failed")

    monkeypatch.setattr(agent_loop, "_prepare_reload", explode)

    try:
        with pytest.raises(RuntimeError):
            await agent_loop.relocate(worktree)

        assert (
            trusted_folders_manager.trust_status(worktree)
            is not WorkspaceTrustStatus.SESSION
        )
    finally:
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_a_session_in_a_subdirectory_can_come_back(tmp_path: Path) -> None:
    # Moving out of repo/sub lands in worktree/sub, so the return trip names
    # repo/sub, which is neither a checkout root nor anything linked() reports.
    # Matching the roots exactly would let a session leave and strand it there.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    git = Repo(repo)
    (repo / "sub").mkdir()
    (repo / "sub" / "f.txt").write_text("x\n")
    git.index.add(["sub/f.txt"])
    git.index.commit("add sub")
    sub = (repo / "sub").resolve()
    with WorktreeRepository.open(sub) as repository:
        prepared = repository.prepare("feature")
    worktree_sub = prepared.path.resolve()
    agent_loop = _session_in(sub)

    try:
        await agent_loop.relocate(worktree_sub)
        assert agent_loop.cwd == worktree_sub

        await agent_loop.relocate(sub)

        assert agent_loop.cwd == sub
    finally:
        trusted_folders_manager.revoke_session_trust(worktree_sub)
        trusted_folders_manager.revoke_session_trust(sub)
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_a_subdirectory_move_can_read_the_destination_project_config(
    tmp_path: Path,
) -> None:
    # The layer finds `.vibe` by walking up from the working directory and asks
    # whether the directory holding it is trusted. Trust resolves upward too, so
    # a grant on repo/sub never reaches the root the file sits at, and the move
    # would land with no project config - the thing re-rooting exists to carry.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    git = Repo(repo)
    (repo / "sub").mkdir()
    (repo / "sub" / "f.txt").write_text("x\n")
    git.index.add(["sub/f.txt"])
    git.index.commit("add sub")
    sub = (repo / "sub").resolve()
    with WorktreeRepository.open(sub) as repository:
        prepared = repository.prepare("feature")
    worktree_sub = prepared.path.resolve()
    worktree_root = prepared.root.resolve()
    agent_loop = _session_in(sub)

    try:
        await agent_loop.relocate(worktree_sub)

        assert (
            trusted_folders_manager.trust_status(worktree_root / ".vibe")
            is WorkspaceTrustStatus.SESSION
        )
    finally:
        trusted_folders_manager.revoke_session_trust(worktree_root)
        await agent_loop.aclose()


def _repo_with_subdirs(tmp_path: Path) -> tuple[Path, Path]:
    """A repository whose committed tree has two sibling directories."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    git = Repo(repo)
    for name in ("sub", "sibling"):
        (repo / name).mkdir()
        (repo / name / "f.txt").write_text("x\n")
    git.index.add(["sub/f.txt", "sibling/f.txt"])
    git.index.commit("add subdirectories")
    return repo.resolve(), (repo / "sub").resolve()


@pytest.mark.asyncio
async def test_a_subdirectory_session_cannot_climb_to_the_repository(
    tmp_path: Path,
) -> None:
    # moved_to makes the destination the write root, so allowing the parent
    # would turn a session scoped to one package into one scoped to the whole
    # repository. That is authority growing across a move, which D3 forbids.
    repo, sub = _repo_with_subdirs(tmp_path)
    agent_loop = _session_in(sub)

    try:
        with pytest.raises(AgentLoopStateError):
            await agent_loop.relocate(repo)

        assert agent_loop.cwd == sub
    finally:
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_a_subdirectory_session_cannot_step_sideways(tmp_path: Path) -> None:
    # A sibling package is inside the repository but is not where this session
    # sits, so it is no more derived than a path outside the repository.
    repo, sub = _repo_with_subdirs(tmp_path)
    sibling = (repo / "sibling").resolve()
    agent_loop = _session_in(sub)

    try:
        with pytest.raises(AgentLoopStateError):
            await agent_loop.relocate(sibling)

        assert agent_loop.cwd == sub
        assert (
            trusted_folders_manager.trust_status(sibling)
            is not WorkspaceTrustStatus.SESSION
        )
    finally:
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_a_counterpart_that_leaves_the_checkout_is_refused(
    tmp_path: Path,
) -> None:
    # linked() resolves its paths through checks that refuse a symlink leaving
    # the checkout. Resolving the main checkout's counterpart directly would
    # skip them, and the escaped path would then be granted session trust.
    repo, sub = _repo_with_subdirs(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    with WorktreeRepository.open(sub) as repository:
        prepared = repository.prepare("feature")
    worktree_sub = prepared.path.resolve()
    # Local, not committed: the worktree keeps a real directory while the main
    # checkout points somewhere else entirely.
    shutil.rmtree(sub)
    sub.symlink_to(outside, target_is_directory=True)
    agent_loop = _session_in(worktree_sub)

    try:
        with pytest.raises(AgentLoopStateError):
            await agent_loop.relocate(outside.resolve())

        assert agent_loop.cwd == worktree_sub
        assert (
            trusted_folders_manager.trust_status(outside.resolve())
            is not WorkspaceTrustStatus.SESSION
        )
    finally:
        trusted_folders_manager.revoke_session_trust(worktree_sub)
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_a_counterpart_redirected_inside_the_checkout_is_refused(
    tmp_path: Path,
) -> None:
    # The escape check above only asks whether the counterpart stayed inside
    # the worktree. A link landing on a sibling passes it, and that sibling
    # then becomes a place the session may move to, is granted trust over and
    # writes into - the sideways move forbidden by name, reached by a link
    # instead. Committable on the destination branch, so not only a local
    # mistake.
    repo, sub = _repo_with_subdirs(tmp_path)
    with WorktreeRepository.open(sub) as repository:
        prepared = repository.prepare("feature")
    worktree_sibling = (prepared.root / "sibling").resolve()
    shutil.rmtree(prepared.root / "sub")
    (prepared.root / "sub").symlink_to(worktree_sibling, target_is_directory=True)
    agent_loop = _session_in(sub)

    try:
        with pytest.raises(AgentLoopStateError):
            await agent_loop.relocate(worktree_sibling)

        assert agent_loop.cwd == sub
        assert (
            trusted_folders_manager.trust_status(worktree_sibling)
            is not WorkspaceTrustStatus.SESSION
        )
    finally:
        trusted_folders_manager.revoke_session_trust(worktree_sibling)
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_trust_does_not_accumulate_across_moves(tmp_path: Path) -> None:
    # moved_to already stops workspace roots from accumulating. Trust has to
    # follow, or a sequence of moves leaves every directory it passed through
    # session-trusted, each one outranking a later explicit untrust.
    repo, worktree = _repo_and_worktree(tmp_path)
    agent_loop = _session_in(repo)

    try:
        await agent_loop.relocate(worktree)
        await agent_loop.relocate(repo)

        assert (
            trusted_folders_manager.trust_status(worktree)
            is not WorkspaceTrustStatus.SESSION
        )
    finally:
        trusted_folders_manager.revoke_session_trust(repo)
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_a_move_is_refused_while_an_operation_holds_the_session(
    tmp_path: Path,
) -> None:
    # A turn is not the only thing in flight. Teleport runs outside one, reads
    # the repository at the session's directory and pushes its branch, so a
    # move landing mid-run would ship the checkout the session had left.
    repo, worktree = _repo_and_worktree(tmp_path)
    agent_loop = _session_in(repo)
    agent_loop._holders.append("teleport")

    try:
        with pytest.raises(AgentLoopStateError):
            await agent_loop.relocate(worktree)

        assert agent_loop.cwd == repo
        assert (
            trusted_folders_manager.trust_status(worktree)
            is not WorkspaceTrustStatus.SESSION
        )
    finally:
        agent_loop._holders.clear()
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_the_move_is_allowed_once_the_holder_releases(tmp_path: Path) -> None:
    repo, worktree = _repo_and_worktree(tmp_path)
    agent_loop = _session_in(repo)
    agent_loop._holders.append("teleport")
    agent_loop._holders.remove("teleport")

    try:
        await agent_loop.relocate(worktree)

        assert agent_loop.cwd == worktree
    finally:
        trusted_folders_manager.revoke_session_trust(worktree)
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_a_move_does_not_release_a_grant_it_did_not_make(tmp_path: Path) -> None:
    # The trust store is process-wide and counts grants. Session start makes
    # one for the directory it opens in, and another session may be sitting
    # there too, so releasing the departure outright takes back somebody
    # else's.
    repo, worktree = _repo_and_worktree(tmp_path)
    trusted_folders_manager.trust_for_session(repo)
    agent_loop = _session_in(repo)

    try:
        await agent_loop.relocate(worktree)

        assert (
            trusted_folders_manager.trust_status(repo) is WorkspaceTrustStatus.SESSION
        )
    finally:
        trusted_folders_manager.revoke_session_trust(repo)
        trusted_folders_manager.revoke_session_trust(worktree)
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_an_operation_cannot_start_while_a_move_holds_the_session(
    tmp_path: Path,
) -> None:
    # The other direction of the same rule. A move checks once and then awaits
    # twice, so an operation that only announced itself could still start in
    # that window and run against a directory moving under it.
    repo, _ = _repo_and_worktree(tmp_path)
    agent_loop = _session_in(repo)
    agent_loop._take_session("relocation")

    try:
        with pytest.raises(AgentLoopStateError):
            agent_loop._take_session("teleport")
    finally:
        agent_loop._release_session("relocation")
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_a_turn_cannot_start_while_a_move_holds_the_session(
    tmp_path: Path,
) -> None:
    # The hole the two rules above leave between them. A turn is not a holder,
    # so _take_session cannot refuse it, and a move is not a turn, so the check
    # a turn does make cannot see one. Both are true across the awaits a move
    # runs, which is exactly when a turn must not begin.
    repo, _ = _repo_and_worktree(tmp_path)
    agent_loop = _session_in(repo)
    agent_loop._take_session("relocation")

    try:
        with pytest.raises(AgentLoopStateError):
            async for _ in agent_loop.act("go"):
                pass

        assert agent_loop._active_turn is None
    finally:
        agent_loop._release_session("relocation")
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_the_recorded_directory_follows_the_move(tmp_path: Path) -> None:
    # The logger holds its own copy and is what writes the directory into
    # session metadata, so a move that skipped it would be recorded as never
    # having happened.
    repo, worktree = _repo_and_worktree(tmp_path)
    agent_loop = _session_in(repo)

    try:
        await agent_loop.relocate(worktree)

        assert agent_loop.session_logger.cwd == worktree
    finally:
        trusted_folders_manager.revoke_session_trust(worktree)
        await agent_loop.aclose()
