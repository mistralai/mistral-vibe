"""Test that conversation roles alternate properly to avoid API errors."""

import pytest

from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent import Agent
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.types import LLMMessage, Role


@pytest.mark.asyncio
async def test_consecutive_user_messages_get_assistant_acknowledgment():
    """Test that consecutive user messages trigger an assistant acknowledgment."""
    config = VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False),
        auto_compact_threshold=0,
        enabled_tools=[],
        system_prompt_id="tests",
        include_project_context=False,
        include_prompt_detail=False,
    )
    agent = Agent(config=config)
    
    # Set up messages with two consecutive user messages (invalid sequence)
    agent.messages = [
        LLMMessage(role=Role.system, content="System prompt"),
        LLMMessage(role=Role.user, content="Hello"),
        LLMMessage(role=Role.assistant, content="Hi there"),
        LLMMessage(role=Role.user, content="Second message"),  # Invalid: user after user
        LLMMessage(role=Role.user, content="Third message"),  # Invalid: user after user
    ]
    
    # Call _clean_message_history which includes _ensure_role_alternation
    agent._clean_message_history()
    
    # Verify that assistant acknowledgment was inserted
    roles = [msg.role for msg in agent.messages]
    
    # Should have: system, user, assistant, user, assistant(ack), user
    # The code inserts ONE acknowledgment after the first consecutive user message
    assert len(agent.messages) == 6
    assert roles[0] == Role.system
    assert roles[1] == Role.user
    assert roles[2] == Role.assistant
    assert roles[3] == Role.user
    assert roles[4] == Role.assistant  # Acknowledgment inserted here
    assert roles[5] == Role.user
    
    # Verify acknowledgment content
    assert "Understood" in agent.messages[4].content


@pytest.mark.asyncio
async def test_consecutive_assistant_messages_get_merged():
    """Test that consecutive assistant messages (without tool calls) get merged."""
    config = VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False),
        auto_compact_threshold=0,
        enabled_tools=[],
        system_prompt_id="tests",
        include_project_context=False,
        include_prompt_detail=False,
    )
    agent = Agent(config=config)
    
    # Set up messages with two consecutive assistant messages (invalid sequence)
    agent.messages = [
        LLMMessage(role=Role.system, content="System prompt"),
        LLMMessage(role=Role.user, content="Hello"),
        LLMMessage(role=Role.assistant, content="First response"),
        LLMMessage(role=Role.assistant, content="Second response"),  # Invalid: assistant after assistant
    ]
    
    # Call _clean_message_history which includes _ensure_role_alternation
    agent._clean_message_history()
    
    # Verify that messages were merged
    assert len(agent.messages) == 3
    roles = [msg.role for msg in agent.messages]
    assert roles[0] == Role.system
    assert roles[1] == Role.user
    assert roles[2] == Role.assistant
    
    # Verify merged content
    assert agent.messages[2].content == "First response\n\nSecond response"


@pytest.mark.asyncio
async def test_tool_messages_are_skipped_in_role_check():
    """Test that tool messages are properly skipped during role alternation check."""
    config = VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False),
        auto_compact_threshold=0,
        enabled_tools=[],
        system_prompt_id="tests",
        include_project_context=False,
        include_prompt_detail=False,
    )
    agent = Agent(config=config)
    
    # Set up valid sequence with tool messages
    agent.messages = [
        LLMMessage(role=Role.system, content="System prompt"),
        LLMMessage(role=Role.user, content="What is 2+2?"),
        LLMMessage(role=Role.assistant, content="Let me calculate", tool_calls=[]),
        LLMMessage(role=Role.tool, content="4", tool_call_id="call_1"),
        LLMMessage(role=Role.assistant, content="The answer is 4"),
    ]
    
    # Call _clean_message_history
    agent._clean_message_history()
    
    # Verify that the sequence remains valid (no changes)
    assert len(agent.messages) == 5
    roles = [msg.role for msg in agent.messages]
    assert roles == [Role.system, Role.user, Role.assistant, Role.tool, Role.assistant]


@pytest.mark.asyncio
async def test_compact_creates_valid_sequence():
    """Test that compact operation creates a valid message sequence."""
    # Use a mock backend to avoid calling real API
    mock_backend = FakeBackend([
        mock_llm_chunk(content="Understood.", finish_reason="stop")
    ])
    config = VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False),
        auto_compact_threshold=0,
        enabled_tools=[],
        system_prompt_id="tests",
        include_project_context=False,
        include_prompt_detail=False,
    )
    agent = Agent(config=config, backend=mock_backend)
    
    # Set up conversation history
    agent.messages = [
        LLMMessage(role=Role.system, content="System prompt"),
        LLMMessage(role=Role.user, content="First message"),
        LLMMessage(role=Role.assistant, content="First response"),
        LLMMessage(role=Role.user, content="Second message"),
        LLMMessage(role=Role.assistant, content="Second response"),
    ]
    
    # Compact the conversation
    summary = await agent.compact()
    
    # Verify the sequence after compact
    # Should be: system, user(summary), assistant(ack), user(summary)
    assert len(agent.messages) == 3
    roles = [msg.role for msg in agent.messages]
    assert roles[0] == Role.system
    assert roles[1] == Role.user  # Summary
    assert roles[2] == Role.assistant  # Acknowledgment
    
    # Verify acknowledgment content
    assert "Understood" in agent.messages[2].content
