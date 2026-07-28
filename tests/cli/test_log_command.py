from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_app,
    build_test_vibe_config,
)
from vibe.cli.textual_ui.widgets.messages import UserCommandMessage
from vibe.core.config import SessionLoggingConfig


@pytest.mark.asyncio
async def test_log_command_shows_persisted_session_directory(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    agent_loop = build_test_agent_loop(config=config)
    await agent_loop.persist_empty_session()
    app = build_test_vibe_app(config=config, agent_loop=agent_loop)

    async with app.run_test() as pilot:
        handled = await app._handle_command("/log")
        await pilot.pause()

        messages = app.query(UserCommandMessage)
        log_messages = [
            message._content
            for message in messages
            if message._content.startswith("## Current Log Directory")
        ]

    assert handled is True
    assert len(log_messages) == 1
    assert str(tmp_path) in log_messages[0]
    assert "You can send this directory to share your interaction." in log_messages[0]
