from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import pytest

from vibe.cli.session_selector import SessionSelectorApp
from vibe.cli.session_selector.screen import SessionSelectorScreen
from vibe.core.config import SessionLoggingConfig


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


@pytest.mark.asyncio
async def test_session_selector_exits_with_none_when_no_sessions(
    config_dir: Path,
) -> None:
    session_dir = config_dir / "logs" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    config = SessionLoggingConfig(save_dir=str(session_dir))
    app = SessionSelectorApp(config)

    async with app.run_test() as pilot:
        # App should exit immediately with None when no sessions
        await pilot.pause(0.1)

    assert app.return_value is None


@pytest.mark.asyncio
async def test_session_selector_shows_sessions(config_dir: Path) -> None:
    session_dir = config_dir / "logs" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    # Create test sessions
    create_session_file(
        session_dir,
        "abc12345-uuid",
        datetime(2024, 12, 17, 14, 30, 0),
        "/home/user/project",
        10,
        "main",
    )

    config = SessionLoggingConfig(save_dir=str(session_dir))
    app = SessionSelectorApp(config)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        # Check the screen is correct
        assert isinstance(app.screen, SessionSelectorScreen)

        # Check sessions were loaded
        screen = app.screen
        assert len(screen._sessions) == 1
        assert screen._sessions[0].session_id == "abc12345"


@pytest.mark.asyncio
async def test_session_selector_navigation(config_dir: Path) -> None:
    session_dir = config_dir / "logs" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    # Create multiple sessions
    for i in range(3):
        create_session_file(
            session_dir,
            f"session{i:02d}-uuid",
            datetime(2024, 12, 17, 10 + i, 0, 0),
            f"/path/{i}",
            i * 10,
        )

    config = SessionLoggingConfig(save_dir=str(session_dir))
    app = SessionSelectorApp(config)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        screen = app.screen
        assert isinstance(screen, SessionSelectorScreen)

        # Initial selection is 0
        assert screen._selected_index == 0

        # Navigate down
        await pilot.press("down")
        assert screen._selected_index == 1

        await pilot.press("down")
        assert screen._selected_index == 2

        # Wrap around
        await pilot.press("down")
        assert screen._selected_index == 0

        # Navigate up (wrap)
        await pilot.press("up")
        assert screen._selected_index == 2


@pytest.mark.asyncio
async def test_session_selector_select_with_enter(config_dir: Path) -> None:
    session_dir = config_dir / "logs" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    filepath = create_session_file(
        session_dir,
        "selected1-uuid",
        datetime(2024, 12, 17, 14, 0, 0),
        "/home/user/project",
        5,
    )

    config = SessionLoggingConfig(save_dir=str(session_dir))
    app = SessionSelectorApp(config)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.1)

    assert app.return_value == filepath


@pytest.mark.asyncio
async def test_session_selector_cancel_with_escape(config_dir: Path) -> None:
    session_dir = config_dir / "logs" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    create_session_file(
        session_dir,
        "session01-uuid",
        datetime(2024, 12, 17, 14, 0, 0),
        "/home/user/project",
        5,
    )

    config = SessionLoggingConfig(save_dir=str(session_dir))
    app = SessionSelectorApp(config)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await pilot.press("escape")
        await pilot.pause(0.1)

    assert app.return_value is None


@pytest.mark.asyncio
async def test_session_selector_select_second_session(config_dir: Path) -> None:
    import os
    import time

    session_dir = config_dir / "logs" / "session"
    session_dir.mkdir(parents=True, exist_ok=True)

    # Create two sessions with controlled mtime order
    # First file is older (will be second in list, sorted by most recent first)
    first_file = create_session_file(
        session_dir, "first000-uuid", datetime(2024, 12, 17, 14, 0, 0), "/path/first", 5
    )
    # Second file is newer (will be first in list)
    create_session_file(
        session_dir,
        "second00-uuid",
        datetime(2024, 12, 17, 15, 0, 0),
        "/path/second",
        10,
    )

    # Set mtime so first_file is older
    os.utime(first_file, (time.time() - 1000, time.time() - 1000))

    config = SessionLoggingConfig(save_dir=str(session_dir))
    app = SessionSelectorApp(config)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        # Navigate to second session (first_file, the older one)
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.1)

    # After pressing down, we select the second item (first_file, the older one)
    assert app.return_value == first_file
