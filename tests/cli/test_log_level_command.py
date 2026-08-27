from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_app,
    build_test_vibe_config,
)
from vibe.cli.textual_ui.widgets.log_level_picker import LogLevelPickerApp
from vibe.cli.textual_ui.widgets.messages import ErrorMessage, UserCommandMessage
from vibe.observability.logging import (
    _VibeFileHandler,
    get_effective_log_level,
    get_log_level_chain,
    get_session_override,
    init_file_logging,
    logger as vibe_logger,
    set_session_override,
)


async def _wait_until(pilot, predicate: Callable[[], bool], *, tries: int = 50) -> bool:
    for _ in range(tries):
        await pilot.pause()
        if predicate():
            return True
    return predicate()


def _has_command_message(app, *needles: str) -> bool:
    return any(
        all(needle in message._content for needle in needles)
        for message in app.query(UserCommandMessage)
    )


@pytest.fixture(autouse=True)
def _clear_session_override(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    set_session_override(None)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("DEBUG_MODE", raising=False)
    yield
    set_session_override(None)


@pytest.mark.asyncio
async def test_bare_opens_picker_panel() -> None:
    config = build_test_vibe_config()
    agent_loop = build_test_agent_loop(config=config)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        handled = await app._handle_command("/log-level")
        await pilot.pause()
        assert app.query(LogLevelPickerApp)

    assert handled is True


@pytest.mark.asyncio
async def test_picker_session_only_shows_feedback() -> None:
    config = build_test_vibe_config()
    agent_loop = build_test_agent_loop(config=config)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await app._handle_command("/log-level")
        await pilot.pause()
        picker = app.query_one(LogLevelPickerApp)
        picker.post_message(
            LogLevelPickerApp.Applied(
                session_level="DEBUG", config_level=None, config_cleared=False
            )
        )
        assert await _wait_until(
            pilot, lambda: _has_command_message(app, "DEBUG", "session")
        )

    assert get_session_override() == "DEBUG"


@pytest.mark.asyncio
async def test_picker_global_shows_feedback_and_persists() -> None:
    config = build_test_vibe_config()
    agent_loop = build_test_agent_loop(config=config)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await app._handle_command("/log-level")
        await pilot.pause()
        picker = app.query_one(LogLevelPickerApp)
        picker.post_message(
            LogLevelPickerApp.Applied(
                session_level=None, config_level="ERROR", config_cleared=False
            )
        )
        assert await _wait_until(
            pilot, lambda: _has_command_message(app, "ERROR", "config.toml")
        )

    assert app.app_server.resources.config.current.log_level == "ERROR"


@pytest.mark.asyncio
async def test_config_change_applies_when_no_session_override(tmp_path: Path) -> None:
    log_file = tmp_path / "vibe.log"
    init_file_logging(log_file, target_logger=vibe_logger)
    try:
        config = build_test_vibe_config()
        agent_loop = build_test_agent_loop(config=config)
        app = build_test_vibe_app(agent_loop=agent_loop)

        async with app.run_test() as pilot:
            await app.app_server.resources.config.update({"log_level": "ERROR"})
            await pilot.pause()
            assert get_session_override() is None
            assert get_effective_log_level() == "ERROR"
    finally:
        vibe_logger.handlers = [
            h
            for h in vibe_logger.handlers
            if not (isinstance(h, _VibeFileHandler) and h.baseFilename == str(log_file))
        ]


@pytest.mark.asyncio
async def test_config_level_applied_at_mount(tmp_path: Path) -> None:
    log_file = tmp_path / "vibe.log"
    init_file_logging(log_file, target_logger=vibe_logger)
    try:
        config = build_test_vibe_config(log_level="ERROR")
        agent_loop = build_test_agent_loop(config=config)
        app = build_test_vibe_app(agent_loop=agent_loop)

        async with app.run_test() as pilot:
            await pilot.pause()
            chain = get_log_level_chain()
            assert chain.config == "ERROR"
            assert chain.session is None
            assert get_effective_log_level() == "ERROR"
    finally:
        vibe_logger.handlers = [
            h
            for h in vibe_logger.handlers
            if not (isinstance(h, _VibeFileHandler) and h.baseFilename == str(log_file))
        ]


@pytest.mark.asyncio
async def test_picker_config_cleared_removes_persisted_value() -> None:
    config = build_test_vibe_config()
    agent_loop = build_test_agent_loop(config=config)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await app.app_server.resources.config.update({"log_level": "ERROR"})
        await pilot.pause()
        assert app.app_server.resources.config.current.log_level == "ERROR"

        await app._handle_command("/log-level")
        await pilot.pause()
        picker = app.query_one(LogLevelPickerApp)
        picker.post_message(
            LogLevelPickerApp.Applied(
                session_level=None, config_level=None, config_cleared=True
            )
        )
        assert await _wait_until(
            pilot, lambda: _has_command_message(app, "config.toml cleared")
        )

    assert app.app_server.resources.config.current.log_level is None


@pytest.mark.asyncio
async def test_picker_session_cleared_shows_feedback() -> None:
    config = build_test_vibe_config()
    agent_loop = build_test_agent_loop(config=config)
    app = build_test_vibe_app(agent_loop=agent_loop)

    set_session_override("DEBUG")
    async with app.run_test() as pilot:
        await app._handle_command("/log-level")
        await pilot.pause()
        picker = app.query_one(LogLevelPickerApp)
        picker.post_message(
            LogLevelPickerApp.Applied(
                session_level=None, config_level=None, config_cleared=False
            )
        )
        assert await _wait_until(
            pilot, lambda: _has_command_message(app, "session override cleared")
        )

    assert get_session_override() is None


@pytest.mark.asyncio
async def test_picker_config_write_failure_surfaces_error(monkeypatch) -> None:
    # When the inline (idle) config write fails, the picker must surface an
    # error instead of reporting success.
    config = build_test_vibe_config()
    agent_loop = build_test_agent_loop(config=config)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async def _boom(*_args, **_kwargs) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(app, "_persist_config_changes", _boom)

    async with app.run_test() as pilot:
        await app._handle_command("/log-level")
        await pilot.pause()
        picker = app.query_one(LogLevelPickerApp)
        picker.post_message(
            LogLevelPickerApp.Applied(
                session_level=None, config_level="ERROR", config_cleared=False
            )
        )
        await pilot.pause()

        errors = app.query(ErrorMessage)
        assert any(
            "log-level" in str(m._error) or "disk on fire" in str(m._error)
            for m in errors
        )
        success = app.query(UserCommandMessage)
        assert not any("config.toml" in m._content for m in success)
