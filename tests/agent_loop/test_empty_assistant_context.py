from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.core.types import FunctionCall, LLMMessage, Role, ToolCall


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_empty_assistant_response_filtered_from_subsequent_request(
    vibe_config, streaming
):
    backend = FakeBackend([
        [mock_llm_chunk(content="")],
        [mock_llm_chunk(content="Recovered")],
    ])
    agent = build_test_agent_loop(
        config=vibe_config, backend=backend, enable_streaming=streaming
    )
    [_ async for _ in agent.act("first")]
    [_ async for _ in agent.act("second")]

    assert len(backend.requests_messages) == 2
    empty_assistants = [
        m
        for m in backend.requests_messages[1]
        if m.role == Role.assistant and not m.content and not m.tool_calls
    ]
    assert len(empty_assistants) == 0, (
        f"Found empty assistant in context: {empty_assistants}"
    )


@pytest.mark.asyncio
async def test_assistant_with_tool_calls_preserved_in_subsequent_request(vibe_config):
    tc = ToolCall(
        id="call_test", index=0, function=FunctionCall(name="dummy", arguments="{}")
    )
    backend = FakeBackend([
        [mock_llm_chunk(content="", tool_calls=[tc])],
        [mock_llm_chunk(content="Answer")],
    ])
    agent = build_test_agent_loop(
        config=vibe_config, backend=backend, enable_streaming=False
    )
    # Put messages in history directly and verify _messages_for_backend preserves it
    active_model = agent.config.get_active_model()
    messages = [
        LLMMessage(role=Role.user, content="prompt"),
        LLMMessage(role=Role.assistant, content="", tool_calls=[tc]),
        LLMMessage(role=Role.tool, tool_call_id="call_test", content="result"),
    ]
    backend_messages = agent._messages_for_backend(messages, active_model)
    assistants = [m for m in backend_messages if m.role == Role.assistant]
    assert len(assistants) == 1
    assert assistants[0].tool_calls == [tc]


@pytest.mark.asyncio
async def test_assistant_with_reasoning_preserved_in_subsequent_request(vibe_config):
    agent_model = vibe_config.get_active_model()
    backend = FakeBackend([])
    agent = build_test_agent_loop(config=vibe_config, backend=backend)
    messages = [
        LLMMessage(role=Role.user, content="prompt"),
        LLMMessage(
            role=Role.assistant, content="", reasoning_content="I am reasoning deeply"
        ),
    ]
    backend_messages = agent._messages_for_backend(messages, agent_model)
    assistants = [m for m in backend_messages if m.role == Role.assistant]
    assert len(assistants) == 1
    assert assistants[0].reasoning_content == "I am reasoning deeply"
