from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import json
from pathlib import Path
from threading import Event

from git import Repo
import pytest

from vibe.app_server._worktree_cleanup import WorktreeCleanup, session_finished
from vibe.app_server._worktree_session import SessionWorktrees
from vibe.core.git.worktree import (
    SNAPSHOT_REF_PREFIX,
    ManagedWorktree,
    PreparedWorktree,
    WorktreeRepository,
)
from vibe.core.git.worktree.record import WorktreeClaim
from vibe.core.trusted_folders import TrustedFoldersManager


def _init_repo(root: Path) -> Repo:
    repo = Repo.init(root, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Tester").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    (root / "file.txt").write_text("hello\n")
    repo.index.add(["file.txt"])
    repo.index.commit("initial")
    return repo


def _managed_worktree(
    root: Path, name: str
) -> tuple[PreparedWorktree, ManagedWorktree]:
    """A worktree one session worked in, then closed."""
    with WorktreeRepository.open(root) as repository:
        worktree = repository.prepare_auto(suggested_name=name)
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    managed.hold(name, session_dir=root / "sessions" / name)
    managed.release_holder(name)
    return worktree, managed


@contextmanager
def _paused_snapshot(monkeypatch: pytest.MonkeyPatch) -> Iterator[Event]:
    snapshot = PreparedWorktree.snapshot
    started = Event()
    release = Event()

    def paused(prepared: PreparedWorktree) -> str:
        started.set()
        release.wait(timeout=30)
        return snapshot(prepared)

    monkeypatch.setattr(PreparedWorktree, "snapshot", paused)
    try:
        yield started
    finally:
        release.set()


type SessionFinished = Callable[[Path], bool]


def _archived(_session_dir: Path) -> bool:
    return True


@pytest.fixture
def cleanup() -> Iterator[WorktreeCleanup]:
    service = WorktreeCleanup(session_finished=_archived, retry_delays=(0.01,))
    yield service
    assert service.wait_idle(timeout=30)


def test_an_archived_sessions_worktree_is_removed_in_the_background(
    tmp_path: Path, cleanup: WorktreeCleanup
) -> None:
    """*Prepare*: A worktree whose only session is archived, which nobody holds.
    *Do*: Schedule its cleanup.
    *Assert*: It is snapshotted and removed without the caller doing it.
    """
    # Prepare
    repo = _init_repo(tmp_path)
    worktree, _ = _managed_worktree(tmp_path, "archived")

    # Do
    cleanup.schedule(worktree.root)

    # Assert
    assert cleanup.wait_idle(timeout=30)
    assert not worktree.root.exists()
    assert repo.commit(f"{SNAPSHOT_REF_PREFIX}/{worktree.name}")


def test_scheduling_does_not_wait_for_the_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup: WorktreeCleanup
) -> None:
    _init_repo(tmp_path)
    worktree, _ = _managed_worktree(tmp_path, "slow")

    with _paused_snapshot(monkeypatch) as snapshot_started:
        cleanup.schedule(worktree.root)

        assert snapshot_started.wait(timeout=10)
        assert worktree.root.is_dir()


def test_a_held_worktree_is_removed_once_its_last_session_releases_it(
    tmp_path: Path, cleanup: WorktreeCleanup
) -> None:
    """*Prepare*: An archived session's worktree another session still holds.
    *Do*: Schedule cleanup, then release the final holder and schedule again.
    *Assert*: It survives while held and goes after the release.
    """
    # Prepare
    _init_repo(tmp_path)
    worktree, managed = _managed_worktree(tmp_path, "shared")
    managed.hold("session-b")

    # Do
    cleanup.schedule(worktree.root)
    assert cleanup.wait_idle(timeout=30)
    still_there = worktree.root.is_dir()
    managed.release_holder("session-b")
    cleanup.schedule(worktree.root)

    # Assert
    assert cleanup.wait_idle(timeout=30)
    assert still_there
    assert not worktree.root.exists()


def test_a_worktree_whose_session_is_not_archived_is_kept(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    worktree, _ = _managed_worktree(tmp_path, "active")
    service = WorktreeCleanup(session_finished=lambda _dir: False)

    service.schedule(worktree.root)

    assert service.wait_idle(timeout=30)
    assert worktree.root.is_dir()


def test_a_startup_sweep_removes_what_an_earlier_process_left(
    tmp_path: Path, cleanup: WorktreeCleanup
) -> None:
    """*Prepare*: An archived session's worktree and a moved-aside worktree
    that the process removing them died before deleting.
    *Do*: Sweep at startup.
    *Assert*: Both are gone.
    """
    # Prepare
    _init_repo(tmp_path)
    worktree, _ = _managed_worktree(tmp_path, "left-behind")
    leftover = WorktreeClaim.trash_root() / "interrupted"
    (leftover / "nested").mkdir(parents=True)
    (leftover / "nested" / "file.txt").write_text("partly deleted\n")

    # Do
    cleanup.sweep()

    # Assert
    assert cleanup.wait_idle(timeout=30)
    assert not worktree.root.exists()
    assert not leftover.exists()


def test_a_startup_sweep_honours_a_removal_an_older_version_requested(
    tmp_path: Path,
) -> None:
    """An upgrade must not strand a removal the older Desktop had pending."""
    _init_repo(tmp_path)
    with WorktreeRepository.open(tmp_path) as repository:
        worktree = repository.prepare_auto(suggested_name="before-upgrade")
    managed = ManagedWorktree.at(worktree.root)
    assert managed is not None
    managed.hold("older-session")
    managed.release_holder("older-session")
    record = managed.claim.read()
    assert record is not None
    managed.claim.write(record.model_copy(update={"reap_requested": True}))
    service = WorktreeCleanup(session_finished=lambda _dir: False)

    service.sweep()

    assert service.wait_idle(timeout=30)
    assert not worktree.root.exists()


def test_a_failed_reap_does_not_stop_later_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup: WorktreeCleanup
) -> None:
    # Prepare
    _init_repo(tmp_path)
    failing, failing_managed = _managed_worktree(tmp_path, "failing")
    other, _ = _managed_worktree(tmp_path, "other")
    reap_if_finished = ManagedWorktree.reap_if_finished

    def fail_first(
        managed: ManagedWorktree,
        finished: SessionFinished,
        *,
        trust_store: TrustedFoldersManager | None = None,
    ) -> object:
        if managed.claim == failing_managed.claim:
            raise OSError("disk unavailable")
        return reap_if_finished(managed, finished, trust_store=trust_store)

    monkeypatch.setattr(ManagedWorktree, "reap_if_finished", fail_first)

    # Do
    cleanup.schedule(failing.root)
    cleanup.schedule(other.root)

    # Assert
    assert cleanup.wait_idle(timeout=30)
    assert failing.root.is_dir()
    assert not other.root.exists()


def test_a_failed_sweep_does_not_stop_later_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup: WorktreeCleanup
) -> None:
    """*Prepare*: A startup sweep that fails while listing worktrees.
    *Do*: Sweep, then schedule an archived session's worktree.
    *Assert*: The worker survives the failure and removes the worktree.
    """
    # Prepare
    _init_repo(tmp_path)
    worktree, _ = _managed_worktree(tmp_path, "after-failed-sweep")

    def unreadable() -> tuple[ManagedWorktree, ...]:
        raise OSError("claims unreadable")

    monkeypatch.setattr(ManagedWorktree, "joined", unreadable)

    # Do
    cleanup.sweep()
    assert cleanup.wait_idle(timeout=30)
    cleanup.schedule(worktree.root)

    # Assert
    assert cleanup.wait_idle(timeout=30)
    assert not worktree.root.exists()


def test_scheduling_an_unmanaged_directory_does_nothing(
    tmp_path: Path, cleanup: WorktreeCleanup
) -> None:
    cleanup.schedule(tmp_path)

    assert cleanup.wait_idle(timeout=5)
    assert tmp_path.is_dir()


def test_releasing_the_final_session_hold_removes_an_archived_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: An archived session still holds its worktree.
    *Do*: Release the session's hold the way every app-server close does.
    *Assert*: The process cleanup removes the worktree.
    """
    # Prepare
    _init_repo(tmp_path)
    worktree, managed = _managed_worktree(tmp_path, "closing")
    service = WorktreeCleanup(session_finished=_archived)
    monkeypatch.setattr(
        "vibe.app_server._worktree_session.worktree_cleanup", lambda: service
    )
    managed.hold("session-a")

    # Do
    SessionWorktrees.release(worktree.root, "session-a")

    # Assert
    assert service.wait_idle(timeout=30)
    assert not worktree.root.exists()


def test_a_reap_that_fails_once_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup: WorktreeCleanup
) -> None:
    # Prepare
    _init_repo(tmp_path)
    worktree, _ = _managed_worktree(tmp_path, "flaky")
    reap_if_finished = ManagedWorktree.reap_if_finished
    attempts: list[int] = []

    def fail_once(
        target: ManagedWorktree,
        finished: SessionFinished,
        *,
        trust_store: TrustedFoldersManager | None = None,
    ) -> object:
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("index.lock exists")
        return reap_if_finished(target, finished, trust_store=trust_store)

    monkeypatch.setattr(ManagedWorktree, "reap_if_finished", fail_once)

    # Do
    cleanup.schedule(worktree.root)

    # Assert
    assert cleanup.wait_idle(timeout=30)
    assert len(attempts) == 2
    assert not worktree.root.exists()


def test_retries_stop_after_the_last_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cleanup: WorktreeCleanup
) -> None:
    """A reap that keeps failing is left to the next release or startup."""
    _init_repo(tmp_path)
    worktree, _ = _managed_worktree(tmp_path, "broken")
    attempts: list[int] = []

    def always_fail(
        _target: ManagedWorktree,
        _finished: SessionFinished,
        *,
        trust_store: TrustedFoldersManager | None = None,
    ) -> object:
        attempts.append(1)
        raise OSError("disk unavailable")

    monkeypatch.setattr(ManagedWorktree, "reap_if_finished", always_fail)

    cleanup.schedule(worktree.root)

    assert cleanup.wait_idle(timeout=30)
    assert len(attempts) == 2
    assert worktree.root.is_dir()


@pytest.mark.parametrize(
    ("archived_at", "expected"), [("2026-10-12T10:00:00+00:00", True), (None, False)]
)
def test_session_finished_reads_the_session_metadata(
    tmp_path: Path, archived_at: str | None, expected: bool
) -> None:
    session_dir = tmp_path / "session-a"
    session_dir.mkdir()
    (session_dir / "meta.json").write_text(
        json.dumps({
            "session_id": "session-a",
            "start_time": "2026-10-12T09:00:00+00:00",
            "end_time": None,
            "git_commit": None,
            "git_branch": None,
            "environment": {},
            "username": "tester",
            "archived_at": archived_at,
        })
    )

    assert session_finished(session_dir) is expected


def test_a_deleted_session_is_finished(tmp_path: Path) -> None:
    assert session_finished(tmp_path / "deleted-session") is True


def test_a_session_with_unreadable_metadata_keeps_its_worktree(tmp_path: Path) -> None:
    session_dir = tmp_path / "session-a"
    session_dir.mkdir()
    (session_dir / "meta.json").write_text("{not json")

    assert session_finished(session_dir) is False


@pytest.mark.parametrize("trust", ["untrusted", "session", "persisted"])
def test_background_worker_uses_injected_persisted_source_policy(
    tmp_path: Path, trust: str
) -> None:
    from vibe.core.trusted_folders import trusted_folders_manager

    root = tmp_path / "source"
    repo = _init_repo(root)
    marker = tmp_path / "clean-ran"
    repo.git.config("filter.cleanup.clean", f"echo clean >> '{marker}'; sed 's/^S://'")
    repo.git.config("filter.cleanup.smudge", "sed 's/^/S:/'")
    (root / ".gitattributes").write_text("*.txt filter=cleanup\n")
    repo.index.add([".gitattributes"])
    repo.index.commit("filter")
    store = trusted_folders_manager.for_session()
    if trust == "persisted":
        store.add_trusted(root)
    elif trust == "session":
        store.trust_for_session(root)
    # The global singleton's temporary grant must never authorize this worker.
    trusted_folders_manager.trust_for_session(root)
    worktree, managed = _managed_worktree(root, "background-filter")
    (worktree.root / "file.txt").write_text("S:changed\n")
    marker.unlink(missing_ok=True)
    service = WorktreeCleanup(
        session_finished=_archived, retry_delays=(), trust_store=store
    )
    service.schedule(worktree.root)
    assert service.wait_idle(timeout=30)
    if trust == "persisted":
        recovery = managed.claim.read_recovery()
        assert recovery is not None
        assert marker.exists()
        assert repo.git.show(f"{recovery.snapshot_ref}:file.txt") == "changed"
    else:
        assert worktree.root.exists()
        assert (worktree.root / "file.txt").read_text() == "S:changed\n"
        assert managed.claim.read_recovery() is None
        assert not marker.exists()
    repo.close()
