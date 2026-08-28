from __future__ import annotations

import json
from pathlib import Path

from vibe.core.session.session_index import SessionIndex


def _write_session(save_dir: Path, name: str, metadata: dict[str, object]) -> None:
    session_dir = save_dir / name
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "meta.json").write_text(json.dumps(metadata))
    (session_dir / "messages.jsonl").write_text(json.dumps({"role": "user"}) + "\n")


def test_the_listing_offers_a_moved_session_from_its_origin(tmp_path: Path) -> None:
    # The listing is a second matcher, separate from the loader. Filtering on
    # the current directory alone loses a moved session at the place the user
    # started it, which is the disappearance this pair of fields prevents.
    save_dir = tmp_path / "sessions"
    _write_session(
        save_dir,
        "test_20260101_000000_abc",
        {
            "session_id": "s1",
            "origin_directory": "/repo",
            "environment": {"working_directory": "/repo/../worktree"},
            "start_time": "2026-01-01T00:00:00+00:00",
        },
    )
    index = SessionIndex(save_dir, "test")

    assert [info["session_id"] for info in index.list("/repo")] == ["s1"]
    assert [info["session_id"] for info in index.list("/repo/../worktree")] == ["s1"]
    assert index.list("/elsewhere") == []


def test_the_listing_still_works_without_a_recorded_origin(tmp_path: Path) -> None:
    save_dir = tmp_path / "sessions"
    _write_session(
        save_dir,
        "test_20260101_000000_def",
        {
            "session_id": "s2",
            "environment": {"working_directory": "/repo"},
            "start_time": "2026-01-01T00:00:00+00:00",
        },
    )
    index = SessionIndex(save_dir, "test")

    assert [info["session_id"] for info in index.list("/repo")] == ["s2"]
    assert index.list("/elsewhere") == []
