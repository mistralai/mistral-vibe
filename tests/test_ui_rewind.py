from __future__ import annotations

import asyncio
from contextlib import suppress
import time

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_app
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server.models import PublicCheckpointEntry
from vibe.cli.textual_ui.app import BottomApp, VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import UserMessage


def _make_app(num_responses: int = 3) -> VibeApp:
    backend = FakeBackend([
        mock_llm_chunk(content=f"Response {i + 1}") for i in range(num_responses)
    ])
    agent_loop = build_test_agent_loop(backend=backend)
    return build_test_vibe_app(agent_loop=agent_loop)


async def _send_messages(pilot, messages: list[str]) -> None:
    for msg in messages:
        await pilot.press(*msg)
        await pilot.press("enter")
        await _wait_until(pilot, lambda: not pilot.app._agent_job_active())


async def _wait_until(pilot, predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for UI state")
        await pilot.pause(0.01)


async def _enter_rewind(pilot) -> None:
    await pilot.press("escape", "escape")
    await _wait_until(
        pilot,
        lambda: (
            pilot.app._rewind_mode and pilot.app._rewind_highlighted_widget is not None
        ),
    )


@pytest.mark.asyncio
async def test_rewind_mode_activates_on_double_escape() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await _enter_rewind(pilot)

        assert app._rewind_mode is True
        assert app._current_bottom_app == BottomApp.Rewind


@pytest.mark.asyncio
async def test_rewind_highlights_last_user_message() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await _enter_rewind(pilot)

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget.get_content() == "world"


@pytest.mark.asyncio
async def test_rewind_navigates_to_previous_message() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await _enter_rewind(pilot)
        await pilot.press("left")
        await _wait_until(
            pilot,
            lambda: (
                app._rewind_highlighted_widget is not None
                and app._rewind_highlighted_widget.get_content() == "hello"
            ),
        )

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget.get_content() == "hello"


@pytest.mark.asyncio
async def test_rewind_navigates_down() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        # Go up once, then back down
        await _enter_rewind(pilot)
        await pilot.press("left")
        await _wait_until(
            pilot,
            lambda: (
                app._rewind_highlighted_widget is not None
                and app._rewind_highlighted_widget.get_content() == "hello"
            ),
        )
        await pilot.press("right")
        await _wait_until(
            pilot,
            lambda: (
                app._rewind_highlighted_widget is not None
                and app._rewind_highlighted_widget.get_content() == "world"
            ),
        )

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget.get_content() == "world"


@pytest.mark.asyncio
async def test_rewind_escape_navigates_to_previous() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await _enter_rewind(pilot)

        await pilot.press("escape")
        await _wait_until(
            pilot,
            lambda: (
                app._rewind_highlighted_widget is not None
                and app._rewind_highlighted_widget.get_content() == "hello"
            ),
        )

        assert app._rewind_mode is True
        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget.get_content() == "hello"


@pytest.mark.asyncio
async def test_rewind_q_exits_mode() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await _enter_rewind(pilot)

        await pilot.press("q")
        await _wait_until(pilot, lambda: not app._rewind_mode)

        assert app._rewind_mode is False
        assert app._rewind_highlighted_widget is None
        assert app._current_bottom_app == BottomApp.Input


@pytest.mark.asyncio
async def test_rewind_arrow_keys_navigate_messages() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await _enter_rewind(pilot)

        await pilot.press("left")
        await _wait_until(
            pilot,
            lambda: (
                app._rewind_highlighted_widget is not None
                and app._rewind_highlighted_widget.get_content() == "hello"
            ),
        )

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget.get_content() == "hello"

        await pilot.press("right")
        await _wait_until(
            pilot,
            lambda: (
                app._rewind_highlighted_widget is not None
                and app._rewind_highlighted_widget.get_content() == "world"
            ),
        )

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget.get_content() == "world"


@pytest.mark.asyncio
async def test_rewind_confirm_edits_message_and_prefills_input() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await _enter_rewind(pilot)

        # Confirm with enter (selects "Edit message from here")
        await pilot.press("enter")
        await _wait_until(pilot, lambda: not app._rewind_mode)

        assert app._rewind_mode is False
        assert app._current_bottom_app == BottomApp.Input

        # Input should be pre-filled with the rewound message
        chat_input = app.query_one(ChatInputContainer)
        assert chat_input.value == "world"


@pytest.mark.asyncio
async def test_rewind_truncates_public_history_and_appends_checkpoint() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["first", "second", "third"])

        # Navigate to "second"
        await _enter_rewind(pilot)
        await pilot.press("left")
        await _wait_until(
            pilot,
            lambda: (
                app._rewind_highlighted_widget is not None
                and app._rewind_highlighted_widget.get_content() == "second"
            ),
        )

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget.get_content() == "second"

        # Confirm
        await pilot.press("enter")
        await _wait_until(pilot, lambda: not app._rewind_mode)

        messages_area = app.query_one("#messages")
        user_widgets = [
            child for child in messages_area.children if isinstance(child, UserMessage)
        ]
        assert [widget.get_content() for widget in user_widgets] == ["first"]
        assert any(
            isinstance(entry, PublicCheckpointEntry) and entry.kind == "rewind"
            for entry in app.app_server.history
        )


@pytest.mark.asyncio
async def test_rewind_skips_command_messages() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello"])

        # Simulate a slash command inserting a client-only UserMessage.
        await app._mount_and_scroll(UserMessage("/model"))
        await pilot.pause(0.1)

        await _send_messages(pilot, ["world"])

        # Entering rewind should land on "world", not the command message
        await _enter_rewind(pilot)

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget.get_content() == "world"

        # Going previous should land on "hello", skipping the command message
        await pilot.press("left")
        await _wait_until(
            pilot,
            lambda: (
                app._rewind_highlighted_widget is not None
                and app._rewind_highlighted_widget.get_content() == "hello"
            ),
        )

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget.get_content() == "hello"


@pytest.mark.asyncio
async def test_rewind_does_not_activate_while_agent_running() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello"])

        blocker = asyncio.create_task(asyncio.Event().wait())
        app._agent_task = blocker

        app._start_rewind_mode()

        assert app._rewind_mode is False
        blocker.cancel()
        with suppress(asyncio.CancelledError):
            await blocker
        app._agent_task = None


@pytest.mark.asyncio
async def test_rewind_option_selection_with_number_keys() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello"])

        await _enter_rewind(pilot)

        # Press "1" to select first option directly
        await pilot.press("1")
        await _wait_until(pilot, lambda: not app._rewind_mode)

        assert app._rewind_mode is False
        assert app._current_bottom_app == BottomApp.Input
