from __future__ import annotations

from typing import Any

import pytest
from textual.containers import VerticalGroup

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import SlashCommandMessage, UserMessage


@pytest.mark.parametrize("alias", ["/exit", "exit", "quit", ":q", ":quit"])
@pytest.mark.asyncio
async def test_exit_synonym_runs_exit_handler_and_is_not_sent_as_prompt(
    alias: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    vibe_app = build_test_vibe_app()
    async with vibe_app.run_test() as pilot:
        await pilot.pause(0.1)

        calls: list[str] = []

        async def _record_exit(**_kwargs: Any) -> None:
            calls.append(alias)

        monkeypatch.setattr(vibe_app, "_exit_app", _record_exit)

        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted(alias))
        await pilot.pause(0.2)

        assert calls == [alias]
        prompts = [
            m
            for m in vibe_app.query(UserMessage)
            if not isinstance(m, SlashCommandMessage)
        ]
        assert prompts == []


@pytest.mark.asyncio
async def test_exit_does_not_echo_the_command_into_a_ui_that_is_closing() -> None:
    vibe_app = build_test_vibe_app()
    async with vibe_app.run_test() as pilot:
        await pilot.pause(0.1)

        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("/exit"))
        await pilot.pause(0.2)

        assert vibe_app.query(SlashCommandMessage).nodes == []


@pytest.mark.asyncio
async def test_submit_after_teardown_does_not_raise() -> None:
    vibe_app = build_test_vibe_app()
    async with vibe_app.run_test() as pilot:
        await pilot.pause(0.1)

        await vibe_app.query_one(ChatInputContainer).remove()

        await vibe_app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("/help")
        )
        await pilot.pause(0.1)


@pytest.mark.asyncio
async def test_mounting_into_an_unattached_messages_area_does_not_raise() -> None:
    vibe_app = build_test_vibe_app()
    async with vibe_app.run_test() as pilot:
        await pilot.pause(0.1)

        # The state behind the reported MountError: #messages is resolvable but
        # not attached to the DOM, either not mounted yet or already detached.
        vibe_app._cached_messages_area = VerticalGroup(id="messages")

        await vibe_app._mount_and_scroll(UserMessage("late"))
        await pilot.pause(0.1)


@pytest.mark.asyncio
async def test_submit_while_exiting_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    vibe_app = build_test_vibe_app()
    async with vibe_app.run_test() as pilot:
        await pilot.pause(0.1)

        dispatched: list[str] = []

        async def _record(value: str) -> None:
            dispatched.append(value)

        monkeypatch.setattr(vibe_app, "_handle_user_message", _record)
        vibe_app._force_quit()

        await vibe_app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("hello")
        )
        await pilot.pause(0.1)

        assert dispatched == []
