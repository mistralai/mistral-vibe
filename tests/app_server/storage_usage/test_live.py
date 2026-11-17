from __future__ import annotations

from pathlib import Path
import threading

import pytest

from mistralai_vibe_local_harness.vibe._storage import SessionLease
from tests.app_server.storage_usage._tree import NOW_MS
from vibe.app_server._storage_usage import live
from vibe.app_server._storage_usage.live import (
    archive_suggestion,
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
