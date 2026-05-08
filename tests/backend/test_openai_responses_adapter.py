"""Tests for the OpenAI Responses API adapter.

Tests cover:
- Request preparation (payload structure, message conversion, tool conversion)
- Non-streaming response parsing
- Streaming event parsing
- Integration with GenericBackend via respx mocks
"""

from __future__ import annotations

import json

import httpx
from pydantic import ValidationError
import pytest
import respx

from tests.backend.data import Chunk, JsonResponse, ResultData, Url
from tests.backend.data.openai_responses import (
    COMMENTARY_CONVERSATION_PARAMS,
    OPENAI_RESPONSES_TEST_BASE_URL,
    SIMPLE_CONVERSATION_PARAMS,
    STREAMED_COMMENTARY_CONVERSATION_PARAMS,
    STREAMED_SIMPLE_CONVERSATION_PARAMS,
    STREAMED_TOOL_CONVERSATION_PARAMS,
    TOOL_CONVERSATION_PARAMS,
)
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.llm.backend.generic import GenericBackend
from vibe.core.llm.backend.openai_responses import OpenAIResponsesAdapter
from vibe.core.types import (
    AvailableFunction,
    AvailableTool,
    FunctionCall,
    LLMChunk,
    LLMMessage,
    Role,
    ToolCall,
)


@pytest.fixture
def adapter():
    return OpenAIResponsesAdapter()


@pytest.fixture
def provider():
    return _make_provider()


@pytest.fixture
def model():
    return _make_model()


def _make_provider(base_url: Url = OPENAI_RESPONSES_TEST_BASE_URL) -> ProviderConfig:
    return ProviderConfig(
        name="openai",
        api_base=f"{base_url}/v1",
        api_key_env_var="OPENAI_API_KEY",
        api_style="openai-responses",
    )


def _make_model() -> ModelConfig:
    return ModelConfig(name="gpt-4o", provider="openai", alias="gpt-4o")


def _make_backend(base_url: Url = OPENAI_RESPONSES_TEST_BASE_URL) -> GenericBackend:
    return GenericBackend(provider=_make_provider(base_url))


def _prepare(adapter, provider, messages, **kwargs):
    defaults = dict(
        model_name="gpt-4o",
        messages=messages,
        temperature=0.2,
        tools=None,
        max_tokens=None,
        tool_choice=None,
        enable_streaming=False,
        provider=provider,
    )
    defaults.update(kwargs)
    return json.loads(adapter.prepare_request(**defaults).body)


def _assert_chunk_matches(result: LLMChunk, expected_result: ResultData) -> None:
    assert result.message.content == expected_result["message"]
    assert result.message.reasoning_content == expected_result.get("reasoning_content")
    assert result.usage is not None
    assert result.usage.prompt_tokens == expected_result["usage"]["prompt_tokens"]
    assert (
        result.usage.completion_tokens == expected_result["usage"]["completion_tokens"]
    )

    expected_tool_calls = expected_result.get("tool_calls")
    if result.message.tool_calls is None:
        assert expected_tool_calls is None
        return

    assert expected_tool_calls is not None
    assert len(result.message.tool_calls) == len(expected_tool_calls)
    for tool_call, expected_tool_call in zip(
        result.message.tool_calls, expected_tool_calls, strict=True
    ):
        assert tool_call.function.name == expected_tool_call["name"]
        assert tool_call.function.arguments == expected_tool_call["arguments"]
        assert tool_call.index == expected_tool_call["index"]


class TestPrepareRequest:
    def test_endpoint(self, adapter):
        assert adapter.endpoint == "/responses"

    def test_simple_message(self, adapter, provider):
        payload = _prepare(
            adapter, provider, [LLMMessage(role=Role.user, content="Hello")]
        )
        assert payload["model"] == "gpt-4o"
        assert payload["input"] == [{"role": "user", "content": "Hello"}]
        assert "instructions" not in payload
        assert payload["store"] is False

    def test_system_message_becomes_system_input_item(self, adapter, provider):
        payload = _prepare(
            adapter,
            provider,
            [
                LLMMessage(role=Role.system, content="You are helpful."),
                LLMMessage(role=Role.user, content="Hi"),
            ],
        )
        assert "instructions" not in payload
        assert payload["input"] == [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]

    def test_consecutive_user_messages_are_merged(self, adapter, provider):
        payload = _prepare(
            adapter,
            provider,
            [
                LLMMessage(role=Role.user, content="Hi"),
                LLMMessage(role=Role.user, content="Again"),
            ],
        )
        assert payload["input"] == [{"role": "user", "content": "Hi\n\nAgain"}]

    def test_multiple_system_messages_are_preserved(self, adapter, provider):
        payload = _prepare(
            adapter,
            provider,
            [
                LLMMessage(role=Role.system, content="Rule 1."),
                LLMMessage(role=Role.system, content="Rule 2."),
                LLMMessage(role=Role.user, content="Hi"),
            ],
        )
        assert "instructions" not in payload
        assert payload["input"] == [
            {"role": "system", "content": "Rule 1."},
            {"role": "system", "content": "Rule 2."},
            {"role": "user", "content": "Hi"},
        ]

    def test_tool_message_becomes_function_call_output(self, adapter, provider):
        payload = _prepare(
            adapter,
            provider,
            [
                LLMMessage(role=Role.user, content="Hi"),
                LLMMessage(
                    role=Role.tool, content='{"result": 42}', tool_call_id="call_123"
                ),
            ],
        )
        tool_output = payload["input"][1]
        assert tool_output["type"] == "function_call_output"
        assert tool_output["call_id"] == "call_123"
        assert tool_output["output"] == '{"result": 42}'

    def test_assistant_tool_calls_become_function_call_items(self, adapter, provider):
        payload = _prepare(
            adapter,
            provider,
            [
                LLMMessage(role=Role.user, content="What's the weather?"),
                LLMMessage(
                    role=Role.assistant,
                    content="",
                    tool_calls=[
                        ToolCall(
                            id="call_abc",
                            function=FunctionCall(
                                name="get_weather", arguments='{"location": "Paris"}'
                            ),
                        )
                    ],
                ),
                LLMMessage(
                    role=Role.tool, content='{"temp": 20}', tool_call_id="call_abc"
                ),
            ],
        )
        # input[0] = user, input[1] = assistant message, input[2] = function_call,
        # input[3] = function_call_output
        assert len(payload["input"]) == 4
        fc = payload["input"][2]
        assert fc["type"] == "function_call"
        assert fc["call_id"] == "call_abc"
        assert fc["name"] == "get_weather"
        assert fc["arguments"] == '{"location": "Paris"}'
        fco = payload["input"][3]
        assert fco["type"] == "function_call_output"
        assert fco["call_id"] == "call_abc"

    def test_assistant_reasoning_state_becomes_reasoning_input_items(
        self, adapter, provider
    ):
        payload = _prepare(
            adapter,
            provider,
            [
                LLMMessage(
                    role=Role.assistant,
                    content="Answer",
                    reasoning_state=["enc:abc", "enc:def"],
                )
            ],
        )

        assert payload["input"] == [
            {"type": "reasoning", "encrypted_content": "enc:abc"},
            {"type": "reasoning", "encrypted_content": "enc:def"},
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Answer"}],
            },
        ]

    def test_tools_converted_to_flat_format(self, adapter, provider):
        tools = [
            AvailableTool(
                function=AvailableFunction(
                    name="get_weather",
                    description="Get the weather",
                    parameters={
                        "type": "object",
                        "properties": {"location": {"type": "string"}},
                        "required": ["location"],
                    },
                )
            )
        ]
        payload = _prepare(
            adapter, provider, [LLMMessage(role=Role.user, content="Hi")], tools=tools
        )
        assert len(payload["tools"]) == 1
        tool = payload["tools"][0]
        # Responses API uses flat format (no nested "function" key)
        assert tool["type"] == "function"
        assert tool["name"] == "get_weather"
        assert tool["description"] == "Get the weather"
        assert "function" not in tool

    def test_max_tokens_becomes_max_output_tokens(self, adapter, provider):
        payload = _prepare(
            adapter,
            provider,
            [LLMMessage(role=Role.user, content="Hi")],
            max_tokens=100,
        )
        assert payload["max_output_tokens"] == 100
        assert "max_tokens" not in payload

    def test_temperature_is_preserved_for_supported_models(self, adapter, provider):
        payload = _prepare(
            adapter,
            provider,
            [LLMMessage(role=Role.user, content="Hi")],
            model_name="gpt-4o",
            temperature=0.7,
        )
        assert payload["temperature"] == 0.7

    def test_temperature_is_omitted_for_reasoning_models(self, adapter, provider):
        payload = _prepare(
            adapter,
            provider,
            [LLMMessage(role=Role.user, content="Hi")],
            model_name="gpt-5.4",
            temperature=0.7,
        )
        assert "temperature" not in payload

    def test_streaming_flag(self, adapter, provider):
        payload = _prepare(
            adapter,
            provider,
            [LLMMessage(role=Role.user, content="Hi")],
            enable_streaming=True,
        )
        assert payload["stream"] is True

    def test_no_stream_by_default(self, adapter, provider):
        payload = _prepare(
            adapter, provider, [LLMMessage(role=Role.user, content="Hi")]
        )
        assert "stream" not in payload

    def test_tool_choice_string(self, adapter, provider):
        tool = AvailableTool(
            function=AvailableFunction(
                name="search",
                description="Search",
                parameters={"type": "object", "properties": {}},
            )
        )
        payload = _prepare(
            adapter,
            provider,
            [LLMMessage(role=Role.user, content="Hi")],
            tools=[tool],
            tool_choice="auto",
        )
        assert payload["tool_choice"] == "auto"

    def test_tool_choice_is_omitted_without_tools(self, adapter, provider):
        payload = _prepare(
            adapter,
            provider,
            [LLMMessage(role=Role.user, content="Hi")],
            tool_choice="auto",
        )
        assert "tool_choice" not in payload

    def test_tool_choice_specific(self, adapter, provider):
        tool = AvailableTool(
            function=AvailableFunction(
                name="search",
                description="Search",
                parameters={"type": "object", "properties": {}},
            )
        )
        payload = _prepare(
            adapter,
            provider,
            [LLMMessage(role=Role.user, content="Hi")],
            tools=[tool],
            tool_choice=tool,
        )
        assert payload["tool_choice"] == {"type": "function", "name": "search"}

    @pytest.mark.parametrize(
        ("thinking", "expected_effort"),
        [
            ("off", "none"),
            ("low", "low"),
            ("medium", "medium"),
            ("high", "high"),
            ("max", "xhigh"),
        ],
    )
    def test_thinking_sets_reasoning_effort(
        self, adapter, provider, thinking, expected_effort
    ):
        payload = _prepare(
            adapter,
            provider,
            [LLMMessage(role=Role.user, content="Hi")],
            thinking=thinking,
        )
        assert payload["reasoning"] == {"effort": expected_effort}

    def test_non_leading_system_message_is_preserved(self, adapter, provider):
        payload = _prepare(
            adapter,
            provider,
            [
                LLMMessage(role=Role.user, content="Hi"),
                LLMMessage(role=Role.system, content="Later system prompt"),
            ],
        )
        assert payload["input"] == [
            {"role": "user", "content": "Hi"},
            {"role": "system", "content": "Later system prompt"},
        ]

    def test_build_headers_with_api_key(self, adapter):
        headers = adapter.build_headers("secret")
        assert headers == {
            "Content-Type": "application/json",
            "Authorization": "Bearer secret",
        }


class TestParseNonStreamingResponse:
    def test_simple_text_response(self, adapter, provider):
        data = {
            "id": "resp_123",
            "object": "response",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Hello!"}],
                    "role": "assistant",
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Hello!"
        assert chunk.message.role == Role.assistant
        assert chunk.usage.prompt_tokens == 10
        assert chunk.usage.completion_tokens == 5

    def test_function_call_response(self, adapter, provider):
        data = {
            "id": "resp_456",
            "object": "response",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call_789",
                    "name": "get_weather",
                    "arguments": '{"location": "Paris"}',
                }
            ],
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.tool_calls is not None
        assert len(chunk.message.tool_calls) == 1
        tc = chunk.message.tool_calls[0]
        assert tc.id == "call_789"
        assert tc.index == 0
        assert tc.function.name == "get_weather"
        assert tc.function.arguments == '{"location": "Paris"}'

    def test_function_call_response_uses_id_when_call_id_missing(
        self, adapter, provider
    ):
        data = {
            "id": "resp_456",
            "object": "response",
            "output": [
                {
                    "type": "function_call",
                    "id": "fc_789",
                    "name": "get_weather",
                    "arguments": '{"location": "Paris"}',
                }
            ],
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.tool_calls is not None
        tc = chunk.message.tool_calls[0]
        assert tc.id == "fc_789"
        assert tc.index == 0
        assert tc.function.name == "get_weather"
        assert tc.function.arguments == '{"location": "Paris"}'

    def test_commentary_phase_becomes_reasoning_content(self, adapter, provider):
        data = {
            "id": "resp_thinking",
            "object": "response",
            "output": [
                {
                    "type": "message",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "Let me think..."}],
                    "role": "assistant",
                },
                {
                    "type": "message",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "Hello!"}],
                    "role": "assistant",
                },
            ],
            "usage": {"input_tokens": 50, "output_tokens": 30},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Hello!"
        assert chunk.message.reasoning_content == "Let me think..."

    def test_invalid_non_streaming_response_schema_raises(self, adapter, provider):
        data = {"id": "resp_invalid", "object": "response", "output": "not-a-list"}

        with pytest.raises(ValidationError):
            adapter.parse_response(data, provider)

    def test_invalid_message_item_content_schema_raises(self, adapter, provider):
        data = {
            "id": "resp_invalid",
            "object": "response",
            "output": [
                {"type": "message", "role": "assistant", "content": "not-a-list"}
            ],
        }

        with pytest.raises(ValidationError):
            adapter.parse_response(data, provider)

    def test_commentary_summary_blocks_become_reasoning_content(
        self, adapter, provider
    ):
        data = {
            "id": "resp_thinking",
            "object": "response",
            "output": [
                {
                    "type": "message",
                    "phase": "commentary",
                    "content": [
                        {"type": "summary_text", "text": "Need more context."},
                        {"type": "reasoning_summary_text", "text": " Compare options."},
                    ],
                    "role": "assistant",
                },
                {
                    "type": "message",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "Done."}],
                    "role": "assistant",
                },
            ],
            "usage": {"input_tokens": 50, "output_tokens": 30},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Done."
        assert chunk.message.reasoning_content == "Need more context. Compare options."

    def test_commentary_mixed_blocks_do_not_leak_into_assistant_content(
        self, adapter, provider
    ):
        data = {
            "id": "resp_thinking",
            "object": "response",
            "output": [
                {
                    "type": "message",
                    "phase": "commentary",
                    "content": [
                        {"type": "output_text", "text": "Let me think."},
                        {"type": "summary_text", "text": " Need more context."},
                        {"type": "reasoning_summary_text", "text": " Compare options."},
                    ],
                    "role": "assistant",
                },
                {
                    "type": "message",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "Done."}],
                    "role": "assistant",
                },
            ],
            "usage": {"input_tokens": 50, "output_tokens": 30},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Done."
        assert (
            chunk.message.reasoning_content
            == "Let me think. Need more context. Compare options."
        )

    def test_reasoning_summary_preserved_without_exposing_encrypted_content(
        self, adapter, provider
    ):
        data = {
            "id": "resp_reasoning",
            "object": "response",
            "output": [
                {
                    "type": "reasoning",
                    "encrypted_content": "enc:abc",
                    "summary": [
                        {"type": "summary_text", "text": "Need to compare options."}
                    ],
                },
                {
                    "type": "message",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "Done."}],
                    "role": "assistant",
                },
            ],
            "usage": {"input_tokens": 50, "output_tokens": 30},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Done."
        assert chunk.message.reasoning_content == "Need to compare options."
        assert chunk.message.reasoning_state == ["enc:abc"]

    def test_invalid_reasoning_item_schema_raises(self, adapter, provider):
        data = {
            "id": "resp_invalid",
            "object": "response",
            "output": [
                {
                    "type": "reasoning",
                    "encrypted_content": "enc:abc",
                    "summary": "not-a-list",
                }
            ],
        }

        with pytest.raises(ValidationError):
            adapter.parse_response(data, provider)

    def test_mixed_message_and_function_call(self, adapter, provider):
        data = {
            "id": "resp_mixed",
            "object": "response",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Let me check."}],
                    "role": "assistant",
                },
                {
                    "type": "function_call",
                    "call_id": "call_abc",
                    "name": "search",
                    "arguments": '{"q": "test"}',
                },
            ],
            "usage": {"input_tokens": 15, "output_tokens": 8},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Let me check."
        assert chunk.message.tool_calls is not None
        assert chunk.message.tool_calls[0].index == 1
        assert chunk.message.tool_calls[0].function.name == "search"


class TestParseStreamingEvents:
    def test_text_delta(self, adapter, provider):
        data = {
            "type": "response.output_text.delta",
            "output_index": 0,
            "content_index": 0,
            "delta": "Hello",
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Hello"

    def test_function_call_args_delta(self, adapter, provider):
        data = {
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "call_id": "call_123",
            "delta": '{"loc',
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == ""
        assert chunk.message.tool_calls is None

    def test_function_call_args_delta_requires_output_index(self, adapter, provider):
        with pytest.raises(ValueError, match="Tool call chunk missing index"):
            adapter.parse_response(
                {
                    "type": "response.function_call_arguments.delta",
                    "call_id": "call_123",
                    "delta": '{"loc',
                },
                provider,
            )

    def test_function_call_args_empty_delta_without_metadata_returns_empty_chunk(
        self, adapter, provider
    ):
        chunk = adapter.parse_response(
            {"type": "response.function_call_arguments.delta", "delta": ""}, provider
        )
        assert chunk.message.content == ""
        assert chunk.message.tool_calls is None

    def test_function_call_args_done_emits_missing_tool_call_data(
        self, adapter, provider
    ):
        data = {
            "type": "response.function_call_arguments.done",
            "output_index": 0,
            "call_id": "call_123",
            "name": "search",
            "arguments": '{"q": "test"}',
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.tool_calls is not None
        tool_call = chunk.message.tool_calls[0]
        assert tool_call.id == "call_123"
        assert tool_call.index == 0
        assert tool_call.function.name == "search"
        assert tool_call.function.arguments == '{"q": "test"}'

    def test_function_call_args_done_after_deltas_emits_full_arguments(
        self, adapter, provider
    ):
        adapter.parse_response(
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "call_id": "call_123",
                "name": "search",
                "delta": '{"q": "test"}',
            },
            provider,
        )

        chunk = adapter.parse_response(
            {
                "type": "response.function_call_arguments.done",
                "output_index": 0,
                "call_id": "call_123",
                "name": "search",
                "arguments": '{"q": "test"}',
            },
            provider,
        )
        assert chunk.message.tool_calls is not None
        tool_call = chunk.message.tool_calls[0]
        assert tool_call.id == "call_123"
        assert tool_call.index == 0
        assert tool_call.function.name == "search"
        assert tool_call.function.arguments == '{"q": "test"}'

    def test_function_call_args_done_after_partial_item_snapshot_emits_full_arguments(
        self, adapter, provider
    ):
        added_chunk = adapter.parse_response(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "call_id": "call_123",
                    "name": "search",
                    "arguments": '{"q": "te',
                },
            },
            provider,
        )
        assert added_chunk.message.tool_calls is not None
        assert added_chunk.message.tool_calls[0].function.arguments == ""

        adapter.parse_response(
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "call_id": "call_123",
                "name": "search",
                "delta": 'st"}',
            },
            provider,
        )

        chunk = adapter.parse_response(
            {
                "type": "response.function_call_arguments.done",
                "output_index": 0,
                "call_id": "call_123",
                "name": "search",
                "arguments": '{"q": "test"}',
            },
            provider,
        )
        assert chunk.message.tool_calls is not None
        tool_call = chunk.message.tool_calls[0]
        assert tool_call.id == "call_123"
        assert tool_call.index == 0
        assert tool_call.function.name == "search"
        assert tool_call.function.arguments == '{"q": "test"}'

    def test_function_call_args_done_uses_full_arguments_on_mismatch(
        self, adapter, provider, caplog
    ):
        adapter.parse_response(
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "call_id": "call_123",
                "name": "search",
                "delta": '{"q":"test"}',
            },
            provider,
        )

        with caplog.at_level("WARNING"):
            chunk = adapter.parse_response(
                {
                    "type": "response.function_call_arguments.done",
                    "output_index": 0,
                    "call_id": "call_123",
                    "name": "search",
                    "arguments": '{"q": "test"}',
                },
                provider,
            )

        assert "tool call arguments mismatch" in caplog.text
        assert chunk.message.tool_calls is not None
        assert chunk.message.tool_calls[0].function.arguments == '{"q": "test"}'

    def test_output_item_added_function_call(self, adapter, provider):
        data = {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "type": "function_call",
                "call_id": "call_456",
                "name": "bash",
                "arguments": "",
            },
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.tool_calls is not None
        assert chunk.message.tool_calls[0].id == "call_456"
        assert chunk.message.tool_calls[0].function.name == "bash"

    def test_output_item_added_invalid_function_call_item_schema_raises(
        self, adapter, provider
    ):
        with pytest.raises(ValidationError):
            adapter.parse_response(
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "type": "function_call",
                        "call_id": "call_456",
                        "name": "bash",
                        "arguments": {},
                    },
                },
                provider,
            )

    def test_output_item_added_function_call_requires_output_index(
        self, adapter, provider
    ):
        with pytest.raises(ValueError, match="Tool call chunk missing index"):
            adapter.parse_response(
                {
                    "type": "response.output_item.added",
                    "item": {
                        "type": "function_call",
                        "call_id": "call_456",
                        "name": "bash",
                        "arguments": "",
                    },
                },
                provider,
            )

    def test_output_item_added_message(self, adapter, provider):
        data = {
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {"type": "message", "role": "assistant"},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == ""
        assert chunk.message.tool_calls is None

    def test_output_item_done_function_call_emits_missing_arguments(
        self, adapter, provider
    ):
        adapter.parse_response(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "call_id": "call_456",
                    "name": "bash",
                    "arguments": "",
                },
            },
            provider,
        )

        chunk = adapter.parse_response(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "call_id": "call_456",
                    "name": "bash",
                    "arguments": '{"cmd": "pwd"}',
                },
            },
            provider,
        )
        assert chunk.message.tool_calls is not None
        tool_call = chunk.message.tool_calls[0]
        assert tool_call.id == "call_456"
        assert tool_call.index == 0
        assert tool_call.function.name == "bash"
        assert tool_call.function.arguments == '{"cmd": "pwd"}'

    def test_output_item_done_after_buffered_arguments_emits_full_arguments(
        self, adapter, provider
    ):
        adapter.parse_response(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "call_id": "call_456",
                    "name": "bash",
                    "arguments": "",
                },
            },
            provider,
        )
        adapter.parse_response(
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "call_id": "call_456",
                "name": "bash",
                "delta": '{"cmd": "pwd"}',
            },
            provider,
        )

        chunk = adapter.parse_response(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "call_id": "call_456",
                    "name": "bash",
                    "arguments": '{"cmd": "pwd"}',
                },
            },
            provider,
        )
        assert chunk.message.tool_calls is not None
        tool_call = chunk.message.tool_calls[0]
        assert tool_call.id == "call_456"
        assert tool_call.index == 0
        assert tool_call.function.name == "bash"
        assert tool_call.function.arguments == '{"cmd": "pwd"}'

    def test_output_item_done_after_partial_item_snapshot_emits_full_arguments(
        self, adapter, provider
    ):
        added_chunk = adapter.parse_response(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "call_id": "call_456",
                    "name": "bash",
                    "arguments": '{"cmd": "p',
                },
            },
            provider,
        )
        assert added_chunk.message.tool_calls is not None
        assert added_chunk.message.tool_calls[0].function.arguments == ""

        adapter.parse_response(
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "call_id": "call_456",
                "name": "bash",
                "delta": 'wd"}',
            },
            provider,
        )

        chunk = adapter.parse_response(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "call_id": "call_456",
                    "name": "bash",
                    "arguments": '{"cmd": "pwd"}',
                },
            },
            provider,
        )
        assert chunk.message.tool_calls is not None
        tool_call = chunk.message.tool_calls[0]
        assert tool_call.id == "call_456"
        assert tool_call.index == 0
        assert tool_call.function.name == "bash"
        assert tool_call.function.arguments == '{"cmd": "pwd"}'

    def test_output_item_done_after_done_emits_no_duplicate_args(
        self, adapter, provider
    ):
        adapter.parse_response(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "call_id": "call_456",
                    "name": "bash",
                    "arguments": "",
                },
            },
            provider,
        )
        adapter.parse_response(
            {
                "type": "response.function_call_arguments.done",
                "output_index": 0,
                "call_id": "call_456",
                "name": "bash",
                "arguments": '{"cmd": "pwd"}',
            },
            provider,
        )

        chunk = adapter.parse_response(
            {
                "type": "response.output_item.done",
                "output_index": 0,
                "item": {
                    "type": "function_call",
                    "call_id": "call_456",
                    "name": "bash",
                    "arguments": '{"cmd": "pwd"}',
                },
            },
            provider,
        )
        assert chunk.message.content == ""
        assert chunk.message.tool_calls is None

    def test_response_completed(self, adapter, provider):
        data = {
            "type": "response.completed",
            "response": {
                "id": "resp_123",
                "output": [
                    {
                        "type": "reasoning",
                        "encrypted_content": "enc:streamed",
                        "summary": [],
                    },
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Done!"}],
                        "role": "assistant",
                    },
                ],
                "usage": {"input_tokens": 50, "output_tokens": 25},
            },
        }
        chunk = adapter.parse_response(data, provider)
        # Streaming completed event only carries usage; content was already
        # delivered via delta events, so message should be empty.
        assert chunk.message.content == ""
        assert chunk.message.reasoning_state == ["enc:streamed"]
        assert chunk.usage.prompt_tokens == 50
        assert chunk.usage.completion_tokens == 25

    def test_response_incomplete_uses_terminal_usage(self, adapter, provider):
        data = {
            "type": "response.incomplete",
            "response": {
                "id": "resp_123",
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "usage": {"input_tokens": 50, "output_tokens": 25},
            },
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == ""
        assert chunk.usage.prompt_tokens == 50
        assert chunk.usage.completion_tokens == 25

    def test_commentary_deltas_become_reasoning_content(self, adapter, provider):
        adapter.parse_response(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"type": "message", "phase": "commentary", "role": "assistant"},
            },
            provider,
        )
        chunk = adapter.parse_response(
            {
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "delta": "Thinking...",
            },
            provider,
        )
        assert chunk.message.content == ""
        assert chunk.message.reasoning_content == "Thinking..."

        adapter.parse_response(
            {
                "type": "response.output_item.added",
                "output_index": 1,
                "item": {
                    "type": "message",
                    "phase": "final_answer",
                    "role": "assistant",
                },
            },
            provider,
        )
        chunk = adapter.parse_response(
            {
                "type": "response.output_text.delta",
                "output_index": 1,
                "content_index": 0,
                "delta": "Hello!",
            },
            provider,
        )
        assert chunk.message.content == "Hello!"
        assert chunk.message.reasoning_content is None

    def test_reasoning_summary_delta_emits_reasoning_content(self, adapter, provider):
        chunk = adapter.parse_response(
            {
                "type": "response.reasoning_summary_text.delta",
                "output_index": 0,
                "summary_index": 0,
                "delta": "Need more context.",
            },
            provider,
        )
        assert chunk.message.content == ""
        assert chunk.message.reasoning_content == "Need more context."

    def test_summary_text_delta_emits_reasoning_content(self, adapter, provider):
        chunk = adapter.parse_response(
            {
                "type": "response.summary_text.delta",
                "output_index": 0,
                "summary_index": 0,
                "delta": "Need more context.",
            },
            provider,
        )
        assert chunk.message.content == ""
        assert chunk.message.reasoning_content == "Need more context."

    def test_commentary_state_resets_on_new_stream(self, adapter, provider):
        # Register commentary index
        adapter.parse_response(
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"type": "message", "phase": "commentary", "role": "assistant"},
            },
            provider,
        )
        # New stream resets state
        adapter.parse_response(
            {
                "type": "response.created",
                "response": {"id": "resp_new", "output": [], "usage": None},
            },
            provider,
        )
        # Index 0 should no longer be suppressed
        chunk = adapter.parse_response(
            {
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "delta": "Fresh start",
            },
            provider,
        )
        assert chunk.message.content == "Fresh start"

    def test_unknown_event_returns_empty_chunk(self, adapter, provider):
        data = {"type": "response.content_part.added", "output_index": 0}
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == ""
        assert chunk.usage.prompt_tokens == 0

    def test_error_event_raises_runtime_error(self, adapter, provider):
        with pytest.raises(RuntimeError, match="OpenAI Responses stream error"):
            adapter.parse_response(
                {
                    "type": "error",
                    "error": {"type": "server_error", "message": "backend failed"},
                },
                provider,
            )

    def test_invalid_error_payload_schema_raises(self, adapter, provider):
        with pytest.raises(ValidationError):
            adapter.parse_response({"type": "error", "error": "not-a-dict"}, provider)


class TestGenericBackendIntegration:
    """Test OpenAIResponsesAdapter via GenericBackend + respx mocks."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "base_url,json_response,result_data",
        [
            *SIMPLE_CONVERSATION_PARAMS,
            *TOOL_CONVERSATION_PARAMS,
            *COMMENTARY_CONVERSATION_PARAMS,
        ],
    )
    async def test_complete(
        self, base_url: Url, json_response: JsonResponse, result_data: ResultData
    ):
        with respx.mock(base_url=base_url) as mock_api:
            mock_api.post("/v1/responses").mock(
                return_value=httpx.Response(status_code=200, json=json_response)
            )
            backend = _make_backend(base_url)
            model = _make_model()
            messages = [LLMMessage(role=Role.user, content="Just say hi")]

            result = await backend.complete(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            )

            _assert_chunk_matches(result, result_data)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "base_url,chunks,result_data",
        [
            *STREAMED_SIMPLE_CONVERSATION_PARAMS,
            *STREAMED_TOOL_CONVERSATION_PARAMS,
            *STREAMED_COMMENTARY_CONVERSATION_PARAMS,
        ],
    )
    async def test_complete_streaming(
        self, base_url: Url, chunks: list[Chunk], result_data: list[ResultData]
    ):
        with respx.mock(base_url=base_url) as mock_api:
            mock_api.post("/v1/responses").mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(stream=b"\n\n".join(chunks)),
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            backend = _make_backend(base_url)
            model = _make_model()
            messages = [LLMMessage(role=Role.user, content="Just say hi")]

            results: list[LLMChunk] = []
            async for result in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            ):
                results.append(result)

            for result, expected_result in zip(results, result_data, strict=True):
                _assert_chunk_matches(result, expected_result)

    @pytest.mark.asyncio
    async def test_streaming_payload_includes_stream_flag(self):
        base_url = OPENAI_RESPONSES_TEST_BASE_URL
        with respx.mock(base_url=base_url) as mock_api:
            route = mock_api.post("/v1/responses").mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(
                        b'data: {"type":"response.output_text.delta","output_index":0,"content_index":0,"delta":"hi"}\n\n'
                        b'data: {"type":"response.completed","response":{"id":"resp_1","output":[{"type":"message","content":[{"type":"output_text","text":"hi"}],"role":"assistant"}],"usage":{"input_tokens":10,"output_tokens":5}}}\n\n'
                        b"data: [DONE]\n\n"
                    ),
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            backend = _make_backend(base_url)
            model = _make_model()
            messages = [LLMMessage(role=Role.user, content="hi")]

            async for _ in backend.complete_streaming(
                model=model,
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            ):
                pass

            assert route.called
            request = route.calls.last.request
            payload = json.loads(request.content)
            assert payload["stream"] is True
            # Responses API does not use stream_options
            assert "stream_options" not in payload
