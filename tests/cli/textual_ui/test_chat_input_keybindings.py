from __future__ import annotations

import pytest
from textual.geometry import Offset
from textual.selection import Selection

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer, ChatTextArea


@pytest.mark.asyncio
async def test_shift_backspace_deletes_character_like_backspace() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        await pilot.press("a", "b", "c")
        await pilot.pause(0.1)
        assert app.query_one(ChatInputContainer).value == "abc"

        await pilot.press("shift+backspace")
        await pilot.pause(0.1)

        assert app.query_one(ChatInputContainer).value == "ab"


@pytest.mark.asyncio
async def test_shift_delete_deletes_character_like_delete() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        await pilot.press("a", "b", "c", "left")
        await pilot.pause(0.1)
        assert app.query_one(ChatInputContainer).value == "abc"

        await pilot.press("shift+delete")
        await pilot.pause(0.1)

        assert app.query_one(ChatInputContainer).value == "ab"


@pytest.mark.asyncio
async def test_shift_backspace_resets_mode_when_empty() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        text_area.set_mode("!")
        await pilot.pause(0.1)
        assert text_area.input_mode == "!"

        await pilot.press("shift+backspace")
        await pilot.pause(0.1)

        assert text_area.input_mode == ">"


@pytest.mark.asyncio
async def test_selection_cleared_when_focus_leaves_text_area() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        text_area.load_text("hello world")
        text_area.select_all()
        await pilot.pause(0.1)
        assert text_area.selected_text == "hello world"

        app.query_one("#chat").focus()
        await pilot.pause(0.1)

        assert text_area.selected_text == ""


@pytest.mark.asyncio
async def test_blur_does_not_clear_other_widget_selection() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        text_area.load_text("hello world")
        text_area.select_all()
        await pilot.pause(0.1)

        chat = app.query_one("#chat")
        sentinel = {chat: Selection(Offset(0, 0), Offset(0, 1))}
        app.screen.selections = sentinel
        text_area.screen.set_focus(chat)
        await pilot.pause(0.1)

        assert text_area.selected_text == ""
        assert app.screen.selections == sentinel
