from __future__ import annotations

import pytest

from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.agent import Agent
from vibe.core.config import SessionLoggingConfig, VibeConfig
from vibe.core.types import Role, LLMMessage

@pytest.mark.asyncio
async def test_manual_compact_works() -> None:
    # Setup
    backend = FakeBackend([
        mock_llm_chunk(content="Detailed summary of conversation."),
    ])
    cfg = VibeConfig(
        session_logging=SessionLoggingConfig(enabled=False)
    )
    agent = Agent(cfg, backend=backend)

    # Add some history
    agent.add_message(LLMMessage(role=Role.user, content="Task 1"))
    agent.add_message(LLMMessage(role=Role.assistant, content="Result 1"))
    agent.add_message(LLMMessage(role=Role.user, content="Task 2"))

    # Act
    summary = await agent.compact()

    # Assert
    assert summary == "Detailed summary of conversation.\n\nLast request from user was: Task 2"

    # Verify messages are reset: [System, Summary]
    # Agent typically initializes with a system prompt.
    assert len(agent.messages) == 2
    assert agent.messages[0].role == Role.system
    assert agent.messages[1].role == Role.user
    assert "Detailed summary of conversation." in agent.messages[1].content
    assert "Last request from user was: Task 2" in agent.messages[1].content

    # Verify stats context tokens are updated (FakeBackend returns 1 by default)
    assert agent.stats.context_tokens == 1
