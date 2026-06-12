from __future__ import annotations

import time

import pytest

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import (
    BashOutputMessage,
    QueueHeaderMessage,
    WarningMessage,
)


@pytest.fixture
def vibe_app() -> VibeApp:
    return build_test_vibe_app()


async def _wait_until(pilot, predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await pilot.pause(0.05)
    return False


@pytest.mark.asyncio
async def test_no_queue_header_when_empty(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test():
        headers = list(vibe_app.query(QueueHeaderMessage))
        assert headers == []


@pytest.mark.asyncio
async def test_bash_submitted_during_running_bash_is_queued(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 0.3"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: vibe_app._bash_task is not None, timeout=1.0)

        chat_input.value = "!echo queued"
        await pilot.press("enter")

        assert len(vibe_app._input_queue) == 1
        assert vibe_app._input_queue.items[0].content == "echo queued"

        headers = list(vibe_app.query(QueueHeaderMessage))
        assert len(headers) == 1

        queued_bashes = [w for w in vibe_app.query(BashOutputMessage) if w._queued]
        assert len(queued_bashes) == 1


@pytest.mark.asyncio
async def test_slash_command_rejected_with_warning_when_busy(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 0.3"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: vibe_app._bash_task is not None, timeout=1.0)

        chat_input.value = "/help"
        await pilot.press("enter")

        assert not list(vibe_app.query(WarningMessage))
        assert any(
            "Slash commands cannot be queued" in notification.message
            for notification in vibe_app._notifications
        )
        assert len(vibe_app._input_queue) == 0
        assert chat_input.value.startswith("/help")


@pytest.mark.asyncio
async def test_ctrl_c_pops_last_queued_item_lifo(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 2"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: vibe_app._bash_task is not None, timeout=2.0)

        chat_input.value = "!echo first"
        await pilot.press("enter")
        chat_input.value = "!echo second"
        await pilot.press("enter")

        assert len(vibe_app._input_queue) == 2

        await pilot.press("ctrl+c")
        assert len(vibe_app._input_queue) == 1
        assert vibe_app._input_queue.items[0].content == "echo first"

        await pilot.press("escape")
        await _wait_until(pilot, lambda: vibe_app._bash_task is None, timeout=5.0)


@pytest.mark.asyncio
async def test_escape_pauses_queue_when_job_running(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 2"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: vibe_app._bash_task is not None, timeout=2.0)

        chat_input.value = "!echo queued"
        await pilot.press("enter")
        assert len(vibe_app._input_queue) == 1

        await pilot.press("escape")
        assert vibe_app._input_queue.paused
        assert len(vibe_app._input_queue) == 1

        await _wait_until(pilot, lambda: vibe_app._bash_task is None, timeout=5.0)


@pytest.mark.asyncio
async def test_drain_runs_queued_bashes_in_fifo_order(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 0.2"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: vibe_app._bash_task is not None, timeout=1.0)

        chat_input.value = "!echo first"
        await pilot.press("enter")
        chat_input.value = "!echo second"
        await pilot.press("enter")

        await _wait_until(
            pilot,
            lambda: (
                len(list(vibe_app.query(BashOutputMessage))) == 3
                and all(not m._pending for m in vibe_app.query(BashOutputMessage))
            ),
            timeout=5.0,
        )

        msgs = list(vibe_app.query(BashOutputMessage))
        assert len(msgs) == 3
        assert vibe_app._input_queue.paused is False
        assert len(vibe_app._input_queue) == 0


@pytest.mark.asyncio
async def test_enter_on_empty_input_flushes_paused_queue(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 2"
        await pilot.press("enter")

        await _wait_until(pilot, lambda: vibe_app._bash_task is not None, timeout=2.0)

        chat_input.value = "!echo queued"
        await pilot.press("enter")
        assert len(vibe_app._input_queue) == 1

        await pilot.press("escape")
        assert vibe_app._input_queue.paused

        await _wait_until(pilot, lambda: vibe_app._bash_task is None, timeout=10.0)

        chat_input.value = ""
        await pilot.press("enter")

        await _wait_until(
            pilot,
            lambda: (
                not vibe_app._input_queue.paused and len(vibe_app._input_queue) == 0
            ),
            timeout=10.0,
        )

        assert not vibe_app._input_queue.paused
        assert len(vibe_app._input_queue) == 0


@pytest.mark.asyncio
async def test_quit_warning_shows_queue_count(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test():
        vibe_app._input_queue.append_prompt("a")
        vibe_app._input_queue.append_prompt("b")
        warning = vibe_app._queue.quit_warning_extra()
        assert warning == "2 queued messages will be discarded"

        vibe_app._input_queue.pop_last()
        warning = vibe_app._queue.quit_warning_extra()
        assert warning == "1 queued message will be discarded"

        vibe_app._input_queue.pop_last()
        assert vibe_app._queue.quit_warning_extra() == ""
