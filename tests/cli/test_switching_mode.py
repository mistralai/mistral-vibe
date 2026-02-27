from __future__ import annotations

import pytest

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer
from vibe.cli.textual_ui.widgets.chat_input.body import ChatInputBody, _PromptSpinner
from vibe.cli.textual_ui.widgets.messages import UserMessage


@pytest.mark.asyncio
async def test_submit_ignored_while_switching_mode() -> None:
    """Enter press during mode switch must not clear input or send a message."""
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        body = app.query_one(ChatInputBody)
        body.switching_mode = True
        await pilot.pause(0.1)

        # Type some text and press enter
        app.query_one(ChatInputContainer).value = "hello world"
        await pilot.press("enter")
        await pilot.pause(0.1)

        # Text must remain in the input
        assert app.query_one(ChatInputContainer).value == "hello world"
        # No user message should have been posted
        assert len(app.query(UserMessage)) == 0


@pytest.mark.asyncio
async def test_submit_works_after_switching_mode_ends() -> None:
    """After switching_mode is set back to False, Enter should work normally."""
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        body = app.query_one(ChatInputBody)

        # Enable then disable switching mode
        body.switching_mode = True
        await pilot.pause(0.1)
        body.switching_mode = False
        await pilot.pause(0.1)

        # Now submit should work
        app.query_one(ChatInputContainer).value = "hello"
        await pilot.press("enter")
        await pilot.pause(0.1)

        assert app.query_one(ChatInputContainer).value == ""


@pytest.mark.asyncio
async def test_spinner_shown_while_switching_mode() -> None:
    """Prompt widget is hidden and spinner is mounted when switching_mode is True."""
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        body = app.query_one(ChatInputBody)
        prompt = body.prompt_widget
        assert prompt is not None
        assert prompt.display is True
        assert len(body.query(_PromptSpinner)) == 0

        body.switching_mode = True
        await pilot.pause(0.1)

        assert prompt.display is False
        assert len(body.query(_PromptSpinner)) == 1


@pytest.mark.asyncio
async def test_spinner_removed_after_switching_mode_ends() -> None:
    """Prompt is restored and spinner removed when switching_mode becomes False."""
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        body = app.query_one(ChatInputBody)
        body.switching_mode = True
        await pilot.pause(0.1)
        body.switching_mode = False
        await pilot.pause(0.1)

        assert body.prompt_widget is not None
        assert body.prompt_widget.display is True
        assert len(body.query(_PromptSpinner)) == 0


@pytest.mark.asyncio
async def test_rapid_switching_mode_no_duplicate_spinners() -> None:
    """Rapidly toggling switching_mode must never produce duplicate spinners."""
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        body = app.query_one(ChatInputBody)

        # Rapidly toggle several times
        for _ in range(5):
            body.switching_mode = True
            body.switching_mode = True  # double set
        await pilot.pause(0.1)

        assert len(body.query(_PromptSpinner)) == 1
