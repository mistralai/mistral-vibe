from __future__ import annotations

import pytest

from tests.conftest import (
    build_test_vibe_app,
    build_test_vibe_config,
    stub_config_reload,
)
from tests.stubs.fake_voice_manager import FakeVoiceManager
from vibe.cli.textual_ui.widgets.chat_input import ChatInputBody, ChatTextArea


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_cursor_blink_preference_survives_focus_and_queue_selection(
    enabled: bool,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config(cursor_blink=enabled))
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        body = app.query_one(ChatInputBody)
        assert text_area.cursor_blink is enabled

        text_area.set_app_focus(False)
        assert text_area.cursor_blink is False
        text_area.set_app_focus(True)
        assert text_area.cursor_blink is enabled

        body._lock_input_for_selection()
        assert text_area.cursor_blink is False
        assert text_area.show_cursor is False
        text_area.set_app_focus(False)
        text_area.set_app_focus(True)
        assert text_area.cursor_blink is False

        body._unlock_input_for_edit()
        assert text_area.cursor_blink is enabled
        assert text_area.show_cursor is True
        await pilot.press("a", "b", "left")
        assert text_area.text == "ab"
        assert text_area.cursor_location == (0, 1)
        if not enabled:
            assert not text_area.blink_timer._active.is_set()
            assert text_area._draw_cursor is True


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("finish", ["stop", "error"])
async def test_recording_restores_cursor_preference(enabled: bool, finish: str) -> None:
    voice = FakeVoiceManager(is_voice_ready=True)
    app = build_test_vibe_app(
        config=build_test_vibe_config(cursor_blink=enabled), voice_manager=voice
    )
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        voice.start_recording()
        await pilot.pause()
        assert text_area.has_class("recording")
        assert text_area.cursor_blink is False
        text_area.set_app_focus(False)
        text_area.set_app_focus(True)
        assert text_area.cursor_blink is False

        if finish == "error":
            app.query_one(ChatInputBody).on_transcribe_error("test failure")
        else:
            await voice.stop_recording()
        await pilot.pause()
        assert not text_area.has_class("recording")
        assert text_area.cursor_blink is enabled


@pytest.mark.asyncio
async def test_reload_applies_cursor_preference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        assert text_area.cursor_blink is True
        for enabled in (False, True):
            stub_config_reload(
                monkeypatch, build_test_vibe_config(cursor_blink=enabled)
            )
            await app._reload_config()
            await pilot.pause()
            assert app.config.cursor_blink is enabled
            assert text_area.cursor_blink is enabled


@pytest.mark.asyncio
async def test_config_write_applies_cursor_preference() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        for enabled in (False, True):
            await app.app_server.resources.config.update({"cursor_blink": enabled})
            await pilot.pause()
            assert app.config.cursor_blink is enabled
            assert text_area.cursor_blink is enabled


@pytest.mark.asyncio
async def test_app_focus_initialized_before_watch_loop_fires() -> None:
    # Textual invokes watchers when they are registered, so the __init__ watch
    # loop already ran _refresh_cursor_blink: _app_has_focus must exist by then
    # for the config-load assignment below to be safe.
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        assert text_area._app_has_focus is True
        text_area.cursor_blink_enabled = False
        assert text_area.cursor_blink is False
        text_area.cursor_blink_enabled = True
        assert text_area.cursor_blink is True
        await pilot.pause()


@pytest.mark.asyncio
@pytest.mark.parametrize("app_focus_cycle", [False, True])
async def test_disabling_blink_leaves_caret_visible(app_focus_cycle: bool) -> None:
    # A live /config or /reload that turns blinking off while the caret sits in
    # the hidden blink phase must not leave a steady-but-invisible caret.
    app = build_test_vibe_app(config=build_test_vibe_config(cursor_blink=True))
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        assert text_area.cursor_blink is True
        # The blink timer toggles this every 500ms; pin the hidden phase.
        text_area._cursor_visible = False
        assert text_area._draw_cursor is False
        if app_focus_cycle:
            text_area.set_app_focus(False)
        await app.app_server.resources.config.update({"cursor_blink": False})
        await pilot.pause()
        assert text_area.cursor_blink is False
        assert text_area._draw_cursor is True
        if app_focus_cycle:
            text_area.set_app_focus(True)
            await pilot.pause()
            assert text_area.cursor_blink is False
            assert text_area._draw_cursor is True
