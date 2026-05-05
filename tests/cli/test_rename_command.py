from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.textual_ui.widgets.messages import ErrorMessage, UserCommandMessage
from vibe.core.config import SessionLoggingConfig


def _enabled_session_config(save_dir: Path) -> SessionLoggingConfig:
    return SessionLoggingConfig(enabled=True, save_dir=str(save_dir))


@pytest.mark.asyncio
async def test_rename_command_updates_live_unsaved_session_title(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(session_logging=_enabled_session_config(tmp_path))
    app = build_test_vibe_app(config=config)

    async with app.run_test() as pilot:
        handled = await app._handle_command("/rename Manual title")
        await pilot.pause()
        messages = app.query(UserCommandMessage)
        assert any(
            message._content == 'Session renamed to "Manual title".'
            for message in messages
        )

    assert handled is True

    metadata = app.agent_loop.session_logger.session_metadata
    assert metadata is not None
    assert metadata.title == "Manual title"
    assert metadata.title_source == "manual"
    assert not app.agent_loop.session_logger.metadata_filepath.exists()


@pytest.mark.asyncio
async def test_rename_command_persists_existing_session_metadata(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(session_logging=_enabled_session_config(tmp_path))
    app = build_test_vibe_app(config=config)
    logger = app.agent_loop.session_logger
    assert logger.session_dir is not None
    assert logger.session_metadata is not None

    logger.session_dir.mkdir(parents=True)
    existing_metadata = {
        **logger.session_metadata.model_dump(),
        "end_time": "2024-01-01T12:05:00Z",
        "extra_field": "preserved",
    }
    logger.metadata_filepath.write_text(json.dumps(existing_metadata), encoding="utf-8")

    async with app.run_test() as pilot:
        handled = await app._handle_command("/rename Persisted title")
        await pilot.pause()

    assert handled is True

    metadata = logger.session_metadata
    assert metadata is not None
    assert metadata.title == "Persisted title"
    assert metadata.title_source == "manual"

    saved_metadata = json.loads(logger.metadata_filepath.read_text())
    assert saved_metadata["title"] == "Persisted title"
    assert saved_metadata["title_source"] == "manual"
    assert saved_metadata["end_time"] == "2024-01-01T12:05:00Z"
    assert saved_metadata["extra_field"] == "preserved"


@pytest.mark.asyncio
async def test_resume_picker_shows_renamed_session_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = build_test_vibe_config(
        session_logging=_enabled_session_config(tmp_path), vibe_code_enabled=False
    )
    app = build_test_vibe_app(config=config)
    logger = app.agent_loop.session_logger
    assert logger.session_dir is not None
    assert logger.session_metadata is not None

    logger.session_dir.mkdir(parents=True)
    existing_metadata = {
        **logger.session_metadata.model_dump(),
        "end_time": "2024-01-01T12:05:00Z",
        "total_messages": 1,
    }
    logger.metadata_filepath.write_text(json.dumps(existing_metadata), encoding="utf-8")
    logger.messages_filepath.write_text(
        '{"role": "user", "content": "Original prompt"}\n', encoding="utf-8"
    )

    captured_picker = None

    async def capture_picker(picker):
        nonlocal captured_picker
        captured_picker = picker

    monkeypatch.setattr(app, "_switch_from_input", capture_picker)

    async with app.run_test() as pilot:
        handled = await app._handle_command("/rename New title")
        await app._show_session_picker()
        await pilot.pause()

    assert handled is True
    assert captured_picker is not None
    assert captured_picker._latest_messages[f"local:{logger.session_id}"] == "New title"


@pytest.mark.asyncio
async def test_rename_command_requires_title(tmp_path: Path) -> None:
    config = build_test_vibe_config(session_logging=_enabled_session_config(tmp_path))
    app = build_test_vibe_app(config=config)

    async with app.run_test() as pilot:
        handled = await app._handle_command("/rename")
        await pilot.pause()
        errors = app.query(ErrorMessage)
        assert any(error._error == "Usage: /rename <title>" for error in errors)

    assert handled is True


@pytest.mark.asyncio
async def test_rename_command_is_intercepted_for_remote_sessions(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(session_logging=_enabled_session_config(tmp_path))
    app = build_test_vibe_app(config=config)

    async with app.run_test() as pilot:
        with patch(
            "vibe.cli.textual_ui.remote.remote_session_manager.RemoteEventsSource"
        ) as MockSource:
            remote_source = MagicMock()
            remote_source.session_id = "remote-session-id"
            remote_source.is_terminated = False
            remote_source.is_waiting_for_input = False
            MockSource.return_value = remote_source

            await app._remote_manager.attach(
                session_id="remote-session-id", config=config
            )
            handled = await app._handle_command("/rename Remote title")
        await pilot.pause()
        errors = app.query(ErrorMessage)
        assert any(
            error._error == "Renaming is only supported for local sessions."
            for error in errors
        )

    assert handled is True
