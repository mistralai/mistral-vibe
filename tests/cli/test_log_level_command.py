from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_app,
    build_test_vibe_config,
)
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


@pytest.fixture(autouse=True)
def _clear_session_override() -> Iterator[None]:
    set_session_override(None)
    yield
    set_session_override(None)


@pytest.mark.asyncio
async def test_bare_prints_full_chain() -> None:
    config = build_test_vibe_config()
    agent_loop = build_test_agent_loop(config=config)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        handled = await app._handle_command("/log-level")
        await pilot.pause()
        messages = app.query(UserCommandMessage)
        assert any(
            "Session: (none)" in m._content
            and "Env:" in m._content
            and "Config:" in m._content
            and "Effective:" in m._content
            for m in messages
        )

    assert handled is True


@pytest.mark.asyncio
async def test_set_applies_session_override() -> None:
    config = build_test_vibe_config()
    agent_loop = build_test_agent_loop(config=config)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        handled = await app._handle_command("/log-level set DEBUG")
        await pilot.pause()
        messages = app.query(UserCommandMessage)
        assert any("Session: DEBUG" in m._content for m in messages)

    assert handled is True
    assert get_session_override() == "DEBUG"


@pytest.mark.asyncio
async def test_set_normalizes_case() -> None:
    config = build_test_vibe_config()
    agent_loop = build_test_agent_loop(config=config)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await app._handle_command("/log-level set info")
        await pilot.pause()

    assert get_session_override() == "INFO"


@pytest.mark.asyncio
async def test_invalid_level_shows_error() -> None:
    config = build_test_vibe_config()
    agent_loop = build_test_agent_loop(config=config)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        handled = await app._handle_command("/log-level set VERBOSE")
        await pilot.pause()
        errors = app.query(ErrorMessage)
        assert any(
            "Invalid" in str(m._error) or "Usage" in str(m._error) for m in errors
        )

    assert handled is True
    assert get_session_override() is None


@pytest.mark.asyncio
async def test_unknown_verb_shows_usage() -> None:
    config = build_test_vibe_config()
    agent_loop = build_test_agent_loop(config=config)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await app._handle_command("/log-level DEBUG")
        await pilot.pause()
        errors = app.query(ErrorMessage)
        assert any("Usage" in str(m._error) for m in errors)


@pytest.mark.asyncio
async def test_set_global_persists_and_applies() -> None:
    config = build_test_vibe_config()
    agent_loop = build_test_agent_loop(config=config)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await app._handle_command("/log-level set-global DEBUG")
        await pilot.pause()

    assert get_session_override() == "DEBUG"
    assert app.app_server.resources.config.current.log_level == "DEBUG"


@pytest.mark.asyncio
async def test_unset_clears_session_override(tmp_path: Path) -> None:
    log_file = tmp_path / "vibe.log"
    init_file_logging(log_file, target_logger=vibe_logger)
    try:
        config = build_test_vibe_config()
        agent_loop = build_test_agent_loop(config=config)
        app = build_test_vibe_app(agent_loop=agent_loop)

        async with app.run_test() as pilot:
            await app._handle_command("/log-level set DEBUG")
            await pilot.pause()
            assert get_session_override() == "DEBUG"

            await app._handle_command("/log-level unset")
            await pilot.pause()
            assert get_session_override() is None
            assert get_effective_log_level() == "WARNING"
    finally:
        vibe_logger.handlers = [
            h
            for h in vibe_logger.handlers
            if not (isinstance(h, _VibeFileHandler) and h.baseFilename == str(log_file))
        ]


@pytest.mark.asyncio
async def test_unset_when_no_override_prints_notice() -> None:
    config = build_test_vibe_config()
    agent_loop = build_test_agent_loop(config=config)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await app._handle_command("/log-level unset")
        await pilot.pause()
        messages = app.query(UserCommandMessage)
        assert any("No session override was set" in m._content for m in messages)


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
