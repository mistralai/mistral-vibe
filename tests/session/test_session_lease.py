from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

from vibe.core.session.session_lease import SessionLease


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_session_lease_files_are_owner_only(tmp_path: Path) -> None:
    """Acquiring a lease creates the lock and registry helper owner-only."""
    lease = SessionLease(tmp_path, "session-1").acquire()

    try:
        lock_mode = stat.S_IMODE(lease.path.stat().st_mode)
        assert lock_mode & 0o077 == 0
        registry_mode = stat.S_IMODE((tmp_path / "active" / ".registry").stat().st_mode)
        assert registry_mode & 0o077 == 0
    finally:
        lease.release()
