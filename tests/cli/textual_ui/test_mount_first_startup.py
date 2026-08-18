from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from tests.conftest import build_test_vibe_app
from vibe.app_server import AppServerSession
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
