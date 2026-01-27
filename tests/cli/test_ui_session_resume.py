from __future__ import annotations

import pytest

from tests.cli.plan_offer.adapters.fake_whoami_gateway import FakeWhoAmIGateway
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.messages import AssistantMessage, UserMessage
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage, ToolResultMessage
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.types import FunctionCall, LLMMessage, Role, ToolCall


@pytest.fixture
def vibe_config() -> VibeConfig:
    return VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False), enable_update_checks=False
    )


@pytest.mark.asyncio
async def test_ui_displays_messages_when_resuming_session(
    vibe_config: VibeConfig,
) -> None:
    """Test that messages are properly displayed when resuming a session."""
    agent_loop = AgentLoop(config=vibe_config, enable_streaming=False)

    # Simulate a previous session with messages
    user_msg = LLMMessage(role=Role.user, content="Hello, how are you?")
    assistant_msg = LLMMessage(
        role=Role.assistant,
        content="I'm doing well, thank you!",
        tool_calls=[
            ToolCall(
                id="tool_call_1",
                index=0,
                function=FunctionCall(
                    name="read_file", arguments='{"path": "test.txt"}'
                ),
            )
        ],
    )
    tool_result_msg = LLMMessage(
        role=Role.tool,
        content="File content here",
        name="read_file",
        tool_call_id="tool_call_1",
    )

    agent_loop.messages.extend([user_msg, assistant_msg, tool_result_msg])

    app = VibeApp(agent_loop=agent_loop, plan_offer_gateway=FakeWhoAmIGateway())

    async with app.run_test() as pilot:
        # Wait for the app to initialize and rebuild history
        await pilot.pause(0.5)

        # Verify user message is displayed
        user_messages = app.query(UserMessage)
        assert len(user_messages) == 1
        assert user_messages[0]._content == "Hello, how are you?"

        # Verify assistant message is displayed
        assistant_messages = app.query(AssistantMessage)
        assert len(assistant_messages) == 1
        assert assistant_messages[0]._content == "I'm doing well, thank you!"

        # Verify tool call message is displayed
        tool_call_messages = app.query(ToolCallMessage)
        assert len(tool_call_messages) == 1
        assert tool_call_messages[0]._tool_name == "read_file"

        # Verify tool result message is displayed
        tool_result_messages = app.query(ToolResultMessage)
        assert len(tool_result_messages) == 1
        assert tool_result_messages[0].tool_name == "read_file"
        assert tool_result_messages[0]._content == "File content here"


@pytest.mark.asyncio
async def test_ui_does_not_display_messages_when_only_system_messages_exist(
    vibe_config: VibeConfig,
) -> None:
    """Test that no messages are displayed when only system messages exist."""
    agent_loop = AgentLoop(config=vibe_config, enable_streaming=False)

    # Only system messages
    system_msg = LLMMessage(role=Role.system, content="System prompt")
    agent_loop.messages.append(system_msg)

    app = VibeApp(agent_loop=agent_loop, plan_offer_gateway=FakeWhoAmIGateway())

    async with app.run_test() as pilot:
        await pilot.pause(0.5)

        # Verify no user or assistant messages are displayed
        user_messages = app.query(UserMessage)
        assert len(user_messages) == 0

        assistant_messages = app.query(AssistantMessage)
        assert len(assistant_messages) == 0


@pytest.mark.asyncio
async def test_ui_displays_multiple_user_assistant_turns(
    vibe_config: VibeConfig,
) -> None:
    """Test that multiple conversation turns are properly displayed."""
    agent_loop = AgentLoop(config=vibe_config, enable_streaming=False)

    # Multiple conversation turns
    messages = [
        LLMMessage(role=Role.user, content="First question"),
        LLMMessage(role=Role.assistant, content="First answer"),
        LLMMessage(role=Role.user, content="Second question"),
        LLMMessage(role=Role.assistant, content="Second answer"),
    ]

    agent_loop.messages.extend(messages)

    app = VibeApp(agent_loop=agent_loop, plan_offer_gateway=FakeWhoAmIGateway())

    async with app.run_test() as pilot:
        await pilot.pause(0.5)

        # Verify all messages are displayed
        user_messages = app.query(UserMessage)
        assert len(user_messages) == 2
        assert user_messages[0]._content == "First question"
        assert user_messages[1]._content == "Second question"

        assistant_messages = app.query(AssistantMessage)
        assert len(assistant_messages) == 2
        assert assistant_messages[0]._content == "First answer"
        assert assistant_messages[1]._content == "Second answer"
