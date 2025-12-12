from __future__ import annotations

from chefchat.core.config import Backend, ProviderConfig
from chefchat.core.llm.backend.generic import OpenAIAdapter
from chefchat.core.types import AvailableTool, LLMMessage, Role


def test_openai_adapter_prepare_request_basic():
    """Verify OpenAI adapter prepares basic chat request correctly."""
    adapter = OpenAIAdapter()
    provider = ProviderConfig(
        name="openai",
        api_base="https://api.openai.com/v1",
        api_key_env_var="OPENAI_API_KEY",
        backend=Backend.GENERIC
    )

    messages = [LLMMessage(role=Role.user, content="Hello")]

    endpoint, headers, body = adapter.prepare_request(
        model_name="gpt-4o",
        messages=messages,
        temperature=0.2,
        tools=None,
        max_tokens=None,
        tool_choice=None,
        enable_streaming=False,
        provider=provider,
        api_key="test-key"
    )

    assert endpoint == "/chat/completions"
    assert headers["Content-Type"] == "application/json"
    assert headers["Authorization"] == "Bearer test-key"

    import json
    payload = json.loads(body)
    assert payload["model"] == "gpt-4o"
    assert payload["temperature"] == 0.2
    assert len(payload["messages"]) == 1
    assert payload["messages"][0]["role"] == "user"
    assert payload["messages"][0]["content"] == "Hello"

def test_openai_adapter_prepare_request_with_tools():
    """Verify OpenAI adapter handles tool definitions correctly."""
    adapter = OpenAIAdapter()
    provider = ProviderConfig(
        name="openai",
        api_base="https://api.openai.com/v1",
        api_key_env_var="OPENAI_API_KEY",
        backend=Backend.GENERIC
    )

    messages = [LLMMessage(role=Role.user, content="Help me")]

    # Create a simple fake tool
    from chefchat.core.types import AvailableFunction
    tool = AvailableTool(
        function=AvailableFunction(
            name="test_tool",
            description="A test tool",
            parameters={"type": "object", "properties": {}}
        )
    )

    endpoint, _, body = adapter.prepare_request(
        model_name="gpt-4o-mini",
        messages=messages,
        temperature=0.7,
        tools=[tool],
        max_tokens=1000,
        tool_choice=None,
        enable_streaming=True,
        provider=provider,
        api_key="test-key"
    )

    import json
    payload = json.loads(body)
    assert "tools" in payload
    assert len(payload["tools"]) == 1
    assert payload["tools"][0]["type"] == "function"
    assert payload["tools"][0]["function"]["name"] == "test_tool"
    assert payload["max_tokens"] == 1000

def test_openai_adapter_parse_response():
    """Verify OpenAI adapter parses responses correctly."""
    adapter = OpenAIAdapter()

    # Mock response data from OpenAI
    fake_response = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "created": 1677652288,
        "model": "gpt-4o-mini",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": "Hello there!",
            },
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": 9,
            "completion_tokens": 12,
            "total_tokens": 21
        }
    }

    chunk = adapter.parse_response(fake_response)

    assert chunk.message.role == Role.assistant
    assert chunk.message.content == "Hello there!"
    assert chunk.finish_reason == "stop"
    assert chunk.usage.prompt_tokens == 9
    assert chunk.usage.completion_tokens == 12
