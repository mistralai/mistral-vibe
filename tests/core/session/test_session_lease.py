from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import vibe.core.session.session_lease as lease_module
from vibe.core.session.session_lease import SessionBusyError, SessionLease

SESSION_ID = "019ffb1e-741d-7f90-84df-ef66011876ca"


def test_session_lease_is_exclusive_and_recoverable(tmp_path: Path) -> None:
    first = SessionLease(tmp_path, SESSION_ID).acquire()
    try:
        with pytest.raises(SessionBusyError):
            SessionLease(tmp_path, SESSION_ID).acquire()
    finally:
        first.release()

    assert not first.path.exists()
    SessionLease(tmp_path, SESSION_ID).acquire().release()


def test_session_lease_rejects_a_path_shaped_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="invalid session ID"):
        SessionLease(tmp_path, "../escape")


def test_session_lease_accepts_a_safe_legacy_identity(tmp_path: Path) -> None:
    lease = SessionLease(tmp_path, "resumable-with-stats").acquire()

    assert lease.path == tmp_path / "active" / "resumable-with-stats.lock"
    lease.release()


@pytest.mark.parametrize(("blocking", "expected_mode"), [(False, 2), (True, 1)])
def test_windows_locking_uses_a_one_byte_region(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, blocking: bool, expected_mode: int
) -> None:
    calls: list[tuple[int, int]] = []
    fake_msvcrt = SimpleNamespace(
        LK_LOCK=1,
        LK_NBLCK=2,
        LK_UNLCK=0,
        locking=lambda _descriptor, mode, length: calls.append((mode, length)),
    )
    monkeypatch.setattr(lease_module, "_is_windows", lambda: True)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    path = tmp_path / "lease.lock"

    with path.open("w+b") as file:
        lease_module._acquire_file_lock(file, blocking=blocking)
        lease_module._release_file_lock(file)

    assert calls == [(expected_mode, 1), (fake_msvcrt.LK_UNLCK, 1)]


def test_session_lease_diagnostics_stay_readable_while_held(tmp_path: Path) -> None:
    """The diagnostic document is readable while the lease is held, on every platform."""
    lease = SessionLease(tmp_path, SESSION_ID).acquire()

    try:
        diagnostic = json.loads(lease.diagnostic_path.read_text())
    finally:
        lease.release()

    assert diagnostic["session_id"] == SESSION_ID
    assert diagnostic["lease_version"] == 1
    assert not lease.diagnostic_path.exists()


def test_session_lease_acquire_releases_the_lock_when_the_diagnostic_write_fails(
    tmp_path: Path,
) -> None:
    """A failed diagnostic write leaves no held lock: the retry succeeds."""
    lease = SessionLease(tmp_path, SESSION_ID)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    lease._diagnostic_path = blocker / "diagnostic.json"

    with pytest.raises(OSError):
        lease.acquire()

    SessionLease(tmp_path, SESSION_ID).acquire().release()


def test_session_lease_rejects_a_symlinked_active_namespace(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "active").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link"):
        SessionLease(tmp_path, SESSION_ID).acquire()
