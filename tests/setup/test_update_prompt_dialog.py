from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from vibe.setup.update_prompt.update_prompt_dialog import (
    UpdateChoice,
    UpdatePromptApp,
    UpdatePromptResult,
)


async def _await_update_completion(app: UpdatePromptApp) -> None:
    while app._update_task is None:
        await asyncio.sleep(0.01)
    try:
        await app._update_task
    except Exception:
        pass


@pytest.mark.asyncio
async def test_dialog_returns_continue_on_continue_selection() -> None:
    app = UpdatePromptApp(current_version="1.0.0", latest_version="2.0.0")

    async with app.run_test() as pilot:
        await pilot.press("right")
        await pilot.press("enter")
        await pilot.pause()

    assert app.return_value is UpdatePromptResult.CONTINUE


@pytest.mark.asyncio
async def test_dialog_returns_updated_when_update_command_succeeds() -> None:
    app = UpdatePromptApp(current_version="1.0.0", latest_version="2.0.0")

    with patch(
        "vibe.setup.update_prompt.update_prompt_dialog.do_update",
        new=AsyncMock(return_value=True),
    ):
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await _await_update_completion(app)
            await pilot.pause()

    assert app.return_value is UpdatePromptResult.UPDATED


@pytest.mark.asyncio
async def test_dialog_returns_update_failed_when_update_command_fails() -> None:
    app = UpdatePromptApp(current_version="1.0.0", latest_version="2.0.0")

    with patch(
        "vibe.setup.update_prompt.update_prompt_dialog.do_update",
        new=AsyncMock(return_value=False),
    ):
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await _await_update_completion(app)
            await pilot.pause()

    assert app.return_value is UpdatePromptResult.UPDATE_FAILED


@pytest.mark.asyncio
async def test_dialog_default_selection_is_update() -> None:
    app = UpdatePromptApp(current_version="1.0.0", latest_version="2.0.0")

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._dialog is not None
        assert app._dialog.selected is UpdateChoice.UPDATE


@pytest.mark.asyncio
async def test_dialog_returns_quit_on_ctrl_q() -> None:
    app = UpdatePromptApp(current_version="1.0.0", latest_version="2.0.0")

    async with app.run_test() as pilot:
        await pilot.press("ctrl+q")
        await pilot.pause()

    assert app.return_value is UpdatePromptResult.QUIT


@pytest.mark.asyncio
async def test_dialog_returns_update_failed_when_do_update_raises() -> None:
    app = UpdatePromptApp(current_version="1.0.0", latest_version="2.0.0")

    with patch(
        "vibe.setup.update_prompt.update_prompt_dialog.do_update",
        new=AsyncMock(side_effect=OSError("boom")),
    ):
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await _await_update_completion(app)
            await pilot.pause()

    assert app.return_value is UpdatePromptResult.UPDATE_FAILED


@pytest.mark.asyncio
async def test_ctrl_q_during_update_cancels_subprocess_and_quits() -> None:
    app = UpdatePromptApp(current_version="1.0.0", latest_version="2.0.0")

    update_started = asyncio.Event()

    async def slow_update() -> bool:
        update_started.set()
        await asyncio.sleep(60)
        return True

    with patch(
        "vibe.setup.update_prompt.update_prompt_dialog.do_update", new=slow_update
    ):
        async with app.run_test() as pilot:
            await pilot.press("enter")
            await update_started.wait()
            await pilot.press("ctrl+q")
            await pilot.pause()

    assert app.return_value is UpdatePromptResult.QUIT
    assert app._update_task is not None
    assert app._update_task.cancelled() or app._update_task.done()
