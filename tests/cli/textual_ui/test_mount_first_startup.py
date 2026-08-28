from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import build_test_vibe_app
from tests.stubs.app_config import build_test_app_config
from vibe.app_server import AppServerSession
from vibe.app_server.events import StatsUpdated
from vibe.app_server.models import AgentStatsSnapshot
from vibe.app_server.protocol import ConfigReadResponse, StatsUpdatedParams
from vibe.cli.textual_ui.widgets.banner.banner import Banner
from vibe.cli.textual_ui.widgets.chat_input import ChatInputBody, ChatInputContainer
from vibe.cli.textual_ui.widgets.context_progress import ContextProgress
from vibe.cli.textual_ui.widgets.narrator_status import NarratorStatus


@pytest.mark.asyncio
async def test_compose_yields_main_ui_when_no_session() -> None:
    async def _blocking_starter() -> AppServerSession:
        await asyncio.Event().wait()
        raise RuntimeError("unreachable")

    app = build_test_vibe_app(app_server=_blocking_starter)
    app._mount_first = True
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.1)
        assert app._app_server is None
        assert app.query_one(Banner) is not None
        assert app.query_one(ChatInputContainer) is not None
        assert app.query_one(NarratorStatus) is not None


@pytest.mark.asyncio
async def test_cold_mount_applies_configured_theme_not_fallback() -> None:
    # Cold mount-first path must apply the theme from the pre-read config so the
    # UI does not flash the fallback theme before the session opens.
    async def _blocking_starter() -> AppServerSession:
        await asyncio.Event().wait()
        raise RuntimeError("unreachable")

    config = build_test_app_config().model_copy(update={"theme": "gruvbox"})
    app = build_test_vibe_app(app_server=_blocking_starter)
    app._mount_first = True
    app._initial_config_response = ConfigReadResponse(config=config)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.1)
        assert app._app_server is None
        assert app.theme == "gruvbox"


@pytest.mark.asyncio
async def test_input_during_cold_bootstrap_waits_for_session() -> None:
    release = asyncio.Event()
    app = build_test_vibe_app()
    app._mount_first = True
    original_starter = app._start_app_server
    assert original_starter is not None

    async def _latched_starter() -> AppServerSession:
        await release.wait()
        return await original_starter()

    app._start_app_server = _latched_starter
    handle_command = AsyncMock()
    with patch.object(app, "_handle_command", handle_command):
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause(0.1)
            assert app._app_server is None

            await pilot.press("/")
            await pilot.press("shift+tab", "ctrl+backslash")
            await pilot.click(Banner)
            dispatch = asyncio.create_task(app._dispatch_idle_input("/mcp"))
            await pilot.pause(0.1)

            handle_command.assert_not_awaited()
            assert app._app_server is None
            assert not dispatch.done()

            release.set()
            await pilot.pause(0.3)
            await dispatch

        handle_command.assert_awaited_once_with("/mcp")


@pytest.mark.asyncio
async def test_registry_swapped_after_session_ready() -> None:
    app = build_test_vibe_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        assert app._app_server is not None
        container = app.query_one(ChatInputContainer)
        entries = container._get_slash_entries()
        assert len(entries) > 0
        banner = app.query_one(Banner)
        assert banner.state.active_model != ""
        context_progress = app.query_one(ContextProgress)
        assert context_progress.tokens.max_tokens > 0


@pytest.mark.asyncio
async def test_stats_update_keeps_context_progress_visible() -> None:
    app = build_test_vibe_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)

        app._update_context_progress(
            StatsUpdated(
                StatsUpdatedParams(
                    event_id=1,
                    session_id="session-1",
                    emitted_at=0,
                    stats=AgentStatsSnapshot(context_tokens=12_500),
                    context_window=200_000,
                )
            )
        )
        await pilot.pause()

        context_progress = app.query_one(ContextProgress)
        assert context_progress.tokens.max_tokens == 200_000
        assert context_progress.tokens.current_tokens == 12_500
        assert str(context_progress.render()) == "12k/200k tokens (6%)"


@pytest.mark.asyncio
async def test_real_managers_bound_to_widgets_after_cold_bootstrap() -> None:
    # Cold mount-first path: compose binds the idle noop voice/narrator
    # managers. Once the session opens, _complete_mount must re-bind the real
    # managers into the already-mounted widgets so voice (Ctrl+R) and narrator
    # status drive the real managers instead of the noops.
    app = build_test_vibe_app()
    app._mount_first = True
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        assert app._app_server is not None
        body = app.query_one(ChatInputBody)
        assert body._voice_manager is app._voice_manager
        # The text area holds the manager reference that Ctrl+R actually reads;
        # verify it was rebound too, not just the body's listener slot.
        assert body.input_widget is not None
        assert body.input_widget._voice_manager is app._voice_manager
        narrator_status = app.query_one(NarratorStatus)
        assert narrator_status._narrator_manager is app._narrator_manager


def test_cold_path_force_quit_does_not_access_config() -> None:
    app = build_test_vibe_app()
    assert app._app_server is None
    with patch.object(app, "_force_quit") as mock_quit:
        app.action_interrupt_or_quit()
        app.action_delete_right_or_quit()
    assert mock_quit.call_count == 2
