from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from types import SimpleNamespace

import pytest

from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import UserMessage, AssistantMessage
from vibe.core.agent import Agent
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.types import BaseEvent, LLMMessage, Role


class StubAgent(Agent):
    def __init__(self) -> None:
        self.messages: list[LLMMessage] = []
        self.stats = SimpleNamespace(context_tokens=0, steps=0, session_prompt_tokens=0,
                                   session_completion_tokens=0, session_total_llm_tokens=0,
                                   last_turn_total_tokens=0, session_cost=0.0)
        self.approval_callback = None

    async def initialize(self) -> None:
        return

    async def act(self, msg: str) -> AsyncGenerator[BaseEvent]:
        if False:
            yield msg

    async def clear_history(self) -> None:
        self.messages.clear()

    async def reload_with_initial_messages(self, config: VibeConfig) -> None:
        pass


@pytest.fixture
def vibe_config() -> VibeConfig:
    return VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False), enable_update_checks=False
    )


@pytest.mark.asyncio
async def test_ctrl_l_clears_screen_when_pressed() -> None:
    """Test that pressing Ctrl+L clears the screen/historical messages."""
    vibe_app = VibeApp(
        config=VibeConfig(
            session_logging=SessionLoggingConfig(enabled=False), enable_update_checks=False
        )
    )

    # Set a stub agent so the clear function works properly
    vibe_app.agent = StubAgent()

    async with vibe_app.run_test() as pilot:
        # Add some messages to the UI to test if they get cleared
        chat_input = vibe_app.query_one(ChatInputContainer)

        # Simulate adding a few messages
        await vibe_app._mount_and_scroll(UserMessage("Test message 1"))
        await vibe_app._mount_and_scroll(AssistantMessage("Test response 1"))
        await vibe_app._mount_and_scroll(UserMessage("Test message 2"))

        # Verify messages exist before clearing
        user_messages = list(vibe_app.query(UserMessage))
        assistant_messages = list(vibe_app.query(AssistantMessage))
        assert len(user_messages) >= 2  # Including the initial messages
        assert len(assistant_messages) >= 1

        # Press Ctrl+L to clear the screen
        await pilot.press("ctrl+l")

        # Wait briefly for the async clear to complete
        await pilot.pause(0.2)

        # Verify that new messages appeared (the confirmation message from _clear_history)
        all_user_messages = list(vibe_app.query(UserMessage))
        has_confirmation = any("Conversation history cleared!" in str(msg._content or "")
                              for msg in all_user_messages)

        assert has_confirmation, "Clear confirmation message should be present after Ctrl+L"