from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_app
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.cli.textual_ui.app import BottomApp, VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import UserMessage
from vibe.core.types import LLMMessage, Role


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
        await pilot.pause(0.4)


async def _enter_rewind(pilot) -> None:
    await pilot.press("escape", "escape")
    await pilot.app.workers.wait_for_complete()
    await pilot.pause(0.1)


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
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

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
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)
        await pilot.press("right")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget.get_content() == "world"


@pytest.mark.asyncio
async def test_rewind_escape_navigates_to_previous() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await _enter_rewind(pilot)

        await pilot.press("escape")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

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
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

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
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget.get_content() == "hello"

        await pilot.press("right")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

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
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.2)

        assert app._rewind_mode is False
        assert app._current_bottom_app == BottomApp.Input

        # Input should be pre-filled with the rewound message
        chat_input = app.query_one(ChatInputContainer)
        assert chat_input.value == "world"


@pytest.mark.asyncio
async def test_rewind_removes_messages_after_selected() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["first", "second", "third"])

        # Navigate to "second"
        await _enter_rewind(pilot)
        await pilot.press("left")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget.get_content() == "second"

        # Confirm
        await pilot.press("enter")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.2)

        # Only "first" should remain as a UserMessage
        messages_area = app.query_one("#messages")
        user_widgets = [
            child for child in messages_area.children if isinstance(child, UserMessage)
        ]
        assert len(user_widgets) == 1
        assert user_widgets[0].get_content() == "first"


@pytest.mark.asyncio
async def test_rewind_skips_command_messages() -> None:
    """Slash-command echo messages (message_index=None) are not rewind-selectable."""
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello"])

        # Simulate a slash command inserting a UserMessage without message_index
        await app._mount_and_scroll(UserMessage("/model"))
        await pilot.pause(0.1)

        await _send_messages(pilot, ["world"])

        # Entering rewind should land on "world", not the command message
        await _enter_rewind(pilot)

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget.get_content() == "world"

        # Going previous should land on "hello", skipping the command message
        await pilot.press("left")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget.get_content() == "hello"


@pytest.mark.asyncio
async def test_rewind_edits_uncommitted_user_message() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await app._mount_and_scroll(UserMessage("first", message_index=1))
        await pilot.pause(0.1)

        await _enter_rewind(pilot)
        await pilot.press("enter")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        chat_input = app.query_one(ChatInputContainer)
        assert app._rewind_mode is False
        assert chat_input.value == "first"


@pytest.mark.asyncio
async def test_rewind_does_not_activate_while_agent_running() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello"])

        app._agent_running = True

        app._start_rewind_mode()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        assert app._rewind_mode is False


def test_next_user_message_index_accounts_for_pending_system_prompt() -> None:
    app = _make_app()
    # Simulate deferred init not yet complete: system prompt not inserted.
    app.agent_loop.messages.reset([])
    # The system prompt will be inserted at index 0, so the user message lands at 1.
    assert app._next_user_message_index() == 1


def test_next_user_message_index_when_system_prompt_present() -> None:
    app = _make_app()
    app.agent_loop.messages.reset([
        LLMMessage(role=Role.system, content="sys"),
        LLMMessage(role=Role.user, content="hi"),
    ])
    assert app._next_user_message_index() == 2


@pytest.mark.asyncio
async def test_rewind_option_selection_with_number_keys() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello"])

        await _enter_rewind(pilot)

        # Press "1" to select first option directly
        await pilot.press("1")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.2)

        assert app._rewind_mode is False
        assert app._current_bottom_app == BottomApp.Input
