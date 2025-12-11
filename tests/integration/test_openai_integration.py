
import os
import pytest
import asyncio
from vibe.core.config import VibeConfig, ModelConfig, ProviderConfig, Backend
from vibe.core.llm_client import LLMClient
from vibe.core.types import AgentStats, LLMMessage, Role
from vibe.core.llm.format import APIToolFormatHandler
from vibe.core.middleware import MiddlewarePipeline
from vibe.core.tools.manager import ToolManager

@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="No OPENAI_API_KEY")
@pytest.mark.asyncio
async def test_openai_completion():
    """Test actual OpenAI API call with GPT-4o-mini."""
    # Setup minimal config
    config = VibeConfig.load(active_model="gpt4o-mini")
    stats = AgentStats()
    tool_manager = ToolManager(config)
    format_handler = APIToolFormatHandler(tool_manager)
    middleware = MiddlewarePipeline(config)

    client = LLMClient(
        config=config,
        stats=stats,
        session_id="test-openai",
        tool_manager=tool_manager,
        format_handler=format_handler,
        middleware_pipeline=middleware
    )

    messages = [LLMMessage(role=Role.user, content="Say 'Hello from ChefChat' and nothing else")]

    result = await client.chat(messages, max_tokens=20)

    assert result is not None
    assert result.message.content is not None
    assert "ChefChat" in result.message.content or "Hello" in result.message.content
    assert result.usage.completion_tokens > 0

@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="No OPENAI_API_KEY")
@pytest.mark.asyncio
async def test_openai_streaming():
    """Test OpenAI streaming completion."""
    config = VibeConfig.load(active_model="gpt4o-mini")
    stats = AgentStats()
    tool_manager = ToolManager(config)
    format_handler = APIToolFormatHandler(tool_manager)
    middleware = MiddlewarePipeline(config)

    client = LLMClient(
        config=config,
        stats=stats,
        session_id="test-openai-stream",
        tool_manager=tool_manager,
        format_handler=format_handler,
        middleware_pipeline=middleware
    )

    messages = [LLMMessage(role=Role.user, content="Count from 1 to 5")]

    chunks_received = 0
    content = ""
    async for event in client.stream_assistant_events(messages):
        chunks_received += 1
        content += event.content

    assert chunks_received > 0
    assert len(content) > 0
