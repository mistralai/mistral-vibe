from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.memory.config import MemoryConfig


@pytest.fixture
def vibe_config() -> VibeConfig:
    return VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False),
        memory=MemoryConfig(enabled=True),
    )


@pytest.fixture
def vibe_app(vibe_config: VibeConfig) -> VibeApp:
    agent_loop = AgentLoop(vibe_config)
    return VibeApp(agent_loop=agent_loop)


@pytest.mark.asyncio
async def test_ctrl_d_flushes_memory_before_exit(vibe_app: VibeApp) -> None:
    mm = vibe_app.agent_loop.memory_manager
    mm.on_session_end = AsyncMock()
    mm.aclose = AsyncMock()

    async with vibe_app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause(0.1)

    mm.on_session_end.assert_awaited_once()
    mm.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_ctrl_c_with_empty_input_flushes_memory_before_exit(
    vibe_app: VibeApp,
) -> None:
    mm = vibe_app.agent_loop.memory_manager
    mm.on_session_end = AsyncMock()
    mm.aclose = AsyncMock()

    async with vibe_app.run_test() as pilot:
        await pilot.press("ctrl+c")
        await pilot.pause(0.1)

    mm.on_session_end.assert_awaited_once()
    mm.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_ctrl_c_with_nonempty_input_clears_input_without_quit(
    vibe_app: VibeApp,
) -> None:
    mm = vibe_app.agent_loop.memory_manager
    mm.on_session_end = AsyncMock()
    mm.aclose = AsyncMock()

    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "do not quit yet"
        await pilot.pause(0.05)
        await pilot.press("ctrl+c")
        await pilot.pause(0.05)
        assert chat_input.value == ""

    mm.on_session_end.assert_not_awaited()
    mm.aclose.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_closes_memory_even_when_toggled_off(vibe_app: VibeApp) -> None:
    mm = vibe_app.agent_loop.memory_manager
    mm.enabled = False
    mm.on_session_end = AsyncMock()
    mm.aclose = AsyncMock()

    async with vibe_app.run_test() as pilot:
        await pilot.press("ctrl+d")
        await pilot.pause(0.1)

    mm.on_session_end.assert_not_awaited()
    mm.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_toggle_memory_blocked_when_disabled_in_config() -> None:
    app = VibeApp(
        agent_loop=AgentLoop(
            VibeConfig(
                session_logging=SessionLoggingConfig(enabled=False),
                memory=MemoryConfig(enabled=False),
            )
        )
    )
    app._mount_and_scroll = AsyncMock()

    await app.action_toggle_memory()

    assert app.agent_loop.memory_manager.enabled is False
    app._mount_and_scroll.assert_awaited()
