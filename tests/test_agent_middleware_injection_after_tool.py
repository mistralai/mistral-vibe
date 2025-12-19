"""Test middleware injection after tool messages to ensure valid sequence."""

import pytest

from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent import Agent
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.middleware import (
    ConversationContext,
    MiddlewareAction,
    MiddlewareResult,
)
from vibe.core.modes import AgentMode
from vibe.core.types import AgentStats, LLMMessage, Role


class TestInjectionAfterToolMiddleware:
    """Middleware that injects after tool messages."""
    
    def __init__(self, message: str):
        self.message = message
        self.before_turn_called = False
    
    async def before_turn(self, context: ConversationContext) -> MiddlewareResult:
        self.before_turn_called = True
        return MiddlewareResult(
            action=MiddlewareAction.INJECT_MESSAGE,
            message=self.message
        )
    
    async def after_turn(self, context: ConversationContext) -> MiddlewareResult:
        return MiddlewareResult()
    
    def reset(self, reset_reason) -> None:
        pass


def make_config() -> VibeConfig:
    return VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False),
        auto_compact_threshold=0,
        enabled_tools=[],
        system_prompt_id="tests",
        include_project_context=False,
        include_prompt_detail=False,
    )


@pytest.mark.asyncio
async def test_injection_after_tool_message_creates_valid_sequence():
    """Test that middleware injection after tool message creates valid sequence.
    
    The sequence should be: system -> user -> assistant -> tool -> assistant (injected)
    Not: system -> user -> assistant -> tool -> tool (which is invalid)
    """
    config = make_config()
    agent = Agent(config=config, mode=AgentMode.DEFAULT)
    
    # Manually set up messages to simulate a tool call scenario
    agent.messages = [
        LLMMessage(role=Role.system, content="System prompt"),
        LLMMessage(role=Role.user, content="What is 2+2?"),
        LLMMessage(role=Role.assistant, content="Let me calculate", tool_calls=[]),
        LLMMessage(role=Role.tool, content="4", tool_call_id="call_1"),
    ]
    
    # Add test middleware that injects before turn
    test_mw = TestInjectionAfterToolMiddleware("Context warning: 50% of context used")
    agent.middleware_pipeline.add(test_mw)
    
    # Run before_turn middleware - this will return INJECT_MESSAGE
    context = ConversationContext(
        messages=agent.messages,
        stats=AgentStats(),
        config=config
    )
    
    result = await agent.middleware_pipeline.run_before_turn(context)
    
    # Verify the result
    assert result.action == MiddlewareAction.INJECT_MESSAGE
    assert result.message == "Context warning: 50% of context used"
    
    # Now handle the middleware result (this is what the agent does)
    events = []
    async for event in agent._handle_middleware_result(result):
        events.append(event)
    
    # Verify the message sequence is valid
    # Should be: system, user, assistant, tool, assistant (injected)
    roles = [msg.role for msg in agent.messages]
    
    # Check that we have 5 messages
    assert len(agent.messages) == 5, f"Expected 5 messages, got {len(agent.messages)}"
    
    # Check the sequence
    assert roles[0] == Role.system
    assert roles[1] == Role.user
    assert roles[2] == Role.assistant
    assert roles[3] == Role.tool
    assert roles[4] == Role.assistant, "Last message should be assistant, not tool"
    
    # Check that the injected message is in the new assistant message
    last_msg = agent.messages[-1]
    assert last_msg.content == "Context warning: 50% of context used"


@pytest.mark.asyncio
async def test_injection_after_assistant_message_modifies_it():
    """Test that middleware injection after assistant message modifies the assistant message."""
    config = make_config()
    agent = Agent(config=config, mode=AgentMode.DEFAULT)
    
    # Set up messages with assistant as last message
    agent.messages = [
        LLMMessage(role=Role.system, content="System prompt"),
        LLMMessage(role=Role.user, content="Hello"),
        LLMMessage(role=Role.assistant, content="Hello there", tool_calls=[]),
    ]
    
    # Add test middleware that injects before turn
    test_mw = TestInjectionAfterToolMiddleware("Warning message")
    agent.middleware_pipeline.add(test_mw)
    
    # Run before_turn middleware
    context = ConversationContext(
        messages=agent.messages,
        stats=AgentStats(),
        config=config
    )
    
    result = await agent.middleware_pipeline.run_before_turn(context)
    
    # Handle the middleware result
    async for event in agent._handle_middleware_result(result):
        pass
    
    # Verify the result
    assert result.action == MiddlewareAction.INJECT_MESSAGE
    
    # Verify the message sequence - should still be 3 messages
    assert len(agent.messages) == 3, f"Expected 3 messages, got {len(agent.messages)}"
    
    # Check that the assistant message was modified
    assistant_msg = agent.messages[2]
    assert assistant_msg.content == "Hello there\n\nWarning message"


@pytest.mark.asyncio
async def test_injection_with_empty_assistant_message():
    """Test that middleware injection works when assistant message has no content."""
    config = make_config()
    agent = Agent(config=config, mode=AgentMode.DEFAULT)
    
    # Set up messages with empty assistant message
    agent.messages = [
        LLMMessage(role=Role.system, content="System prompt"),
        LLMMessage(role=Role.user, content="Hello"),
        LLMMessage(role=Role.assistant, content="", tool_calls=[]),
    ]
    
    # Add test middleware that injects before turn
    test_mw = TestInjectionAfterToolMiddleware("Warning message")
    agent.middleware_pipeline.add(test_mw)
    
    # Run before_turn middleware
    context = ConversationContext(
        messages=agent.messages,
        stats=AgentStats(),
        config=config
    )
    
    result = await agent.middleware_pipeline.run_before_turn(context)
    
    # Handle the middleware result
    async for event in agent._handle_middleware_result(result):
        pass
    
    # Verify the result
    assert result.action == MiddlewareAction.INJECT_MESSAGE
    
    # Verify the message sequence - should still be 3 messages
    assert len(agent.messages) == 3, f"Expected 3 messages, got {len(agent.messages)}"
    
    # Check that the assistant message now has content
    assistant_msg = agent.messages[2]
    assert assistant_msg.content == "Warning message"
