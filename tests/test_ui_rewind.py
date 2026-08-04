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
from vibe.cli.textual_ui.widgets.rewind_app import RewindApp, _RewindStep
from vibe.cli.textual_ui.widgets.rewind_fork_message import RewindForkMessage
from vibe.utils.session_id import shorten_session_id


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
async def test_rewind_q_exits_mode_from_persistence_step() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await _enter_rewind(pilot)
        await pilot.press("enter")
        await pilot.pause(0.1)

        rewind_app = app.query_one(RewindApp)
        assert rewind_app._step == _RewindStep.PERSISTENCE

        await pilot.press("q")
        await _wait_until(pilot, lambda: not app._rewind_mode)

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

        # First enter picks the action, second enter confirms persistence
        await pilot.press("enter")
        await pilot.pause(0.1)
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

        # Confirm: pick action, then confirm persistence
        await pilot.press("enter")
        await pilot.pause(0.1)
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

        # Press "1" to select the first action, then "1" to confirm persistence
        await pilot.press("1")
        await pilot.pause(0.1)
        await pilot.press("1")
        await _wait_until(pilot, lambda: not app._rewind_mode)

        assert app._rewind_mode is False
        assert app._current_bottom_app == BottomApp.Input


@pytest.mark.asyncio
async def test_rewind_shows_persistence_step_after_action() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await _enter_rewind(pilot)

        rewind_app = app.query_one(RewindApp)
        assert rewind_app._step == _RewindStep.ACTION

        await pilot.press("enter")
        await pilot.pause(0.1)

        # Persistence step defaults to the first (in-place) option
        assert rewind_app._step == _RewindStep.PERSISTENCE
        assert rewind_app.selected_option == 0


@pytest.mark.asyncio
async def test_rewind_escape_on_persistence_step_returns_to_action() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await _enter_rewind(pilot)
        await pilot.press("enter")
        await pilot.pause(0.1)

        rewind_app = app.query_one(RewindApp)
        assert rewind_app._step == _RewindStep.PERSISTENCE

        await pilot.press("escape")
        await pilot.pause(0.1)

        # Back to action step, still in rewind mode
        assert rewind_app._step == _RewindStep.ACTION
        assert app._rewind_mode is True
        assert app._current_bottom_app == BottomApp.Rewind


@pytest.mark.asyncio
async def test_rewind_in_place_persists_in_current_session(monkeypatch) -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])
        old_session_id = app.app_server.session_id

        captured: dict[str, object] = {}
        rewind = app.app_server.resources.sessions.rewind

        async def recording_rewind(
            entry_id: str, *, restore_files: bool, inplace: bool = False
        ):
            captured["inplace"] = inplace
            captured["restore_files"] = restore_files
            return await rewind(entry_id, restore_files=restore_files, inplace=inplace)

        monkeypatch.setattr(
            app.app_server.resources.sessions, "rewind", recording_rewind
        )

        await _enter_rewind(pilot)
        # Action, then confirm the default in-place option
        await pilot.press("enter")
        await pilot.pause(0.1)
        await pilot.press("enter")
        await _wait_until(pilot, lambda: not app._rewind_mode)

        assert captured == {"inplace": True, "restore_files": False}
        assert app.app_server.session_id == old_session_id


@pytest.mark.asyncio
async def test_rewind_fork_creates_new_session(monkeypatch) -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        captured: dict[str, object] = {}
        rewind = app.app_server.resources.sessions.rewind

        async def recording_rewind(
            entry_id: str, *, restore_files: bool, inplace: bool = False
        ):
            captured["inplace"] = inplace
            return await rewind(entry_id, restore_files=restore_files, inplace=inplace)

        monkeypatch.setattr(
            app.app_server.resources.sessions, "rewind", recording_rewind
        )

        await _enter_rewind(pilot)
        # Action, then pick the fork (second) persistence option
        await pilot.press("enter")
        await pilot.pause(0.1)
        await pilot.press("2")
        await _wait_until(pilot, lambda: not app._rewind_mode)

        assert captured["inplace"] is False


@pytest.mark.asyncio
async def test_rewind_fork_shows_session_hint() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        old_session_id = app.app_server.session_id

        await _enter_rewind(pilot)
        await pilot.press("enter")
        await pilot.pause(0.1)
        await pilot.press("2")
        await _wait_until(pilot, lambda: not app._rewind_mode)

        new_session_id = app.app_server.session_id
        assert new_session_id != old_session_id

        hint = app.query_one(RewindForkMessage)
        content = hint.get_content()
        assert shorten_session_id(old_session_id) in content
        assert shorten_session_id(new_session_id) in content


@pytest.mark.asyncio
async def test_rewind_in_place_shows_no_session_hint() -> None:
    app = _make_app()
    async with app.run_test() as pilot:
        await _send_messages(pilot, ["hello", "world"])

        await _enter_rewind(pilot)
        await pilot.press("enter")
        await pilot.pause(0.1)
        await pilot.press("enter")
        await _wait_until(pilot, lambda: not app._rewind_mode)

        assert len(app.query(RewindForkMessage)) == 0
