from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from git import Repo
import pytest

from vibe.core.git.errors import GitError
import vibe.core.git.repo as repo_module
from vibe.core.git.repo import BranchChanges, GitRepo, GitStatus
from vibe.core.git.worktree import WorktreeRepository


def _status(cwd: Path) -> GitStatus | None:
    """None where the old soft opener returned it: no repository, or no git.

    `GitRepo.open` raises instead, so the tests that pin "renders nothing
    rather than an error" now pin which error, and the caller decides.
    """
    try:
        repo = GitRepo.open(cwd)
    except GitError:
        return None
    try:
        return repo.status()
    except GitError:
        return None
    finally:
        repo.close()


def _init_repo(root: Path, *, initial_branch: str = "main") -> Repo:
    repo = Repo.init(root, initial_branch=initial_branch)
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (root / "file.txt").write_text("hello\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    return repo


def _commit(repo: Repo, path: Path, body: str, message: str) -> None:
    path.write_text(body)
    repo.index.add([str(path.relative_to(repo.working_dir))])
    repo.index.commit(message)


def test_reads_main_checkout(tmp_path: Path) -> None:
    """The main checkout is the case `list_linked_worktrees` cannot report."""
    _init_repo(tmp_path)

    status = _status(tmp_path)

    assert status is not None
    assert status.worktree_name == tmp_path.name
    assert status.root == tmp_path.resolve()
    assert status.branch == "main"


def test_reads_linked_worktree(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    with WorktreeRepository.open(tmp_path) as repository:
        worktree = repository.prepare("feature-worktree", branch="feat/feature")

    status = _status(worktree.path)

    assert status is not None
    assert status.worktree_name == "feature-worktree"
    assert status.branch == "feat/feature"
    assert status.base_branch == "main"


def test_resolves_from_a_subdirectory(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    nested = tmp_path / "packages" / "app"
    nested.mkdir(parents=True)

    status = _status(nested)

    assert status is not None
    assert status.root == tmp_path.resolve()
    assert status.branch == "main"


def test_returns_none_outside_a_repository(tmp_path: Path) -> None:
    assert _status(tmp_path) is None


def test_returns_none_for_a_missing_path(tmp_path: Path) -> None:
    assert _status(tmp_path / "nope") is None


def test_refuses_a_bare_repository_without_stranding_the_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A bare repository has no working tree, so there is no checkout to
    # describe. Refused rather than reported empty: the caller still has to
    # close what it opened, and this runs on every session listing, so an
    # answer that hid the open would leak a set of handles per refresh.
    bare = tmp_path / "bare.git"
    Repo.init(bare, bare=True)
    closed: list[bool] = []
    real = repo_module._git_python()

    class _Watched(Repo):
        def close(self) -> None:
            closed.append(True)
            super().close()

    monkeypatch.setattr(
        repo_module, "_git_python", lambda: replace(real, repo=_Watched)
    )

    assert _status(bare) is None
    assert closed == [True]


def test_reports_no_branch_when_head_is_detached(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    repo.git.checkout(repo.head.commit.hexsha)

    status = _status(tmp_path)

    assert status is not None
    assert status.branch is None
    assert status.worktree_name == tmp_path.name


def test_prefers_origin_head_over_a_conventional_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    repo.git.update_ref("refs/remotes/origin/trunk", repo.head.commit.hexsha)
    repo.git.symbolic_ref("refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")

    status = _status(tmp_path)

    assert status is not None
    assert status.base_branch == "trunk"


def test_falls_back_to_a_configured_default_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path, initial_branch="trunk")
    repo.config_writer().set_value("init", "defaultBranch", "trunk").release()

    status = _status(tmp_path)

    assert status is not None
    assert status.base_branch == "trunk"


def test_reports_no_base_branch_without_a_known_default(tmp_path: Path) -> None:
    _init_repo(tmp_path, initial_branch="feat/only")

    status = _status(tmp_path)

    assert status is not None
    assert status.branch == "feat/only"
    assert status.base_branch is None


# Normalised rather than echoed back, so an ssh remote and an https one for the
# same repository match each other and the project's own list.
def test_normalises_an_ssh_remote_to_an_https_url(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    repo.create_remote("origin", "git@github.com:mistralai/dashboard.git")

    status = _status(tmp_path)

    assert status is not None
    assert status.repo_url == "https://github.com/mistralai/dashboard.git"


def test_reports_no_repo_url_without_a_remote(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    status = _status(tmp_path)

    assert status is not None
    assert status.repo_url is None


# Where the repository is hosted is not this probe's business: it reports what
# the checkout points at, and only teleport needs that to be GitHub.
def test_reports_a_remote_that_is_not_github(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    repo.create_remote("origin", "git@gitlab.com:owner/repo.git")

    status = _status(tmp_path)

    assert status is not None
    assert status.repo_url == "https://gitlab.com/owner/repo.git"


def test_measures_a_branch_it_is_not_standing_on(tmp_path: Path) -> None:
    # What a picker needs: one checkout reporting on every worktree of the
    # repository. Opening one per worktree would allocate a repository each,
    # and each holds its cat-file children until it is closed.
    repo = _init_repo(tmp_path)
    repo.create_head("feature").checkout()
    _commit(repo, tmp_path / "file.txt", "hello\nand more\n", "branch work")
    repo.heads.main.checkout()

    checkout = GitRepo.open(tmp_path)

    assert checkout is not None
    with checkout:
        assert checkout.branch() == "main"
        assert checkout.changes_on("feature") == BranchChanges(additions=1, deletions=0)
        # Standing on the base measures nothing, which is not the same as
        # having no base to measure against.
        assert checkout.changes_on("main") == BranchChanges(additions=0, deletions=0)


def test_a_branch_that_is_gone_measures_as_nothing_to_compare(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    checkout = GitRepo.open(tmp_path)

    assert checkout is not None
    with checkout:
        assert checkout.changes_on("no-such-branch") is None
