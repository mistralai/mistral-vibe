from __future__ import annotations

from pathlib import Path
from typing import Any

from git import Repo
import pytest

from vibe.app_server._storage_usage.deletion import remove_orphaned_worktrees
from vibe.app_server._storage_usage.rules import frees_worktree
from vibe.app_server.protocol import StorageUsageSnapshot, StorageWorktreeUsage
from vibe.core.git.worktree import (
    SNAPSHOT_REF_PREFIX,
    ManagedWorktree,
    WorktreeRelease,
    WorktreeRepository,
)
from vibe.core.git.worktree.record import WorktreeClaim, WorktreeRecordError
from vibe.core.paths import WORKTREES_DIR


def _init_repo(root: Path) -> Repo:
    repo = Repo.init(root, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (root / "file.txt").write_text("hello\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    return repo


def _worktree(repo_root: Path, sessions: Path, *names: str) -> ManagedWorktree:
    with WorktreeRepository.open(repo_root) as repository:
        worktree = repository.prepare_auto(suggested_name=names[0])
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    for name in names:
        (sessions / name).mkdir(parents=True, exist_ok=True)
        managed.hold(name, session_dir=sessions / name)
        managed.release_holder(name)
    return managed


def test_a_deleted_sessions_worktree_goes_unless_another_session_claims_it(
    tmp_path: Path, config_dir: Path
) -> None:
    """*Prepare*: One worktree only the deleted session claimed, with an
    uncommitted file, and one it shares with a session still on disk.
    *Do*: Remove the worktrees the delete left orphaned.
    *Assert*: The first goes with its work saved to a ref and its measured
    size freed; the shared one stays.
    """
    # Prepare
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repo = _init_repo(repo_root)
    sessions = tmp_path / "sessions"
    own = _worktree(repo_root, sessions, "gone")
    shared = _worktree(repo_root, sessions, "gone-too", "alive")
    (own.root / "notes.txt").write_text("unsaved\\n")
    for name in ("gone", "gone-too"):
        (sessions / name).rmdir()
    own_id = f"{own.claim.bucket}/{own.claim.name}"
    snapshot = StorageUsageSnapshot(
        scan_started_at=0,
        measured_at=0,
        worktrees=[StorageWorktreeUsage(id=own_id, path=str(own.root), bytes=4_000)],
    )

    # Do
    freed = remove_orphaned_worktrees(
        ["gone", "gone-too"], WORKTREES_DIR.path, snapshot, None
    )

    # Assert
    assert freed.removed_worktree_ids == [own_id]
    assert freed.worktree_bytes == 4_000
    assert not own.root.exists()
    assert shared.root.is_dir()
    assert "notes.txt" in repo.git.show(
        "--stat", f"{SNAPSHOT_REF_PREFIX}/{own.claim.name}"
    )


def _snapshot_of(*worktrees: tuple[ManagedWorktree, list[str]]) -> StorageUsageSnapshot:
    return StorageUsageSnapshot(
        scan_started_at=0,
        measured_at=0,
        worktrees=[
            StorageWorktreeUsage(
                id=f"{managed.claim.bucket}/{managed.claim.name}",
                path=str(managed.root),
                bytes=1_000,
                session_ids=sessions,
            )
            for managed, sessions in worktrees
        ],
    )


def test_a_worktree_breaking_mid_removal_is_kept_as_an_error_and_the_rest_goes(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: Two worktrees each claimed only by a deleted session; the
    first one's release raises ``WorktreeRecordError``, as a corrupt claim does.
    *Do*: Remove the worktrees the delete left orphaned.
    *Assert*: The first is kept with ``error``; the second is removed.
    """
    # Prepare
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)
    sessions = tmp_path / "sessions"
    broken = _worktree(repo_root, sessions, "broken")
    fine = _worktree(repo_root, sessions, "fine")
    for name in ("broken", "fine"):
        (sessions / name).rmdir()
    real_release = ManagedWorktree.release

    def release(self: ManagedWorktree, *args: Any, **kwargs: Any) -> WorktreeRelease:
        if self.claim == broken.claim:
            raise WorktreeRecordError("Unusable worktree holder id '.removing'.")
        return real_release(self, *args, **kwargs)

    monkeypatch.setattr(ManagedWorktree, "release", release)
    broken_id = f"{broken.claim.bucket}/{broken.claim.name}"
    fine_id = f"{fine.claim.bucket}/{fine.claim.name}"

    # Do
    freed = remove_orphaned_worktrees(
        ["broken", "fine"], WORKTREES_DIR.path, None, None
    )

    # Assert
    assert freed.removed_worktree_ids == [fine_id]
    assert [(item.id, item.reason) for item in freed.kept_worktrees] == [
        (broken_id, "error")
    ]
    assert broken.root.is_dir()
    assert not fine.root.exists()


def test_a_failing_trash_empty_still_reports_the_worktrees_already_removed(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A worktree claimed only by a deleted session; emptying the
    worktree trash raises.
    *Do*: Remove the worktrees the delete left orphaned.
    *Assert*: The worktree is reported removed: it is already gone.
    """
    # Prepare
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)
    sessions = tmp_path / "sessions"
    worktree = _worktree(repo_root, sessions, "gone")
    (sessions / "gone").rmdir()

    def empty_trash() -> None:
        raise OSError("trash is busy")

    monkeypatch.setattr(ManagedWorktree, "empty_trash", staticmethod(empty_trash))

    # Do
    freed = remove_orphaned_worktrees(["gone"], WORKTREES_DIR.path, None, None)

    # Assert
    assert freed.removed_worktree_ids == [
        f"{worktree.claim.bucket}/{worktree.claim.name}"
    ]
    assert not worktree.root.exists()


def test_an_unreadable_claim_of_a_deleted_session_is_reported_kept(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A worktree the last measurement lists for a deleted session,
    whose claim can no longer be read.
    *Do*: Remove the worktrees the delete left orphaned.
    *Assert*: The worktree is reported kept with ``error``, never silently
    left out of the answer.
    """
    # Prepare
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)
    sessions = tmp_path / "sessions"
    broken = _worktree(repo_root, sessions, "gone")
    (sessions / "gone").rmdir()
    snapshot = _snapshot_of((broken, ["gone"]))
    real_read = WorktreeClaim.read

    def read(self: WorktreeClaim) -> Any:
        if self == broken.claim:
            raise WorktreeRecordError("Unreadable claim record.")
        return real_read(self)

    monkeypatch.setattr(WorktreeClaim, "read", read)

    # Do
    freed = remove_orphaned_worktrees(["gone"], WORKTREES_DIR.path, snapshot, None)

    # Assert
    assert freed.removed_worktree_ids == []
    assert [(item.id, item.reason) for item in freed.kept_worktrees] == [
        (f"{broken.claim.bucket}/{broken.claim.name}", "error")
    ]


def test_an_unmeasured_removed_worktree_makes_the_freed_size_a_lower_bound(
    tmp_path: Path, config_dir: Path
) -> None:
    """*Prepare*: A worktree claimed only by a deleted session, not measured
    yet by the last scan.
    *Do*: Remove the worktrees the delete left orphaned.
    *Assert*: It is removed, and the freed size says it is a lower bound.
    """
    # Prepare
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)
    sessions = tmp_path / "sessions"
    worktree = _worktree(repo_root, sessions, "gone")
    (sessions / "gone").rmdir()
    snapshot = _snapshot_of((worktree, ["gone"]))
    snapshot.worktrees[0].bytes = None

    # Do
    freed = remove_orphaned_worktrees(["gone"], WORKTREES_DIR.path, snapshot, None)

    # Assert
    assert freed.removed_worktree_ids == [
        f"{worktree.claim.bucket}/{worktree.claim.name}"
    ]
    assert freed.worktree_bytes == 0
    assert freed.at_least is True


def test_removal_and_estimate_agree_on_a_worktree_shared_by_two_deleted_sessions(
    tmp_path: Path, config_dir: Path
) -> None:
    """*Prepare*: A worktree two sessions claim, both being deleted together,
    and one only a deleted session and a third, kept session claim.
    *Do*: Estimate the delete of both from the scan, then delete them and
    remove the worktrees left orphaned.
    *Assert*: The estimate counts exactly the worktree the removal removes.
    """
    # Prepare
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)
    sessions = tmp_path / "sessions"
    both = _worktree(repo_root, sessions, "a", "b")
    with_kept = _worktree(repo_root, sessions, "a-too", "kept")
    snapshot = _snapshot_of((both, ["a", "b"]), (with_kept, ["a-too", "kept"]))
    deleting = {"a", "b", "a-too"}
    estimated = [
        row.id
        for row in snapshot.worktrees
        if frees_worktree(row.session_ids, deleting)
    ]
    for name in deleting:
        (sessions / name).rmdir()

    # Do
    freed = remove_orphaned_worktrees(
        sorted(deleting), WORKTREES_DIR.path, snapshot, None
    )

    # Assert
    assert freed.removed_worktree_ids == estimated
    assert estimated == [f"{both.claim.bucket}/{both.claim.name}"]
    assert with_kept.root.is_dir()
