from __future__ import annotations

from pathlib import Path
import time
from unittest.mock import patch

import pytest

from tests.cli.plan_offer.adapters.fake_whoami_gateway import FakeWhoAmIGateway
from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_app,
    build_test_vibe_config,
)
from tests.update_notifier.adapters.fake_update_cache_repository import (
    FakeUpdateCacheRepository,
)
from tests.update_notifier.adapters.fake_update_gateway import FakeUpdateGateway
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIResponse
from vibe.cli.textual_ui.widgets.messages import (
    AssistantMessage,
    UserMessage,
    WhatsNewMessage,
)
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage, ToolResultMessage
from vibe.cli.update_notifier import UpdateCache
from vibe.core.config import VibeConfig
from vibe.core.types import FunctionCall, LLMMessage, Role, ToolCall


@pytest.mark.asyncio
async def test_ui_displays_messages_when_resuming_session(
    vibe_config: VibeConfig,
) -> None:
    """Test that messages are properly displayed when resuming a session."""
    agent_loop = build_test_agent_loop(config=vibe_config)

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

    app = build_test_vibe_app(agent_loop=agent_loop)

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
    agent_loop = build_test_agent_loop(config=vibe_config)

    # Only system messages
    system_msg = LLMMessage(role=Role.system, content="System prompt")
    agent_loop.messages.append(system_msg)

    app = build_test_vibe_app(agent_loop=agent_loop)

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
    agent_loop = build_test_agent_loop(config=vibe_config)

    # Multiple conversation turns
    messages = [
        LLMMessage(role=Role.user, content="First question"),
        LLMMessage(role=Role.assistant, content="First answer"),
        LLMMessage(role=Role.user, content="Second question"),
        LLMMessage(role=Role.assistant, content="Second answer"),
    ]

    agent_loop.messages.extend(messages)

    app = build_test_vibe_app(agent_loop=agent_loop)

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


@pytest.mark.asyncio
async def test_ui_rebuilds_history_when_whats_new_is_shown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # we have to define an api key to make sure we display the Plan Offer message
    monkeypatch.setenv("MISTRAL_API_KEY", "api-key")
    config = build_test_vibe_config(enable_update_checks=True)
    agent_loop = build_test_agent_loop(config=config)
    agent_loop.messages.extend([
        LLMMessage(role=Role.user, content="Hello from the previous session."),
        LLMMessage(role=Role.assistant, content="Welcome back!"),
    ])
    update_cache = UpdateCache(
        latest_version="1.0.0",
        stored_at_timestamp=int(time.time()),
        seen_whats_new_version=None,
    )
    update_cache_repository = FakeUpdateCacheRepository(update_cache=update_cache)
    plan_offer_gateway = FakeWhoAmIGateway(
        WhoAmIResponse(
            is_pro_plan=False,
            advertise_pro_plan=True,
            prompt_switching_to_pro_plan=False,
        )
    )
    app = build_test_vibe_app(
        agent_loop=agent_loop,
        update_notifier=FakeUpdateGateway(update=None),
        update_cache_repository=update_cache_repository,
        plan_offer_gateway=plan_offer_gateway,
        current_version="1.0.0",
        config=config,
    )

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        whats_new_file = tmp_path / "whats_new.md"
        whats_new_file.write_text("# What's New\n\n- Feature 1")

        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            whats_new_message = app.query_one(WhatsNewMessage)
            message = app.query_one(UserMessage)
            assistant_message = app.query_one(AssistantMessage)
            messages_area = app.query_one("#messages")
            children = list(messages_area.children)

    assert message._content == "Hello from the previous session."
    assert whats_new_message is not None
    assert "after-history" in whats_new_message.classes
    assert children == [message, assistant_message, whats_new_message]
