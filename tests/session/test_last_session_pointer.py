from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

from vibe.core.config import SessionLoggingConfig
from vibe.core.session import last_session_pointer


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_recorded_pointer_is_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recording a pointer creates the directory and file owner-only."""
    config = SessionLoggingConfig(
        save_dir=str(tmp_path), session_prefix="session", enabled=True
    )
    monkeypatch.setattr(last_session_pointer, "current_tty_key", lambda: "ttys000")

    last_session_pointer.record(config, "session-1")

    pointer_dir = tmp_path / ".last_session"
    assert stat.S_IMODE(pointer_dir.stat().st_mode) & 0o077 == 0
    pointer = pointer_dir / "ttys000"
    assert pointer.read_text(encoding="utf-8").strip() == "session-1"
    assert stat.S_IMODE(pointer.stat().st_mode) & 0o077 == 0
