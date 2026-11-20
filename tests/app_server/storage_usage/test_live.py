from __future__ import annotations

from pathlib import Path
import threading

import pytest

from mistralai_vibe_local_harness.vibe._storage import SessionLease
from tests.app_server.storage_usage._tree import NOW_MS
from vibe.app_server._storage_usage import live
from vibe.app_server._storage_usage.live import (
    archive_suggestion,
    delete_bytes,
    delete_suggestion,
    low_disk_threshold,
    open_sessions,
)
from vibe.app_server.protocol import (
    StorageSessionUsage,
    StorageUsageSnapshot,
    StorageWorktreeUsage,
)

_GB = 10**9
_DAY_MS = 24 * 60 * 60 * 1000


@pytest.mark.parametrize(
    ("total", "threshold"),
    [(494 * _GB, 20 * _GB), (2000 * _GB, 20 * _GB), (128 * _GB, 12.8 * _GB)],
)
def test_the_low_disk_threshold_is_the_lower_of_10_percent_and_20_gb(
    total: int, threshold: float
) -> None:
    """*Prepare*: A 494 GB, a 2 TB and a 128 GB disk.
    *Do*: Compute the low-disk threshold.
    *Assert*: 20 GB, 20 GB and 12.8 GB.
    """
    # Do
    result = low_disk_threshold(total)

    # Assert
    assert result == int(threshold)


def _row(
    session_id: str, *, idle_days: float, archived: bool = False
) -> StorageSessionUsage:
    return StorageSessionUsage(
        session_id=session_id,
        archived=archived,
        last_activity_at=NOW_MS - int(idle_days * _DAY_MS),
    )


def _worktree(name: str, size: int | None, *sessions: str) -> StorageWorktreeUsage:
    return StorageWorktreeUsage(
        id=f"b/{name}", path=f"/w/{name}", bytes=size, session_ids=list(sessions)
    )


def test_the_suggestion_lists_idle_sessions_and_what_archiving_them_frees() -> None:
    """*Prepare*: Idle, recent, open, archived and metadata-less sessions, with a
    worktree shared with a recent session, one shared with an archived peer,
    and one not measured yet.
    *Do*: Compute the archive suggestion.
    *Assert*: Only idle, closed, titled sessions; a worktree counts only when
    every session using it is suggested or archived, never when not measured.
    """
    # Prepare
    snapshot = StorageUsageSnapshot(
        scan_started_at=NOW_MS,
        measured_at=NOW_MS,
        sessions=[
            _row("idle", idle_days=40),
            _row("idle-peer", idle_days=31),
            _row("recent", idle_days=2),
            _row("open", idle_days=60),
            _row("gone", idle_days=90, archived=True),
            _row("untitled", idle_days=90),
            _row("unmeasured", idle_days=45),
        ],
        worktrees=[
            _worktree("with-recent", 1000, "idle", "recent"),
            _worktree("with-archived", 2000, "idle-peer", "gone"),
            _worktree("unknown", None, "unmeasured"),
        ],
    )

    # Do
    suggestion = archive_suggestion(snapshot, {"open"}, {"untitled"}, NOW_MS)

    # Assert
    assert suggestion.session_ids == ["idle", "idle-peer", "unmeasured"]
    assert suggestion.reclaimable_bytes == 2000
    assert suggestion.inactive_days == 30


def test_the_delete_suggestion_lists_old_archived_sessions_and_what_goes() -> None:
    """*Prepare*: Old and recent archived sessions, an open one, an active one,
    a worktree only old archived sessions use, and one an active session shares.
    *Do*: Compute the delete suggestion.
    *Assert*: Only old, closed, archived sessions; their histories count, and
    a worktree only when no session left would still use it.
    """

    # Prepare
    def row(session_id: str, idle_days: float, archived: bool) -> StorageSessionUsage:
        return _row(session_id, idle_days=idle_days, archived=archived).model_copy(
            update={"history_bytes": 100}
        )

    snapshot = StorageUsageSnapshot(
        scan_started_at=NOW_MS,
        measured_at=NOW_MS,
        sessions=[
            row("old", 40, True),
            row("old-peer", 35, True),
            row("old-shared", 50, True),
            row("recent", 3, True),
            row("open", 60, True),
            row("active", 90, False),
        ],
        worktrees=[
            _worktree("only-old", 1000, "old", "old-peer"),
            _worktree("with-active", 2000, "old-shared", "active"),
        ],
    )

    # Do
    suggestion = delete_suggestion(snapshot, {"open"}, set(), NOW_MS)

    # Assert
    assert suggestion.session_ids == ["old", "old-peer", "old-shared"]
    assert suggestion.reclaimable_bytes == 3 * 100 + 1000


def test_a_session_delete_frees_its_history_and_only_its_own_worktrees() -> None:
    """*Prepare*: A session with a worktree only it claims and one it shares.
    *Do*: Compute what deleting it alone frees.
    *Assert*: Its history and its own worktree, never the shared one.
    """
    # Prepare
    snapshot = StorageUsageSnapshot(
        scan_started_at=NOW_MS,
        measured_at=NOW_MS,
        sessions=[
            _row("a", idle_days=40).model_copy(update={"history_bytes": 100}),
            _row("b", idle_days=40),
        ],
        worktrees=[
            _worktree("own", 1000, "a"),
            _worktree("shared", 2000, "a", "b"),
            _worktree("unmeasured", None, "a"),
        ],
    )

    # Do
    freed = delete_bytes(snapshot, "a")

    # Assert
    assert freed == (100 + 1000, True)
    assert delete_bytes(snapshot, "gone") == (0, False)


def test_an_unmeasured_worktree_the_action_frees_makes_the_number_a_lower_bound() -> (
    None
):
    """*Prepare*: Old archived sessions, one alone in an unmeasured worktree,
    one sharing an unmeasured worktree with an active session; idle active
    sessions likewise.
    *Do*: Compute the delete and archive suggestions and each delete size.
    *Assert*: Flagged as a lower bound only when an unmeasured worktree would
    be freed; an unmeasured worktree that stays never flags it.
    """
    # Prepare
    snapshot = StorageUsageSnapshot(
        scan_started_at=NOW_MS,
        measured_at=NOW_MS,
        sessions=[
            _row("old-alone", idle_days=40, archived=True),
            _row("old-shared", idle_days=40, archived=True),
            _row("idle-alone", idle_days=40),
            _row("idle-shared", idle_days=40),
            _row("recent", idle_days=1),
        ],
        worktrees=[
            _worktree("alone", None, "old-alone"),
            _worktree("shared", None, "old-shared", "recent"),
            _worktree("idle-alone", None, "idle-alone"),
            _worktree("idle-shared", None, "idle-shared", "recent"),
        ],
    )
    only_shared = snapshot.model_copy(
        update={"worktrees": [snapshot.worktrees[1], snapshot.worktrees[3]]}
    )

    # Do
    deleting = delete_suggestion(snapshot, set(), set(), NOW_MS)
    archiving = archive_suggestion(snapshot, set(), set(), NOW_MS)

    # Assert
    assert deleting.reclaimable_at_least
    assert archiving.reclaimable_at_least
    assert delete_bytes(snapshot, "old-alone") == (0, True)
    assert delete_bytes(snapshot, "old-shared") == (0, False)
    assert not delete_suggestion(only_shared, set(), set(), NOW_MS).reclaimable_at_least
    assert not archive_suggestion(
        only_shared, set(), set(), NOW_MS
    ).reclaimable_at_least


def test_a_probe_in_a_loop_never_makes_a_lease_acquire_fail(tmp_path: Path) -> None:
    """*Prepare*: A session store and a thread probing open sessions in a loop.
    *Do*: Acquire and release the session's lease many times meanwhile.
    *Assert*: Every acquire succeeds, and the probe saw it open at least once.
    """
    # Prepare
    stop = threading.Event()
    seen_open: list[bool] = []

    def probe() -> None:
        while not stop.is_set():
            seen_open.append("s1" in open_sessions([tmp_path]))

    prober = threading.Thread(target=probe)
    prober.start()
    failures: list[BaseException] = []

    # Do
    try:
        for _ in range(300):
            lease = SessionLease(tmp_path, "s1")
            try:
                lease.acquire()
            except BaseException as exc:
                failures.append(exc)
                continue
            lease.release()
    finally:
        stop.set()
        prober.join()

    # Assert
    assert failures == []
    assert any(seen_open)


def test_an_unheld_lease_file_is_not_open_and_is_kept(tmp_path: Path) -> None:
    """*Prepare*: A lease file a crashed process left behind.
    *Do*: Probe open sessions.
    *Assert*: Not open, and the file is still there (the probe never sweeps).
    """
    # Prepare
    leases = tmp_path / "active"
    leases.mkdir()
    (leases / "crashed.lock").write_bytes(b"\0")

    # Do
    found = open_sessions([tmp_path])

    # Assert
    assert found == set()
    assert (leases / "crashed.lock").exists()


@pytest.mark.parametrize("error", [BlockingIOError, PermissionError])
def test_a_store_whose_lease_directory_cannot_be_locked_counts_every_lease_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: type[OSError]
) -> None:
    """*Prepare*: A store with two unheld lease files and another file, whose
    lease directory lock raises (Windows ``LK_LOCK`` timing out, or access
    denied).
    *Do*: Probe open sessions.
    *Assert*: Both sessions read open: never suggested for archiving.
    """
    # Prepare
    leases = tmp_path / "active"
    leases.mkdir()
    for name in ("a.lock", "b.lock", "a.lock.json"):
        (leases / name).write_bytes(b"\0")

    def refuse(_file: object, *, blocking: bool = False) -> None:
        raise error

    monkeypatch.setattr(live, "acquire_file_lock", refuse)

    # Do
    found = open_sessions([tmp_path])

    # Assert
    assert found == {"a", "b"}


def test_a_store_without_a_lease_directory_has_no_open_session(tmp_path: Path) -> None:
    """*Prepare*: A store where no session ever took a lease.
    *Do*: Probe open sessions.
    *Assert*: None, and no lease directory was created.
    """
    # Do
    found = open_sessions([tmp_path])

    # Assert
    assert found == set()
    assert not (tmp_path / "active").exists()
