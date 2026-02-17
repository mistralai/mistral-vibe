from __future__ import annotations

import json

import pytest

from vibe.core.config import ProviderConfig
from vibe.core.llm.backend.anthropic import AnthropicAdapter, AnthropicMapper
from vibe.core.types import (
    AvailableFunction,
    AvailableTool,
    FunctionCall,
    LLMMessage,
    Role,
    ToolCall,
)


@pytest.fixture
def mapper():
    return AnthropicMapper()


@pytest.fixture
def adapter():
    return AnthropicAdapter()


@pytest.fixture
def provider():
    return ProviderConfig(
        name="anthropic",
        api_base="https://api.anthropic.com",
        api_key_env_var="ANTHROPIC_API_KEY",
        api_style="anthropic",
    )


class TestMapperPrepareMessages:
    def test_system_extracted(self, mapper):
        messages = [
            LLMMessage(role=Role.system, content="You are helpful."),
            LLMMessage(role=Role.user, content="Hi"),
        ]
        system, converted = mapper.prepare_messages(messages)
        assert system == "You are helpful."
        assert len(converted) == 1
        assert converted[0]["role"] == "user"

    def test_user_message(self, mapper):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        _, converted = mapper.prepare_messages(messages)
        assert converted[0]["content"] == [{"type": "text", "text": "Hello"}]

    def test_assistant_text(self, mapper):
        messages = [LLMMessage(role=Role.assistant, content="Sure")]
        _, converted = mapper.prepare_messages(messages)
        assert converted[0]["role"] == "assistant"
        content = converted[0]["content"]
        assert any(b.get("type") == "text" and b.get("text") == "Sure" for b in content)

    def test_assistant_with_reasoning_content_and_signature(self, mapper):
        messages = [
            LLMMessage(
                role=Role.assistant,
                content="Answer",
                reasoning_content="hmm",
                reasoning_signature="sig",
            )
        ]
        _, converted = mapper.prepare_messages(messages)
        content = converted[0]["content"]
        assert content[0] == {"type": "thinking", "thinking": "hmm", "signature": "sig"}
        assert content[1]["type"] == "text"

    def test_assistant_with_reasoning_content(self, mapper):
        messages = [
            LLMMessage(
                role=Role.assistant, content="Answer", reasoning_content="thinking..."
            )
        ]
        _, converted = mapper.prepare_messages(messages)
        content = converted[0]["content"]
        assert content[0] == {"type": "thinking", "thinking": "thinking..."}

    def test_assistant_with_tool_calls(self, mapper):
        messages = [
            LLMMessage(
                role=Role.assistant,
                content="Let me search.",
                tool_calls=[
                    ToolCall(
                        id="tc_1",
                        index=0,
                        function=FunctionCall(name="search", arguments='{"q": "test"}'),
                    )
                ],
            )
        ]
        _, converted = mapper.prepare_messages(messages)
        content = converted[0]["content"]
        tool_block = [b for b in content if b["type"] == "tool_use"][0]
        assert tool_block["name"] == "search"
        assert tool_block["input"] == {"q": "test"}

    def test_tool_result_appended_to_user(self, mapper):
        messages = [
            LLMMessage(role=Role.user, content="Do it"),
            LLMMessage(role=Role.tool, content="result", tool_call_id="tc_1"),
        ]
        _, converted = mapper.prepare_messages(messages)
        # tool_result is merged into the preceding user message
        assert len(converted) == 1
        assert converted[0]["role"] == "user"
        blocks = converted[0]["content"]
        assert any(b.get("type") == "tool_result" for b in blocks)

    def test_tool_result_new_user_when_no_prior(self, mapper):
        messages = [LLMMessage(role=Role.tool, content="result", tool_call_id="tc_1")]
        _, converted = mapper.prepare_messages(messages)
        assert converted[0]["role"] == "user"
        assert converted[0]["content"][0]["type"] == "tool_result"


class TestMapperPrepareTools:
    def test_none_returns_none(self, mapper):
        assert mapper.prepare_tools(None) is None

    def test_empty_returns_none(self, mapper):
        assert mapper.prepare_tools([]) is None

    def test_converts_tools(self, mapper):
        tools = [
            AvailableTool(
                function=AvailableFunction(
                    name="search",
                    description="Search things",
                    parameters={"type": "object"},
                )
            )
        ]
        result = mapper.prepare_tools(tools)
        assert len(result) == 1
        assert result[0]["name"] == "search"
        assert result[0]["input_schema"] == {"type": "object"}


class TestMapperToolChoice:
    def test_none(self, mapper):
        assert mapper.prepare_tool_choice(None) is None

    def test_auto(self, mapper):
        assert mapper.prepare_tool_choice("auto") == {"type": "auto"}

    def test_none_str(self, mapper):
        assert mapper.prepare_tool_choice("none") == {"type": "none"}

    def test_any(self, mapper):
        assert mapper.prepare_tool_choice("any") == {"type": "any"}

    def test_required(self, mapper):
        assert mapper.prepare_tool_choice("required") == {"type": "any"}

    def test_specific_tool(self, mapper):
        tool = AvailableTool(
            function=AvailableFunction(name="search", description="", parameters={})
        )
        assert mapper.prepare_tool_choice(tool) == {"type": "tool", "name": "search"}


class TestMapperParseResponse:
    def test_text(self, mapper):
        data = {
            "content": [{"type": "text", "text": "Hello"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        chunk = mapper.parse_response(data)
        assert chunk.message.content == "Hello"
        assert chunk.usage.prompt_tokens == 10

    def test_thinking(self, mapper):
        data = {
            "content": [
                {"type": "thinking", "thinking": "hmm", "signature": "sig"},
                {"type": "text", "text": "Answer"},
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        chunk = mapper.parse_response(data)
        assert chunk.message.content == "Answer"
        assert chunk.message.reasoning_content == "hmm"
        assert chunk.message.reasoning_signature == "sig"

    def test_redacted_thinking(self, mapper):
        data = {
            "content": [
                {"type": "redacted_thinking", "data": "xyz"},
                {"type": "text", "text": "Answer"},
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        chunk = mapper.parse_response(data)
        assert chunk.message.content == "Answer"
        assert chunk.message.reasoning_content is None

    def test_tool_use(self, mapper):
        data = {
            "content": [
                {"type": "tool_use", "id": "t1", "name": "search", "input": {"q": "hi"}}
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        chunk = mapper.parse_response(data)
        assert chunk.message.tool_calls[0].function.name == "search"
        assert json.loads(chunk.message.tool_calls[0].function.arguments) == {"q": "hi"}

    def test_cache_tokens(self, mapper):
        data = {
            "content": [{"type": "text", "text": "x"}],
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 5,
                "cache_read_input_tokens": 3,
                "output_tokens": 7,
            },
        }
        chunk = mapper.parse_response(data)
        assert chunk.usage.prompt_tokens == 18
        assert chunk.usage.completion_tokens == 7


class TestMapperStreamingEvents:
    def test_text_delta(self, mapper):
        chunk, idx = mapper.parse_streaming_event(
            "content_block_delta",
            {"delta": {"type": "text_delta", "text": "hi"}, "index": 0},
            0,
        )
        assert chunk.message.content == "hi"

    def test_thinking_delta(self, mapper):
        chunk, _ = mapper.parse_streaming_event(
            "content_block_delta",
            {"delta": {"type": "thinking_delta", "thinking": "hmm"}, "index": 0},
            0,
        )
        assert chunk.message.reasoning_content == "hmm"

    def test_tool_use_start(self, mapper):
        chunk, idx = mapper.parse_streaming_event(
            "content_block_start",
            {
                "content_block": {"type": "tool_use", "id": "t1", "name": "search"},
                "index": 2,
            },
            0,
        )
        assert chunk.message.tool_calls[0].id == "t1"
        assert idx == 2

    def test_input_json_delta(self, mapper):
        chunk, _ = mapper.parse_streaming_event(
            "content_block_delta",
            {
                "delta": {"type": "input_json_delta", "partial_json": '{"q":'},
                "index": 1,
            },
            0,
        )
        assert chunk.message.tool_calls[0].function.arguments == '{"q":'

    def test_message_start_usage(self, mapper):
        chunk, _ = mapper.parse_streaming_event(
            "message_start",
            {"message": {"usage": {"input_tokens": 50, "cache_read_input_tokens": 10}}},
            0,
        )
        assert chunk.usage.prompt_tokens == 60

    def test_message_delta_usage(self, mapper):
        chunk, _ = mapper.parse_streaming_event(
            "message_delta", {"usage": {"output_tokens": 42}}, 0
        )
        assert chunk.usage.completion_tokens == 42

    def test_unknown_event(self, mapper):
        chunk, idx = mapper.parse_streaming_event("ping", {}, 5)
        assert chunk is None
        assert idx == 5

    def test_signature_delta(self, mapper):
        chunk, _ = mapper.parse_streaming_event(
            "content_block_delta",
            {"delta": {"type": "signature_delta", "signature": "sig"}, "index": 0},
            0,
        )
        assert chunk is not None
        assert chunk.message.reasoning_signature == "sig"


class TestAdapterPrepareRequest:
    def test_basic(self, adapter, provider):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        req = adapter.prepare_request(
            model_name="claude-sonnet-4-20250514",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=1024,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
        )

        payload = json.loads(req.body)
        assert payload["model"] == "claude-sonnet-4-20250514"
        assert payload["max_tokens"] == 1024
        assert payload["temperature"] == 0.5
        assert req.endpoint == "/v1/messages"
        assert req.headers["anthropic-version"] == "2023-06-01"

    def test_beta_features(self, adapter, provider):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        req = adapter.prepare_request(
            model_name="claude-sonnet-4-20250514",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=1024,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
        )
        assert "prompt-caching-2024-07-31" in req.headers["anthropic-beta"]
        assert "interleaved-thinking-2025-05-14" in req.headers["anthropic-beta"]
        assert "fine-grained-tool-streaming-2025-05-14" in req.headers["anthropic-beta"]

    def test_api_key_header(self, adapter, provider):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        req = adapter.prepare_request(
            model_name="claude-sonnet-4-20250514",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=1024,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
            api_key="sk-test-key",
        )
        assert req.headers["x-api-key"] == "sk-test-key"

    def test_streaming(self, adapter, provider):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        req = adapter.prepare_request(
            model_name="claude-sonnet-4-20250514",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=1024,
            tool_choice=None,
            enable_streaming=True,
            provider=provider,
        )
        assert json.loads(req.body)["stream"] is True

    def test_default_max_tokens(self, adapter, provider):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        req = adapter.prepare_request(
            model_name="claude-sonnet-4-20250514",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=None,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
        )
        assert json.loads(req.body)["max_tokens"] == AnthropicAdapter.DEFAULT_MAX_TOKENS

    def test_with_thinking(self, adapter, provider):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        req = adapter.prepare_request(
            model_name="claude-sonnet-4-20250514",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=1024,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
            thinking="medium",
        )
        payload = json.loads(req.body)
        assert payload["thinking"] == {"type": "enabled", "budget_tokens": 10000}
        assert payload["max_tokens"] == 1024
        assert payload["temperature"] == 1

    def test_system_cached(self, adapter, provider):
        messages = [
            LLMMessage(role=Role.system, content="Be helpful."),
            LLMMessage(role=Role.user, content="Hello"),
        ]
        req = adapter.prepare_request(
            model_name="claude-sonnet-4-20250514",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=1024,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
        )
        payload = json.loads(req.body)
        assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}

    def test_with_tools(self, adapter, provider):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        tools = [
            AvailableTool(
                function=AvailableFunction(
                    name="test_tool",
                    description="A test tool",
                    parameters={"type": "object", "properties": {}},
                )
            )
        ]
        req = adapter.prepare_request(
            model_name="claude-sonnet-4-20250514",
            messages=messages,
            temperature=0.5,
            tools=tools,
            max_tokens=1024,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
        )
        payload = json.loads(req.body)
        assert len(payload["tools"]) == 1
        assert payload["tools"][0]["name"] == "test_tool"

    @pytest.mark.parametrize(
        "level,expected_budget", [("low", 1024), ("medium", 10_000), ("high", 32_000)]
    )
    def test_thinking_levels_budget_model(
        self, adapter, provider, level, expected_budget
    ):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        req = adapter.prepare_request(
            model_name="claude-sonnet-4-20250514",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=None,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
            thinking=level,
        )
        payload = json.loads(req.body)
        assert payload["thinking"] == {
            "type": "enabled",
            "budget_tokens": expected_budget,
        }
        assert payload["temperature"] == 1
        assert payload["max_tokens"] == expected_budget + 8192

    @pytest.mark.parametrize("level", ["low", "medium", "high"])
    def test_thinking_levels_adaptive_model(self, adapter, provider, level):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        req = adapter.prepare_request(
            model_name="claude-opus-4-6-20260101",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=None,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
            thinking=level,
        )
        payload = json.loads(req.body)
        assert payload["thinking"] == {"type": "adaptive"}
        assert payload["output_config"] == {"effort": level}
        assert payload["temperature"] == 1
        assert payload["max_tokens"] == 32_768

    def test_history_forced_thinking_budget_model(self, adapter, provider):
        messages = [
            LLMMessage(role=Role.user, content="Hello"),
            LLMMessage(
                role=Role.assistant,
                content="Answer",
                reasoning_content="thinking...",
                reasoning_signature="sig",
            ),
            LLMMessage(role=Role.user, content="Follow up"),
        ]
        req = adapter.prepare_request(
            model_name="claude-sonnet-4-20250514",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=None,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
        )
        payload = json.loads(req.body)
        assert payload["thinking"] == {"type": "enabled", "budget_tokens": 10_000}
        assert payload["temperature"] == 1
        assert payload["max_tokens"] == 18_192

    def test_history_forced_thinking_adaptive_model(self, adapter, provider):
        messages = [
            LLMMessage(role=Role.user, content="Hello"),
            LLMMessage(
                role=Role.assistant,
                content="Answer",
                reasoning_content="thinking...",
                reasoning_signature="sig",
            ),
            LLMMessage(role=Role.user, content="Follow up"),
        ]
        req = adapter.prepare_request(
            model_name="claude-opus-4-6-20260101",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=None,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
        )
        payload = json.loads(req.body)
        assert payload["thinking"] == {"type": "adaptive"}
        assert payload["output_config"] == {"effort": "medium"}
        assert payload["max_tokens"] == 32_768


class TestAdapterParseResponse:
    def test_non_streaming(self, adapter, provider):
        data = {
            "content": [{"type": "text", "text": "Hello!"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Hello!"
        assert chunk.usage.prompt_tokens == 10

    def test_streaming_text_delta(self, adapter, provider):
        data = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hi"},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Hi"

    def test_streaming_message_start(self, adapter, provider):
        data = {"type": "message_start", "message": {"usage": {"input_tokens": 100}}}
        chunk = adapter.parse_response(data, provider)
        assert chunk.usage.prompt_tokens == 100

    def test_streaming_unknown_returns_empty(self, adapter, provider):
        data = {"type": "ping"}
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.role == Role.assistant
        assert chunk.message.content is None

    def test_cache_control_last_user_message(self, adapter):
        messages = [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]
        adapter._add_cache_control_to_last_user_message(messages)
        assert messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_cache_control_skips_non_user(self, adapter):
        messages = [
            {"role": "assistant", "content": [{"type": "text", "text": "Hello"}]}
        ]
        adapter._add_cache_control_to_last_user_message(messages)
        assert "cache_control" not in messages[0]["content"][0]

    def test_cache_control_empty(self, adapter):
        messages: list[dict] = []
        adapter._add_cache_control_to_last_user_message(messages)
        assert messages == []
