from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from vibe.core.session.session_loader import (
    MESSAGES_FILENAME,
    METADATA_FILENAME,
    SessionLoader,
)


def _session(
    root: Path, *, origin: Path | None = None, legacy: Path | None = None
) -> Path:
    session_dir = root / "session"
    session_dir.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, Any] = {
        "session_id": "s1",
        "environment": {"working_directory": str(legacy)} if legacy else {},
        "total_messages": 1,
    }
    if origin is not None:
        metadata["origin_directory"] = str(origin)
    (session_dir / METADATA_FILENAME).write_text(json.dumps(metadata))
    (session_dir / MESSAGES_FILENAME).write_text(json.dumps({"role": "user"}) + "\n")
    return session_dir


def _found_from(session_dir: Path, working_directory: Path) -> bool:
    return (
        SessionLoader._read_validated_session(session_dir, working_directory)
        is not None
    )


def test_a_moved_session_is_found_from_where_it_began(tmp_path: Path) -> None:
    # The failure this exists to prevent. One field doing both jobs meant a
    # session that moved into a worktree disappeared from the repository the
    # user started it in.
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    session_dir = _session(tmp_path, origin=repo, legacy=worktree)

    assert _found_from(session_dir, repo)


def test_a_moved_session_is_found_from_where_it_now_sits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    session_dir = _session(tmp_path, origin=repo, legacy=worktree)

    assert _found_from(session_dir, worktree)


def test_an_unrelated_directory_still_does_not_match(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    session_dir = _session(tmp_path, origin=repo, legacy=worktree)

    assert not _found_from(session_dir, tmp_path / "elsewhere")


def test_sessions_written_before_the_split_still_match(tmp_path: Path) -> None:
    # Nothing rewrites old metadata, so the environment entry has to keep
    # working on its own.
    repo = tmp_path / "repo"
    session_dir = _session(tmp_path, legacy=repo)

    assert _found_from(session_dir, repo)
    assert not _found_from(session_dir, tmp_path / "elsewhere")
