from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock

from git import Repo
import pytest
import tomli_w

from tests.stubs.app_server import FakeSessionBackendServices
from vibe.app_server import _runtime as runtime
import vibe.app_server._host as host_module
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._worktree_cleanup import session_finished
from vibe.app_server._worktree_session import SessionWorktrees
from vibe.app_server.protocol import (
    AutoWorktreeInput,
    ClientInfo,
    ExistingWorktreeInput,
    NewWorktreeInput,
    SessionOptions,
    WorkspaceGitCheckoutsParams,
    WorkspaceGitCheckoutsResponse,
    WorkspaceWorktreeLimitUpdateParams,
    WorkspaceWorktreeLimitUpdateResponse,
    WorkspaceWorktreeListParams,
    WorkspaceWorktreeListResponse,
    WorkspaceWorktreePruneParams,
    WorkspaceWorktreePruneResponse,
)
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.git.errors import GitError, GitUnavailableError
from vibe.core.git.worktree import (
    SNAPSHOT_REF_PREFIX,
    LinkedWorktree,
    ManagedWorktree,
    PreparedWorktree,
    WorktreeError,
    WorktreeRelease,
    WorktreeReleaseOutcome,
    WorktreeRepository,
)
import vibe.core.git.worktree.record as worktree_record
import vibe.core.git.worktree.repository as worktree_module
import vibe.core.session.worktrees as worktree_lifecycle_module
from vibe.core.session.worktrees import (
    CreateNamedWorktree,
    CreateWorktreeForPrompt,
    SessionWorktrees as WorktreeLifecycle,
    UseExistingWorktree,
)
from vibe.core.trusted_folders import trusted_folders_manager


def test_local_harness_options_preserves_client_positional_argument() -> None:
    client = runtime.ClientDescriptor(info=ClientInfo(name="test", version="1"))

    options = runtime.LocalHarnessOptions(client)

    assert options.client is client


# The API is repository-scoped; these keep a test that only cares about the
# resulting worktree down to a single call.


def _prepare(name: str, base: Path, *, branch: str | None = None) -> PreparedWorktree:
    with WorktreeRepository.open(base) as repository:
        return repository.prepare(name, branch=branch)


def _prepare_auto(base: Path, *, prompt: str | None = None) -> PreparedWorktree:
    with WorktreeRepository.open(base) as repository:
        return repository.prepare_auto(prompt=prompt)


def _finish_worktree_start(worktree: PreparedWorktree) -> None:
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    managed.hold("test-session")
    managed.release_holder("test-session")


def _linked(base: Path) -> tuple[LinkedWorktree, ...]:
    with WorktreeRepository.open(base) as repository:
        return repository.linked()


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

    resolution = SessionWorktrees.resolve(
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

    resolution = SessionWorktrees.resolve(
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

    resolution = SessionWorktrees.resolve(
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

    monkeypatch.setattr(worktree_lifecycle_module, "suggest_worktree_name", suggest)

    suggested = await WorktreeLifecycle()._suggest_name(
        CreateWorktreeForPrompt(prompt="Fix the login bug"), tmp_path
    )

    assert suggested == "repair-oauth-redirect"
    assert asked == ["Fix the login bug"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_",
    [
        UseExistingWorktree(cwd=Path("/tmp/somewhere")),
        CreateNamedWorktree(name="feat-x", branch="feat/x"),
    ],
)
async def test_only_an_auto_worktree_pays_for_a_model_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, request_: Any
) -> None:
    async def explode(*_args: Any, **_kwargs: Any) -> str:
        raise AssertionError("a named worktree must not wait on the model")

    monkeypatch.setattr(worktree_lifecycle_module, "suggest_worktree_name", explode)

    assert await WorktreeLifecycle()._suggest_name(request_, tmp_path) is None


def test_session_start_worktree_auto_selection_is_unique_per_call(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)

    def resolve() -> str:
        resolution = SessionWorktrees.resolve(
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

    resolution = SessionWorktrees.resolve(
        SessionOptions(cwd=str(tmp_path), worktree=AutoWorktreeInput())
    )

    assert resolution.prepared_worktree is not None
    assert resolution.prepared_worktree.name == "brave-quiet-otter"


def test_session_start_worktree_existing_selection_resolves_cwd(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = _prepare("existing-worktree", tmp_path, branch="feat/existing-worktree")
    _finish_worktree_start(worktree)

    resolution = SessionWorktrees.resolve(
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
    assert resolution.pending_hold is not None
    assert ManagedWorktree.prune(limit=0) == 0
    resolution.pending_hold.release()


def test_session_start_from_managed_worktree_holds_it_for_attachment(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    worktree = _prepare("existing-worktree", tmp_path, branch="feat/existing-worktree")
    _finish_worktree_start(worktree)

    resolution = SessionWorktrees.resolve(SessionOptions(cwd=str(worktree.path)))

    assert resolution.pending_hold is not None
    assert ManagedWorktree.prune(limit=0) == 0
    resolution.pending_hold.release()


@pytest.mark.asyncio
async def test_forking_a_managed_workspace_resolves_an_independent_worktree(
    tmp_path: Path,
) -> None:
    """*Prepare*: Session options targeting a managed worktree and one extra root.
    *Do*: Resolve the workspace for a fork.
    *Assert*: The cwd root moves to a new worktree while the extra root remains.
    """
    # Prepare
    _init_repo(tmp_path)
    source = _prepare("source", tmp_path, branch="vibe/source")
    _finish_worktree_start(source)
    extra_root = tmp_path / "attachments"
    extra_root.mkdir()

    # Do
    resolution = await SessionWorktrees().resolve_for_fork(
        SessionOptions(
            cwd=str(source.path), workspace_roots=[str(source.path), str(extra_root)]
        )
    )

    # Assert
    assert resolution.options.cwd is not None
    fork_cwd = Path(resolution.options.cwd)
    assert fork_cwd != source.path
    assert resolution.options.workspace_roots == [str(fork_cwd), str(extra_root)]
    assert resolution.prepared_worktree is not None
    assert resolution.prepared_worktree.branch == "vibe/source-fork"
    assert resolution.pending_hold is None
    await SessionWorktrees().cleanup(resolution)


@pytest.mark.asyncio
async def test_forking_an_unmanaged_workspace_keeps_its_existing_cwd(
    tmp_path: Path,
) -> None:
    """*Prepare*: Session options targeting an ordinary directory.
    *Do*: Resolve the workspace for a fork.
    *Assert*: No worktree is created and the original cwd and roots are retained.
    """
    # Prepare
    extra_root = tmp_path / "attachments"
    extra_root.mkdir()
    options = SessionOptions(
        cwd=str(tmp_path), workspace_roots=[str(tmp_path), str(extra_root)]
    )

    # Do
    resolution = await SessionWorktrees().resolve_for_fork(options)

    # Assert
    assert resolution.options == options
    assert resolution.prepared_worktree is None
    assert resolution.pending_hold is None


@pytest.mark.asyncio
async def test_forking_a_reaped_managed_workspace_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A managed source whose claim disappears before attachment.
    *Do*: Resolve that source for a fork.
    *Assert*: Forking fails instead of silently sharing the source checkout.
    """
    # Prepare
    _init_repo(tmp_path)
    source = _prepare("source", tmp_path, branch="vibe/source")
    _finish_worktree_start(source)
    monkeypatch.setattr(ManagedWorktree, "hold_for_attachment", lambda _self: None)

    # Do / Assert
    with pytest.raises(WorktreeError, match="no longer available"):
        await SessionWorktrees().resolve_for_fork(SessionOptions(cwd=str(source.path)))


def test_worktree_remove_counts_the_calling_sessions_holder(tmp_path: Path) -> None:
    # The plain call keeps its historical contract: no holder discount, so a
    # live session's own holder keeps the worktree. Only the confirm flow
    # (`force`/`inspect`) discounts the asking session.
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _finish_worktree_start(worktree)
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    managed.hold("quitting-session")

    response = host_module.worktree_remove_response(
        worktree.root, session_id="quitting-session"
    )

    assert response.outcome == WorktreeReleaseOutcome.KEPT_IN_USE
    assert worktree.root.is_dir()


def test_worktree_remove_reports_unreadable_holders_as_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _finish_worktree_start(worktree)

    def unreadable(self: worktree_record.WorktreeClaim) -> frozenset[str]:
        raise PermissionError("holders directory unreadable")

    monkeypatch.setattr(worktree_record.WorktreeClaim, "holders", unreadable)
    response = host_module.worktree_remove_response(worktree.root)

    assert response.outcome == "kept_error"
    assert response.reasons == ["holders directory unreadable"]
    assert worktree.root.is_dir()


def test_worktree_remove_force_discards_while_the_calling_session_holds(
    tmp_path: Path,
) -> None:
    repo = _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _finish_worktree_start(worktree)
    (worktree.root / "unsaved.txt").write_text("work\n")
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    managed.hold("quitting-session")

    response = host_module.worktree_remove_response(
        worktree.root, force=True, session_id="quitting-session"
    )

    assert response.outcome == WorktreeReleaseOutcome.REMOVED
    assert not worktree.root.exists()
    # The caller confirmed discarding, so nothing was saved.
    assert not any(
        head.name == f"{SNAPSHOT_REF_PREFIX}/{worktree.name}"
        for head in repo.references
    )


def test_worktree_remove_inspect_reports_a_git_failure_as_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Keeping a worktree is never a fault the caller can act on, and the
    # probe shells out to git like the removal does: the same failure maps
    # to kept_error instead of a protocol error.
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _finish_worktree_start(worktree)

    def failing(
        self: ManagedWorktree, session_id: str | None = None
    ) -> WorktreeRelease:
        raise GitError("git status failed")

    monkeypatch.setattr(ManagedWorktree, "probe_release", failing)
    response = host_module.worktree_remove_response(worktree.root, inspect=True)

    assert response.outcome == "kept_error"
    assert response.reasons == ["git status failed"]
    assert worktree.root.is_dir()


def test_worktree_remove_inspect_reports_a_clean_worktree_without_removing(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _finish_worktree_start(worktree)
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    managed.hold("quitting-session")

    response = host_module.worktree_remove_response(
        worktree.root, session_id="quitting-session", inspect=True
    )

    # "removed" is the would-be outcome: nothing moved, nothing released.
    assert response.outcome == WorktreeReleaseOutcome.REMOVED
    assert worktree.root.is_dir()
    assert managed.claim.read() is not None
    assert managed.holders() == frozenset({"quitting-session"})
    assert response.holders == 0


def test_worktree_remove_inspect_keeps_a_dirty_worktree_and_its_holders(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _finish_worktree_start(worktree)
    (worktree.root / "unsaved.txt").write_text("work\n")
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    managed.hold("quitting-session")

    response = host_module.worktree_remove_response(
        worktree.root, session_id="quitting-session", inspect=True
    )

    assert response.outcome == WorktreeReleaseOutcome.KEPT_DIRTY
    assert response.reasons == ["untracked files"]
    assert response.holders == 0
    assert worktree.root.is_dir()
    assert managed.holders() == frozenset({"quitting-session"})


def test_worktree_remove_inspect_counts_other_holders(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _finish_worktree_start(worktree)
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    managed.hold("quitting-session")
    managed.hold("live-session")

    response = host_module.worktree_remove_response(
        worktree.root, session_id="quitting-session", inspect=True
    )

    assert response.outcome == WorktreeReleaseOutcome.KEPT_IN_USE
    # The asking session's own holder is discounted from the count.
    assert response.holders == 1
    assert worktree.root.is_dir()


def test_worktree_remove_keeps_when_another_session_holds(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path)
    _finish_worktree_start(worktree)
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    managed.hold("quitting-session")
    managed.hold("live-session")

    response = host_module.worktree_remove_response(
        worktree.root, session_id="quitting-session"
    )

    assert response.outcome == WorktreeReleaseOutcome.KEPT_IN_USE
    assert worktree.root.is_dir()


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
async def test_passive_host_uses_worktree_limit(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (config_dir / "config.toml").write_text("worktree_limit = 25\n")
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    prune = Mock(return_value=2)
    monkeypatch.setattr(ManagedWorktree, "prune", prune)
    cleanup = Mock()
    monkeypatch.setattr("vibe.app_server._host.worktree_cleanup", lambda: cleanup)
    prune_result = await handler.dispatch(
        "workspace/git/worktrees/prune",
        WorkspaceWorktreePruneParams().model_dump(mode="json", by_alias=True),
    )

    prune_response = cast(WorkspaceWorktreePruneResponse, prune_result.response)
    assert prune_response.removed == 2
    prune.assert_called_once_with(25, session_finished)
    cleanup.sweep.assert_called_once_with()


@pytest.mark.asyncio
async def test_passive_host_updates_worktree_limit(config_dir: Path) -> None:
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "workspace/git/worktrees/limit/update",
        WorkspaceWorktreeLimitUpdateParams(limit=0).model_dump(
            mode="json", by_alias=True
        ),
    )

    response = cast(WorkspaceWorktreeLimitUpdateResponse, result.response)
    assert response.limit == 0
    assert response.failures == []
    assert "worktree_limit = 0" in (config_dir / "config.toml").read_text()


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
async def test_passive_host_reports_the_counterpart_of_a_retained_worktree(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    worktree = _prepare_auto(tmp_path, prompt="Retain this worktree")
    _finish_worktree_start(worktree)
    retained_cwd = worktree.root / "packages" / "api"
    retained_cwd.mkdir(parents=True)
    (retained_cwd / "saved.txt").write_text("saved\n")
    assert ManagedWorktree.prune(limit=0) == 1
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "workspace/git/worktrees/list",
        WorkspaceWorktreeListParams(
            cwd=str(worktree.root / "packages" / "api")
        ).model_dump(mode="json", by_alias=True),
    )

    response = cast(WorkspaceWorktreeListResponse, result.response)
    assert response.repository_cwd is None
    assert response.repository_mapped_cwd == str(tmp_path / "packages" / "api")
    assert response.repository_root == str(tmp_path)


@pytest.mark.asyncio
async def test_retained_worktree_listing_ignores_a_repository_above_its_leftover_path(
    config_dir: Path, tmp_path: Path
) -> None:
    repo_root = tmp_path / "project"
    _init_repo(repo_root)
    worktree = _prepare_auto(repo_root, prompt="Retain this worktree")
    _finish_worktree_start(worktree)
    retained_cwd = worktree.root / "packages" / "api"
    retained_cwd.mkdir(parents=True)
    (retained_cwd / "saved.txt").write_text("saved\n")
    assert ManagedWorktree.prune(limit=0) == 1
    retained_cwd.mkdir(parents=True)
    _init_repo(config_dir.parent)
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    result = await handler.dispatch(
        "workspace/git/worktrees/list",
        WorkspaceWorktreeListParams(cwd=str(retained_cwd)).model_dump(
            mode="json", by_alias=True
        ),
    )

    response = cast(WorkspaceWorktreeListResponse, result.response)
    assert response.repository_cwd is None
    assert response.repository_mapped_cwd == str(repo_root / "packages" / "api")
    assert response.repository_root == str(repo_root)


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
        "repositoryMappedCwd": None,
        "repositoryRoot": None,
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
        "repositoryMappedCwd": None,
        "repositoryRoot": None,
    }


@pytest.mark.asyncio
async def test_config_load_is_bound_to_harness_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    config_dir = project / ".vibe"
    config_dir.mkdir(parents=True)
    with (config_dir / "config.toml").open("wb") as file:
        tomli_w.dump({"default_agent": "ask"}, file)
    trusted_folders_manager.trust_for_session(project)
    monkeypatch.chdir(tmp_path)

    harness_files = HarnessFilesManager(sources=("project",), cwd=project)

    loaded = await runtime.build_default_orchestrator(harness_files=harness_files)
    assert loaded.config.default_agent == "ask"

    monkeypatch.setenv("VIBE_DEFAULT_AGENT", "lean")
    loaded = await runtime.build_default_orchestrator(harness_files=harness_files)
    assert loaded.config.default_agent == "lean"


def test_experimental_harness_process_selects_the_unified_harness_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: Experimental Harness construction before client initialization.
    *Do*: Create the selected backend Host while client metadata is unavailable.
    *Assert*: Host selection succeeds without eagerly reading telemetry metadata.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendHostAdapter,
    )

    selected = SimpleNamespace(
        harness_kind="unified", configure_hook_handlers=lambda _handlers: None
    )
    monkeypatch.setattr(
        runtime, "create_experimental_harness_host", lambda *_args, **_kwargs: selected
    )
    services = FakeSessionBackendServices()

    def unavailable_client_info() -> ClientInfo:
        raise RuntimeError("App-server client metadata is unavailable")

    monkeypatch.setattr(services, "client_info", unavailable_client_info)

    # Do
    process = runtime.HarnessProcess()
    host = process.create_session_backend_host(services)

    # Assert
    # The process hands the app server the Vibe-side adapter, not the raw
    # Harness Host, so the two protocol shapes only meet in one place.
    assert isinstance(host, UnifiedHarnessBackendHostAdapter)
    assert host.harness_kind == selected.harness_kind


def test_default_process_selects_the_unified_harness_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unflagged startup is the hard default: it must construct the Unified
    Runtime Host and hand out the unified adapter, never the legacy backend.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendHostAdapter,
    )

    selected = SimpleNamespace(
        harness_kind="unified", configure_hook_handlers=lambda _handlers: None
    )
    monkeypatch.setattr(
        runtime, "create_experimental_harness_host", lambda *_args, **_kwargs: selected
    )

    process = runtime.HarnessProcess()
    host = process.create_session_backend_host(FakeSessionBackendServices())

    assert isinstance(host, UnifiedHarnessBackendHostAdapter)
    assert host.harness_kind == selected.harness_kind


def test_a_host_that_cannot_take_builtin_hooks_fails_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Host created but failing before the builtin-hook registry is
    configured (a harness version without ``configure_hook_handlers``) must
    abort startup with the actionable error — and must not leave the unified
    host assigned, which would hand out the unified adapter while the process
    reports a failed startup.
    """
    from vibe._experimental_harness import ExperimentalHarnessUnavailableError

    def without_registry() -> object:
        return SimpleNamespace()  # no configure_hook_handlers

    monkeypatch.setattr(runtime, "create_experimental_harness_host", without_registry)

    with pytest.raises(ExperimentalHarnessUnavailableError) as excinfo:
        runtime.HarnessProcess(HarnessFilesManager(sources=()))

    message = str(excinfo.value)
    assert "configure_hook_handlers" in message
    assert "--legacy-harness" not in message


@pytest.mark.asyncio
async def test_a_worktree_is_removed_when_the_session_cannot_hold_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed holder write must trigger immediate startup cleanup."""
    _init_repo(tmp_path)
    worktrees = SessionWorktrees()
    attempted: list[Path] = []

    def refuse(
        cwd: Path,
        _session_id: str,
        _reservation: object = None,
        _session_dir: Path | None = None,
    ) -> None:
        attempted.append(cwd)
        raise OSError("holder write failed")

    monkeypatch.setattr(SessionWorktrees, "hold", staticmethod(refuse))

    async def never_moves(_options: SessionOptions) -> None:
        raise AssertionError("the session moved without holding its worktree")

    with pytest.raises(OSError):
        await worktrees.raise_behind(
            "session-1",
            None,
            never_moves,
            SessionOptions(
                cwd=str(tmp_path),
                worktree=NewWorktreeInput(branch="jun/unheld", name="unheld"),
            ),
            SessionOptions(cwd=str(tmp_path)),
        )

    assert attempted
    assert not attempted[0].exists()


@pytest.mark.asyncio
async def test_the_move_is_announced_once_the_worktree_exists(tmp_path: Path) -> None:
    """The settle precedes the move: a client waiting out the preparation
    proceeds the moment the worktree exists, while the context rebuild that
    retargets the tools still runs.
    """
    _init_repo(tmp_path)
    worktrees = SessionWorktrees()
    order: list[str] = []

    async def announce(
        options: SessionOptions, prepared: PreparedWorktree | None
    ) -> None:
        order.append("announced")
        assert prepared is not None
        assert prepared.root.is_dir()
        assert str(prepared.root) == options.cwd

    async def move(options: SessionOptions) -> None:
        order.append("moved")

    await worktrees.raise_behind(
        "session-1",
        None,
        move,
        SessionOptions(
            cwd=str(tmp_path),
            worktree=NewWorktreeInput(branch="jun/settle", name="settle"),
        ),
        SessionOptions(cwd=str(tmp_path)),
        announce=announce,
    )

    assert order == ["announced", "moved"]


@pytest.mark.asyncio
async def test_a_cancel_mid_move_keeps_the_announced_worktree(tmp_path: Path) -> None:
    """A teardown that lands mid-move is not a failed start: the settle was
    already handed to a client, and the session replacing this one (a resume
    rebinds, shutting the start backend down) may be standing in the worktree.
    The claim goes; the directory stays.
    """
    repo = _init_repo(tmp_path)
    worktrees = SessionWorktrees()
    roots: list[Path] = []
    in_move = asyncio.Event()

    async def announce(
        options: SessionOptions, prepared: PreparedWorktree | None
    ) -> None:
        assert prepared is not None
        roots.append(prepared.root)

    async def move(options: SessionOptions) -> None:
        in_move.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(
        worktrees.raise_behind(
            "session-1",
            None,
            move,
            SessionOptions(
                cwd=str(tmp_path),
                worktree=NewWorktreeInput(branch="jun/superseded", name="superseded"),
            ),
            SessionOptions(cwd=str(tmp_path)),
            announce=announce,
        )
    )
    await in_move.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert roots
    assert roots[0].is_dir()
    assert _linked(tmp_path)
    assert "jun/superseded" in [head.name for head in repo.heads]


@pytest.mark.asyncio
async def test_a_failure_mid_move_still_cleans_up_the_worktree(tmp_path: Path) -> None:
    """A move that fails on its own unwinds the start it created: the worktree
    and its branch go, so retention cannot pick up an unheld checkout.
    """
    repo = _init_repo(tmp_path)
    worktrees = SessionWorktrees()

    async def announce(
        options: SessionOptions, prepared: PreparedWorktree | None
    ) -> None:
        assert prepared is not None

    async def move(options: SessionOptions) -> None:
        raise RuntimeError("context rebuild failed")

    with pytest.raises(RuntimeError, match="context rebuild failed"):
        await worktrees.raise_behind(
            "session-1",
            None,
            move,
            SessionOptions(
                cwd=str(tmp_path),
                worktree=NewWorktreeInput(branch="jun/broken", name="broken"),
            ),
            SessionOptions(cwd=str(tmp_path)),
            announce=announce,
        )

    assert _linked(tmp_path) == ()
    assert "jun/broken" not in [head.name for head in repo.heads]


@pytest.mark.asyncio
async def test_a_failed_announce_never_blocks_the_move(tmp_path: Path) -> None:
    """The announce is best effort: a lost settle costs an early footer, not
    the session.
    """
    _init_repo(tmp_path)
    worktrees = SessionWorktrees()
    moved: list[str] = []

    async def announce(
        options: SessionOptions, prepared: PreparedWorktree | None
    ) -> None:
        raise RuntimeError("services gone")

    async def move(options: SessionOptions) -> None:
        moved.append("moved")

    await worktrees.raise_behind(
        "session-1",
        None,
        move,
        SessionOptions(
            cwd=str(tmp_path),
            worktree=NewWorktreeInput(branch="jun/quiet", name="quiet"),
        ),
        SessionOptions(cwd=str(tmp_path)),
        announce=announce,
    )

    assert moved == ["moved"]
