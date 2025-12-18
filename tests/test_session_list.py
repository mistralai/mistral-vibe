from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

from vibe.core.config import SessionLoggingConfig
from vibe.core.interaction_logger import InteractionLogger, SessionListEntry


def create_session_file(
    save_dir: Path,
    session_id: str,
    start_time: datetime,
    working_directory: str,
    message_count: int,
    git_branch: str | None = None,
) -> Path:
    """Helper to create a test session file."""
    timestamp = start_time.strftime("%Y%m%d_%H%M%S")
    filename = f"session_{timestamp}_{session_id[:8]}.json"
    filepath = save_dir / filename

    data = {
        "metadata": {
            "session_id": session_id,
            "start_time": start_time.isoformat(),
            "end_time": start_time.isoformat(),
            "git_branch": git_branch,
            "environment": {"working_directory": working_directory},
            "total_messages": message_count,
        },
        "messages": [],
    }

    filepath.write_text(json.dumps(data), encoding="utf-8")
    return filepath


def test_list_sessions_returns_empty_when_no_sessions(config_dir: Path) -> None:
    session_dir = config_dir / "logs" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    config = SessionLoggingConfig(save_dir=str(session_dir))
    result = InteractionLogger.list_sessions(config)

    assert result == []


def test_list_sessions_returns_empty_when_dir_not_exists(config_dir: Path) -> None:
    config = SessionLoggingConfig(save_dir=str(config_dir / "nonexistent"))
    result = InteractionLogger.list_sessions(config)

    assert result == []


def test_list_sessions_returns_sessions_sorted_by_time(config_dir: Path) -> None:
    session_dir = config_dir / "logs" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    # Create sessions with different times
    older_time = datetime(2024, 12, 15, 10, 0, 0)
    newer_time = datetime(2024, 12, 17, 14, 30, 0)

    older_file = create_session_file(
        session_dir, "older123-uuid-here", older_time, "/home/user/project1", 10, "main"
    )
    newer_file = create_session_file(
        session_dir,
        "newer456-uuid-here",
        newer_time,
        "/home/user/project2",
        25,
        "feature",
    )

    # Set file modification times to match creation order
    import os
    import time

    os.utime(older_file, (time.time() - 1000, time.time() - 1000))
    os.utime(newer_file, (time.time(), time.time()))

    config = SessionLoggingConfig(save_dir=str(session_dir))
    result = InteractionLogger.list_sessions(config)

    assert len(result) == 2
    # Most recent first
    assert result[0].session_id == "newer456"
    assert result[1].session_id == "older123"


def test_list_sessions_returns_correct_metadata(config_dir: Path) -> None:
    session_dir = config_dir / "logs" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    test_time = datetime(2024, 12, 17, 14, 30, 0)
    create_session_file(
        session_dir,
        "abc12345-full-uuid",
        test_time,
        "/home/user/myproject",
        42,
        "develop",
    )

    config = SessionLoggingConfig(save_dir=str(session_dir))
    result = InteractionLogger.list_sessions(config)

    assert len(result) == 1
    entry = result[0]
    assert isinstance(entry, SessionListEntry)
    assert entry.session_id == "abc12345"
    assert entry.start_time == test_time
    assert entry.working_directory == "/home/user/myproject"
    assert entry.message_count == 42
    assert entry.git_branch == "develop"


def test_list_sessions_respects_limit(config_dir: Path) -> None:
    session_dir = config_dir / "logs" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    # Create 5 sessions
    for i in range(5):
        test_time = datetime(2024, 12, 17, 10 + i, 0, 0)
        create_session_file(
            session_dir, f"session{i:02d}-uuid", test_time, f"/path/{i}", i * 10
        )

    config = SessionLoggingConfig(save_dir=str(session_dir))
    result = InteractionLogger.list_sessions(config, limit=3)

    assert len(result) == 3


def test_list_sessions_skips_corrupted_files(config_dir: Path) -> None:
    session_dir = config_dir / "logs" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    # Create a valid session
    test_time = datetime(2024, 12, 17, 14, 0, 0)
    create_session_file(
        session_dir, "valid123-uuid", test_time, "/home/user/project", 5
    )

    # Create a corrupted session file
    corrupted = session_dir / "session_20241217_120000_corrupt1.json"
    corrupted.write_text("not valid json {{{", encoding="utf-8")

    config = SessionLoggingConfig(save_dir=str(session_dir))
    result = InteractionLogger.list_sessions(config)

    # Should only return the valid session
    assert len(result) == 1
    assert result[0].session_id == "valid123"


def test_list_sessions_handles_missing_optional_fields(config_dir: Path) -> None:
    session_dir = config_dir / "logs" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    # Create a session with minimal metadata
    filepath = session_dir / "session_20241217_140000_minimal1.json"
    data = {
        "metadata": {
            "session_id": "minimal1-uuid",
            "start_time": "2024-12-17T14:00:00",
            "total_messages": 3,
            # No git_branch, no environment
        },
        "messages": [],
    }
    filepath.write_text(json.dumps(data), encoding="utf-8")

    config = SessionLoggingConfig(save_dir=str(session_dir))
    result = InteractionLogger.list_sessions(config)

    assert len(result) == 1
    assert result[0].session_id == "minimal1"
    assert result[0].git_branch is None
    assert result[0].working_directory is None


def test_list_sessions_uses_custom_prefix(config_dir: Path) -> None:
    session_dir = config_dir / "logs" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    # Create session with default prefix
    test_time = datetime(2024, 12, 17, 14, 0, 0)
    create_session_file(session_dir, "default1-uuid", test_time, "/path", 5)

    # Create session with custom prefix
    custom_file = session_dir / "custom_20241217_150000_custom12.json"
    custom_data = {
        "metadata": {
            "session_id": "custom12-uuid",
            "start_time": "2024-12-17T15:00:00",
            "total_messages": 10,
        },
        "messages": [],
    }
    custom_file.write_text(json.dumps(custom_data), encoding="utf-8")

    # Search with custom prefix should only find custom files
    config = SessionLoggingConfig(save_dir=str(session_dir), session_prefix="custom")
    result = InteractionLogger.list_sessions(config)

    assert len(result) == 1
    assert result[0].session_id == "custom12"
