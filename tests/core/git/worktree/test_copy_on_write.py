from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import subprocess
import sys
import threading

from git import Repo
import pytest

from vibe.core.git.errors import GitError
from vibe.core.git.worktree import (
    ManagedWorktree,
    PreparedWorktree,
    WorktreeReleaseOutcome,
    WorktreeRepository,
)
import vibe.core.git.worktree.copy_on_write as copy_on_write
from vibe.core.git.worktree.copy_on_write import (
    CopyOnWriteOutcome,
    _refreshes,
    evict_unused,
    refresh,
    templates_root,
)
from vibe.core.git.worktree.record import WorktreeClaim
from vibe.core.trusted_folders import trusted_folders_manager

# The suite mocks sys.platform to linux; host_platform restores the real one.
macos = pytest.mark.skipif(sys.platform != "darwin", reason="APFS clonefile")
pytestmark = pytest.mark.usefixtures("host_platform")


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Path]:
    """A trusted clone whose origin/HEAD names main, as `git clone` leaves it."""
    origin = tmp_path / "origin"
    with Repo.init(origin, initial_branch="main") as source:
        source.config_writer().set_value("user", "name", "T").release()
        source.config_writer().set_value("user", "email", "t@example.com").release()
        (origin / "src").mkdir()
        for index in range(50):
            (origin / "src" / f"f{index}.txt").write_text(f"file {index}\n")
        (origin / ".gitignore").write_text("node_modules/\n")
        source.index.add([".gitignore", *(f"src/f{i}.txt" for i in range(50))])
        source.index.commit("initial")
    root = tmp_path / "repo"
    Repo.clone_from(str(origin), str(root)).close()
    trusted_folders_manager.trust_for_session(root)
    yield root


def _templates() -> list[str]:
    """Template checkouts and ready markers; locks stay by design."""
    root = templates_root()
    return (
        []
        if not root.exists()
        else [
            p.name
            for p in root.iterdir()
            if p.name[0] != "." and not p.name.endswith(".lock")
        ]
    )


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _create(repo: Path, name: str) -> PreparedWorktree:
    with WorktreeRepository.open(repo, copy_on_write=True) as repository:
        prepared = repository.prepare(name)
    for thread in list(_refreshes.values()):
        thread.join()
    return prepared


@macos
def test_the_second_worktree_is_cloned_from_the_template_built_after_the_first(
    repo: Path,
) -> None:
    first = _create(repo, "first")
    second = _create(repo, "second")

    assert first.copy_on_write is CopyOnWriteOutcome.TEMPLATE_PENDING
    assert second.copy_on_write is CopyOnWriteOutcome.CLONED
    assert _git(second.root, "status", "--porcelain", "--untracked-files=all") == ""
    assert (second.root / "src" / "f7.txt").read_text() == "file 7\n"
    # The template's ignored files are removed before anything clones them.
    assert not (second.root / "node_modules").exists()


@macos
def test_a_cloned_worktree_sees_a_same_size_edit(repo: Path) -> None:
    _create(repo, "first")
    cloned = _create(repo, "second")
    _git(cloned.root, "status")

    (cloned.root / "src" / "f1.txt").write_text("file 2\n")

    assert _git(cloned.root, "status", "--porcelain") == "M src/f1.txt"


@macos
def test_the_stat_setting_stays_in_the_cloned_worktree(repo: Path) -> None:
    _create(repo, "first")
    cloned = _create(repo, "second")

    assert _git(cloned.root, "config", "--get", "core.checkStat") == "minimal"
    assert _git(repo, "config", "--get", "extensions.worktreeConfig") == "true"
    assert (
        subprocess.run(
            ["git", "-C", str(repo), "config", "--get", "core.checkStat"],
            capture_output=True,
        ).returncode
        == 1
    )


@macos
def test_a_worktree_on_another_commit_gets_that_commit(repo: Path) -> None:
    _create(repo, "first")
    (repo / "src" / "f3.txt").write_text("changed on main\n")
    _git(repo, "-c", "user.name=T", "-c", "user.email=t@e", "commit", "-qam", "next")
    head = _git(repo, "rev-parse", "HEAD")

    with WorktreeRepository.open(repo, copy_on_write=True) as repository:
        forked = repository.prepare_fork(suggested_name="fork")

    assert forked.copy_on_write is CopyOnWriteOutcome.CLONED
    assert _git(forked.root, "rev-parse", "HEAD") == head
    assert (forked.root / "src" / "f3.txt").read_text() == "changed on main\n"
    assert _git(forked.root, "status", "--porcelain", "--untracked-files=all") == ""


@macos
def test_an_untrusted_repository_is_not_cloned_or_changed(tmp_path: Path) -> None:
    root = tmp_path / "plain"
    with Repo.init(root, initial_branch="main") as plain:
        (root / "a.txt").write_text("a\n")
        plain.index.add(["a.txt"])
        plain.index.commit("initial")

    created = _create(root, "wt")

    assert created.copy_on_write is CopyOnWriteOutcome.UNSUPPORTED
    assert not _templates()
    assert (
        subprocess.run(
            ["git", "-C", str(root), "config", "--get", "extensions.worktreeConfig"],
            capture_output=True,
        ).returncode
        == 1
    )


@macos
def test_a_repository_with_core_worktree_set_keeps_its_config(repo: Path) -> None:
    _git(repo, "config", "core.worktree", str(repo))

    created = _create(repo, "wt")

    assert created.copy_on_write is CopyOnWriteOutcome.UNSUPPORTED
    assert "extensions.worktreeconfig" not in _git(repo, "config", "--list", "--local")
    assert not _templates()
    assert _git(created.root, "status", "--porcelain") == ""


def test_the_template_is_neither_a_session_checkout_nor_managed(repo: Path) -> None:
    with WorktreeRepository.open(repo) as repository:
        refresh(repo, repository.bucket)
        template = templates_root() / repository.bucket
        assert (template / ".git").is_file()
        assert all(root != template for root, _ in repository.checkouts())
        assert all(worktree.root != template for worktree in repository.linked())
    assert ManagedWorktree.at(template) is None


def test_the_flag_off_creates_as_before(repo: Path) -> None:
    with WorktreeRepository.open(repo) as repository:
        created = repository.prepare("plain")

    assert created.copy_on_write is None
    assert not _templates()


@macos
def test_a_volume_that_cannot_clone_leaves_the_repository_unchanged(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_clones(source: Path, destination: Path) -> None:
        raise OSError(45, "Operation not supported", str(source))

    monkeypatch.setattr(copy_on_write, "_clone", no_clones)

    created = _create(repo, "wt")

    assert created.copy_on_write is CopyOnWriteOutcome.UNSUPPORTED
    assert "extensions.worktreeconfig" not in _git(repo, "config", "--list", "--local")
    assert not _templates()


@macos
def test_the_template_goes_with_the_last_worktree_of_its_repository(repo: Path) -> None:
    first = _create(repo, "first")
    second = _create(repo, "second")
    template = templates_root() / first.root.parent.name
    assert (template / ".git").is_file()

    for created in (first, second):
        managed = ManagedWorktree.at(created.root)
        assert managed is not None
        # A session would have taken and released the worktree by now.
        managed.claim.finish_starting()
        release = managed.release()
        assert release.outcome is WorktreeReleaseOutcome.REMOVED
        ManagedWorktree.empty_trash()
        assert template.exists() is (created is first)

    assert not _templates()
    assert "templates" not in _git(repo, "worktree", "list")


@macos
def test_concurrent_creations_all_see_that_the_volume_clones() -> None:
    results: list[bool] = []
    start = threading.Barrier(16)

    def probe() -> None:
        start.wait()
        results.append(copy_on_write._clones_supported())

    threads = [threading.Thread(target=probe) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [True] * 16
    assert not any(templates_root().iterdir())


@macos
def test_a_template_evicted_by_a_process_that_died_is_deleted_by_the_next_sweep(
    repo: Path,
) -> None:
    _create(repo, "first")
    _create(repo, "second")

    # Eviction moved the template aside, then the process died before the sweep.
    evict_unused(set())
    assert not _templates()
    assert any(WorktreeClaim.trash_root().iterdir())

    ManagedWorktree.empty_trash()

    assert not any(WorktreeClaim.trash_root().iterdir())


@macos
def test_an_error_while_checking_eligibility_falls_back_to_a_normal_checkout(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_config(_git: object) -> bool:
        raise GitError("could not lock config file")

    monkeypatch.setattr(copy_on_write, "_enable_worktree_config", failing_config)

    created = _create(repo, "wt")

    assert created.copy_on_write is CopyOnWriteOutcome.UNSUPPORTED
    assert _git(created.root, "status", "--porcelain") == ""
    assert (created.root / "src" / "f1.txt").read_text() == "file 1\n"


@macos
def test_eviction_keeps_the_lock_a_waiting_refresh_holds(repo: Path) -> None:
    """A refresh blocked on the lock rebuilds the template after eviction
    returns. Eviction finds templates through their lock files, so deleting the
    lock would hide that rebuilt template from every later sweep.
    """
    created = _create(repo, "first")
    lock = templates_root() / f"{created.root.parent.name}.lock"
    assert lock.is_file()

    evict_unused(set())

    assert lock.is_file()
    assert not _templates()
