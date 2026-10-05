from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import PropertyMock

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_app, wait_until
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server.protocol import SessionTextContentBlock
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer
from vibe.cli.textual_ui.widgets.chat_input.completion_popup import CompletionPopup
from vibe.cli.textual_ui.widgets.messages import ErrorMessage, SlashCommandMessage


@pytest.mark.asyncio
@pytest.mark.parametrize("experimental_harness", [False, True])
async def test_loop_remains_available_in_autocomplete(
    experimental_harness: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = build_test_vibe_app()

    async with app.run_test() as pilot:
        await app.app_server.resources.runtime.wait_until_ready()
        monkeypatch.setattr(
            type(app.app_server.resources.runtime),
            "experimental_harness",
            PropertyMock(return_value=experimental_harness),
        )
        app._refresh_command_registry()
        chat_input = app.query_one(ChatInputContainer)
        chat_input.focus_input()
        await pilot.press("/", "l", "o", "o")
        popup = app.query_one(CompletionPopup)

        assert await wait_until(pilot, lambda: "/loop" in popup.content_text)
        await pilot.press("tab")
        assert chat_input.value == "/loop"


class FakeBlockingBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__([[mock_llm_chunk(content="done")]] * 2)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def complete(self, **kwargs):
        self.started.set()
        await self.release.wait()
        return await super().complete(**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prompt",
    [
        "/loop",
        "/loop list",
        "/loop cancel",
        "/loop cancel abc123",
        "/loop cancel all",
        "/loop 30s check\nthe build",
        "/loop every weekday at 9am check the build",
    ],
)
async def test_unified_loop_prompts_reach_model_unchanged_idle_and_queued(
    prompt: str, monkeypatch: pytest.MonkeyPatch, telemetry_events: list[dict[str, Any]]
) -> None:
    backend = FakeBlockingBackend()
    app = build_test_vibe_app(agent_loop=build_test_agent_loop(backend=backend))

    async with app.run_test() as pilot:
        await app.app_server.resources.runtime.wait_until_ready()
        runtime = app.app_server.resources.runtime
        monkeypatch.setattr(
            type(runtime), "experimental_harness", PropertyMock(return_value=True)
        )
        app._refresh_command_registry()

        assert app.commands.has_command("loop")
        assert "/loop" in app.commands.get_help_text()
        assert "/loop list" not in app.commands.get_help_text()
        assert "/loop cancel" not in app.commands.get_help_text()
        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted(prompt)
        )
        assert await wait_until(pilot, backend.started.is_set)
        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted(prompt)
        )

        def queued_texts() -> list[str]:
            return [
                block.text
                for item in app.app_server.turn_queue.items
                for entry in item.entries
                if entry.role == "user"
                for block in entry.content
                if isinstance(block, SessionTextContentBlock)
            ]

        assert await wait_until(pilot, lambda: queued_texts() == [prompt])
        assert not list(app.query(SlashCommandMessage))
        assert not list(app.query(ErrorMessage))
        assert await app.app_server.resources.loops.list() == []

        backend.release.set()
        assert await wait_until(
            pilot,
            lambda: len(backend.requests_messages) == 2 and not app._agent_job_active(),
        )

    assert [request[-1].content for request in backend.requests_messages] == [
        prompt,
        prompt,
    ]
    assert [
        (event["properties"]["command"], event["properties"]["command_type"])
        for event in telemetry_events
        if event["event_name"] == "vibe.slash_command_used"
    ] == [("loop", "builtin"), ("loop", "builtin")]
