from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_app
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
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
        await pilot.pause(0.4)


@pytest.mark.asyncio
async def test_rewind_mode_activates_on_alt_up() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        assert app._rewind_mode is True
        assert app._current_bottom_app == BottomApp.Rewind


@pytest.mark.asyncio
async def test_rewind_highlights_last_user_message() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget._content == "world"


@pytest.mark.asyncio
async def test_rewind_navigates_to_previous_message() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)
        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget._content == "hello"


@pytest.mark.asyncio
async def test_rewind_navigates_down() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        # Go up twice, then down once
        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)
        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)
        await pilot.press("alt+down")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget._content == "world"


@pytest.mark.asyncio
async def test_rewind_escape_exits_mode() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        await pilot.press("escape")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        assert app._rewind_mode is False
        assert app._rewind_highlighted_widget is None
        assert app._current_bottom_app == BottomApp.Input


@pytest.mark.asyncio
async def test_rewind_ctrl_p_n_alternate_bindings() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        # ctrl+p should enter rewind mode
        await pilot.press("ctrl+p")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        assert app._rewind_mode is True
        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget._content == "world"

        # ctrl+p again goes to previous
        await pilot.press("ctrl+p")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget._content == "hello"

        # ctrl+n goes back
        await pilot.press("ctrl+n")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget._content == "world"


@pytest.mark.asyncio
async def test_rewind_confirm_edits_message_and_prefills_input() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

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
        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)
        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        assert app._rewind_highlighted_widget is not None
        assert app._rewind_highlighted_widget._content == "second"

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
        assert user_widgets[0]._content == "first"


@pytest.mark.asyncio
async def test_rewind_does_not_activate_while_agent_running() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello"])

        app._agent_running = True

        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        assert app._rewind_mode is False


@pytest.mark.asyncio
async def test_rewind_option_selection_with_number_keys() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello"])

        await pilot.press("alt+up")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.1)

        # Press "1" to select first option directly
        await pilot.press("1")
        await pilot.app.workers.wait_for_complete()
        await pilot.pause(0.2)

        assert app._rewind_mode is False
        assert app._current_bottom_app == BottomApp.Input
