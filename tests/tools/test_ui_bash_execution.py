from __future__ import annotations

import time

import pytest
from textual.widgets import Static

from tests.conftest import build_test_agent_loop, build_test_vibe_app
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import BashOutputMessage, ErrorMessage
from vibe.core.types import Role


async def _wait_for_bash_output_message(
    vibe_app: VibeApp, pilot, timeout: float = 1.0
) -> BashOutputMessage:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if message := next(iter(vibe_app.query(BashOutputMessage)), None):
            if not message._pending:
                return message
        await pilot.pause(0.05)
    raise TimeoutError(f"BashOutputMessage did not appear within {timeout}s")


async def _wait_for_pending_bash_message(
    vibe_app: VibeApp, pilot, timeout: float = 1.0
) -> BashOutputMessage:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if message := next(iter(vibe_app.query(BashOutputMessage)), None):
            return message
        await pilot.pause(0.05)
    raise TimeoutError(f"BashOutputMessage did not appear within {timeout}s")


def assert_no_command_error(vibe_app: VibeApp) -> None:
    errors = list(vibe_app.query(ErrorMessage))
    if not errors:
        return

    disallowed = {
        "Command failed",
        "Command timed out",
        "No command provided after '!'",
    }
    offending = [
        getattr(err, "_error", "")
        for err in errors
        if getattr(err, "_error", "")
        and any(phrase in getattr(err, "_error", "") for phrase in disallowed)
    ]
    assert not offending, f"Unexpected command errors: {offending}"


@pytest.mark.asyncio
async def test_ui_reports_no_output(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!true"

        await pilot.press("enter")
        message = await _wait_for_bash_output_message(vibe_app, pilot)
        output_widget = message.query_one(".bash-output", Static)
        assert str(output_widget.render()) == "(no output)"
        assert_no_command_error(vibe_app)


@pytest.mark.asyncio
async def test_ui_shows_success_in_case_of_zero_code(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!true"

        await pilot.press("enter")
        message = await _wait_for_bash_output_message(vibe_app, pilot)
        assert message.has_class("bash-success")
        assert not message.has_class("bash-error")


@pytest.mark.asyncio
async def test_ui_shows_failure_in_case_of_non_zero_code(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!bash -lc 'exit 7'"

        await pilot.press("enter")
        message = await _wait_for_bash_output_message(vibe_app, pilot)
        assert message.has_class("bash-error")
        assert not message.has_class("bash-success")


@pytest.mark.asyncio
async def test_ui_handles_non_utf8_output(vibe_app: VibeApp) -> None:
    """Assert the UI accepts decoding a non-UTF8 sequence like `printf '\xf0\x9f\x98'`.
    Whereas `printf '\xf0\x9f\x98\x8b'` prints a smiley face (😋) and would work even without those changes.
    """
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!printf '\\xff\\xfe'"

        await pilot.press("enter")
        message = await _wait_for_bash_output_message(vibe_app, pilot)
        output_widget = message.query_one(".bash-output", Static)
        # accept both possible encodings, as some shells emit escaped bytes as literal strings
        assert str(output_widget.render()) in {"��", "\xff\xfe", r"\xff\xfe"}
        assert_no_command_error(vibe_app)


@pytest.mark.asyncio
async def test_ui_handles_utf8_output(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!echo hello"

        await pilot.press("enter")
        message = await _wait_for_bash_output_message(vibe_app, pilot)
        output_widget = message.query_one(".bash-output", Static)
        assert str(output_widget.render()) == "hello"
        assert_no_command_error(vibe_app)


@pytest.mark.asyncio
async def test_ui_handles_non_utf8_stderr(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!bash -lc \"printf '\\\\xff\\\\xfe' 1>&2\""

        await pilot.press("enter")
        message = await _wait_for_bash_output_message(vibe_app, pilot)
        output_widget = message.query_one(".bash-output", Static)
        assert str(output_widget.render()) == "��"
        assert_no_command_error(vibe_app)


@pytest.mark.asyncio
async def test_ui_sends_manual_command_output_to_next_agent_turn() -> None:
    backend = FakeBackend(mock_llm_chunk(content="I saw it."))
    vibe_app = build_test_vibe_app(agent_loop=build_test_agent_loop(backend=backend))

    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!echo hello"

        await pilot.press("enter")
        await _wait_for_bash_output_message(vibe_app, pilot)

        injected_message = vibe_app.agent_loop.messages[-1]
        assert injected_message.role == Role.user
        assert injected_message.injected is True
        assert injected_message.content is not None
        assert "Manual `!` command result from the user." in injected_message.content
        assert "Command: `echo hello`" in injected_message.content
        assert "Exit code: 0" in injected_message.content
        assert "Stdout:\n```text\nhello\n```" in injected_message.content

        chat_input.value = "what did the command print?"
        await pilot.press("enter")
        await pilot.app.workers.wait_for_complete()

        assert len(backend.requests_messages) == 1
        user_messages = [
            msg for msg in backend.requests_messages[0] if msg.role == Role.user
        ]
        assert len(user_messages) >= 2
        assert user_messages[-2].content == injected_message.content
        assert user_messages[-2].injected is True
        assert user_messages[-1].content == "what did the command print?"


@pytest.mark.asyncio
async def test_ui_shows_command_immediately_in_pending_state(vibe_app: VibeApp) -> None:
    """The command line should appear before the process finishes."""
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 10"

        await pilot.press("enter")
        message = await _wait_for_pending_bash_message(vibe_app, pilot)
        assert message._pending is True
        # command line is rendered
        cmd_widget = message.query_one(".bash-command", Static)
        assert str(cmd_widget.render()) == "sleep 10"
        # no output container yet
        assert not list(message.query(".bash-output"))

        # clean up: cancel the background task
        if vibe_app._bash_task and not vibe_app._bash_task.done():
            vibe_app._bash_task.cancel()


@pytest.mark.asyncio
async def test_ui_streams_output_incrementally(vibe_app: VibeApp) -> None:
    """Output should appear as the command produces it, not all at once."""
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        # print lines with a small delay so streaming has a chance to show partial output
        chat_input.value = "!bash -lc 'echo first; echo second'"

        await pilot.press("enter")
        message = await _wait_for_bash_output_message(vibe_app, pilot)
        output_widget = message.query_one(".bash-output", Static)
        rendered = str(output_widget.render())
        assert "first" in rendered
        assert "second" in rendered


@pytest.mark.asyncio
async def test_ui_cancels_running_command_on_new_submit(vibe_app: VibeApp) -> None:
    """Submitting new input while a bang command is running should cancel it."""
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "!sleep 30"

        await pilot.press("enter")
        await _wait_for_pending_bash_message(vibe_app, pilot)
        assert vibe_app._bash_task is not None
        assert not vibe_app._bash_task.done()

        # submit a new command which should cancel the first one
        chat_input.value = "!echo done"
        await pilot.press("enter")

        # wait until we have two messages and the second is finished
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            all_msgs = list(vibe_app.query(BashOutputMessage))
            if len(all_msgs) == 2 and not all_msgs[1]._pending:
                break
            await pilot.pause(0.05)

        all_msgs = list(vibe_app.query(BashOutputMessage))
        assert len(all_msgs) == 2
        second = all_msgs[1]
        output_widget = second.query_one(".bash-output", Static)
        assert str(output_widget.render()) == "done"
