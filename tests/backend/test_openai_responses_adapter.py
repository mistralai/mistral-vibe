"""Tests for the OpenAI Responses API adapter.

Tests cover:
- Request preparation (payload structure, message conversion, tool conversion)
- Non-streaming response parsing
- Streaming event parsing
- Integration with GenericBackend via respx mocks
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from http import HTTPStatus
import json

import httpx
from pydantic import ValidationError
import pytest
import respx

from tests.backend.data import Chunk, JsonResponse, ResultData, Url
from tests.backend.data.openai_responses import (
    COMMENTARY_CONVERSATION_PARAMS,
    SIMPLE_CONVERSATION_PARAMS,
    STREAMED_COMMENTARY_CONVERSATION_PARAMS,
    STREAMED_SIMPLE_CONVERSATION_PARAMS,
    STREAMED_TOOL_CONVERSATION_PARAMS,
    TOOL_CONVERSATION_PARAMS,
)
from tests.constants import OPENAI_BASE_URL, OPENAI_RESPONSES_PATH
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.llm.backend.generic import GenericBackend
from vibe.core.llm.backend.openai_responses import (
    OpenAIResponsesAdapter,
    OpenAIResponsesStreamError,
)
from vibe.core.llm.exceptions import BackendError
from vibe.core.types import (
    AvailableFunction,
    AvailableTool,
    FunctionCall,
    LLMChunk,
    LLMMessage,
    Role,
    ToolCall,
)
from vibe.core.utils import RetryCategory, RetryReason


@pytest.fixture
def adapter():
    return OpenAIResponsesAdapter()


@pytest.fixture
def provider():
    return _make_provider()


@pytest.fixture
def model():
    return _make_model()


def _make_provider(base_url: Url = OPENAI_BASE_URL) -> ProviderConfig:
    return ProviderConfig(
        name="openai",
        api_base=f"{base_url}/v1",
        api_key_env_var="OPENAI_API_KEY",
        api_style="openai-responses",
    )


def _make_model() -> ModelConfig:
    return ModelConfig(name="gpt-4o", provider="openai", alias="gpt-4o")


def _make_backend(base_url: Url = OPENAI_BASE_URL) -> GenericBackend:
    return GenericBackend(provider=_make_provider(base_url))


class TrackingAsyncStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], error: Exception | None = None) -> None:
        self.chunks = chunks
        self.error = error
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
        if self.error is not None:
            raise self.error

    async def aclose(self) -> None:
        self.closed = True


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
        assert payload["include"] == ["reasoning.encrypted_content"]

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

    def test_consecutive_user_messages_are_preserved(self, adapter, provider):
        payload = _prepare(
            adapter,
            provider,
            [
                LLMMessage(role=Role.user, content="Hi"),
                LLMMessage(role=Role.user, content="Again"),
            ],
        )
        assert payload["input"] == [
            {"role": "user", "content": "Hi"},
            {"role": "user", "content": "Again"},
        ]

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
        # The assistant produced no text, so no message item is invented for it:
        # input[0] = user, input[1] = function_call, input[2] = function_call_output
        assert len(payload["input"]) == 3
        assert all(item.get("role") != "assistant" for item in payload["input"])
        fc = payload["input"][1]
        assert fc["type"] == "function_call"
        assert fc["call_id"] == "call_abc"
        assert fc["name"] == "get_weather"
        assert fc["arguments"] == '{"location": "Paris"}'
        fco = payload["input"][2]
        assert fco["type"] == "function_call_output"
        assert fco["call_id"] == "call_abc"

    def test_reasoning_items_replayed_verbatim(self, adapter, provider):
        item = {
            "type": "reasoning",
            "id": "rs_abc",
            "encrypted_content": "enc:abc",
            "summary": [{"type": "summary_text", "text": "Comparing options."}],
            "status": "completed",
        }
        payload = _prepare(
            adapter,
            provider,
            [
                LLMMessage(
                    role=Role.assistant, content="Answer", reasoning_payloads=[item]
                )
            ],
        )

        assert payload["input"][0] == item

    def test_reasoning_payloads_from_another_backend_is_dropped(
        self, adapter, provider
    ):
        payload = _prepare(
            adapter,
            provider,
            [
                LLMMessage(
                    role=Role.assistant,
                    content="Answer",
                    reasoning_payloads=[
                        {"type": "thinking", "thinking": "hmm", "signature": "sig"}
                    ],
                )
            ],
        )

        assert payload["input"] == [
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Answer"}],
            }
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
        assert chunk.usage.cached_tokens == 0

    def test_cached_tokens_parsed_from_input_details(self, adapter, provider):
        data = {
            "id": "resp_cached",
            "object": "response",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Hi"}],
                    "role": "assistant",
                }
            ],
            "usage": {
                "input_tokens": 100,
                "output_tokens": 5,
                "input_tokens_details": {"cached_tokens": 64},
            },
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.usage.cached_tokens == 64

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
        reasoning_item = {
            "type": "reasoning",
            "id": "rs_abc",
            "encrypted_content": "enc:abc",
            "summary": [{"type": "summary_text", "text": "Need to compare options."}],
        }
        data = {
            "id": "resp_reasoning",
            "object": "response",
            "output": [
                reasoning_item,
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
        assert chunk.message.reasoning_payloads == [reasoning_item]

        payload = _prepare(adapter, provider, [chunk.message])
        assert payload["input"][0] == reasoning_item

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
        assert chunk.message.reasoning_payloads == [
            {"type": "reasoning", "encrypted_content": "enc:streamed", "summary": []}
        ]
        assert chunk.usage.prompt_tokens == 50
        assert chunk.usage.completion_tokens == 25
        assert chunk.stop is not None
        assert chunk.stop.reason == "completed"

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
        assert chunk.stop is not None
        assert chunk.stop.reason == "incomplete"

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

    def test_response_object_accepts_null_error(self, adapter, provider):
        chunk = adapter.parse_response(
            {"type": "response.created", "response": {"id": "resp_123", "error": None}},
            provider,
        )

        assert chunk.message.content == ""
        assert chunk.usage.prompt_tokens == 0

    @pytest.mark.parametrize(
        ("error_type", "expected_status"),
        [
            ("authentication_error", HTTPStatus.UNAUTHORIZED),
            ("invalid_api_key", HTTPStatus.UNAUTHORIZED),
            ("too_many_requests", HTTPStatus.TOO_MANY_REQUESTS),
            ("rate_limit_error", HTTPStatus.TOO_MANY_REQUESTS),
            ("rate_limit_exceeded", HTTPStatus.TOO_MANY_REQUESTS),
            ("server_error", HTTPStatus.INTERNAL_SERVER_ERROR),
            ("unknown_error", None),
        ],
    )
    def test_error_event_raises_structured_stream_error(
        self, adapter, provider, error_type, expected_status
    ):
        with pytest.raises(OpenAIResponsesStreamError) as exc_info:
            adapter.parse_response(
                {
                    "type": "error",
                    "error": {"type": error_type, "message": "backend failed"},
                },
                provider,
            )

        assert exc_info.value.error_type == error_type
        assert exc_info.value.message == "backend failed"
        assert exc_info.value.status == expected_status

    @pytest.mark.parametrize(
        "data",
        [
            {
                "type": "error",
                "code": "rate_limit_exceeded",
                "message": "backend failed",
            },
            {
                "type": "response.failed",
                "response": {
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "backend failed",
                    }
                },
            },
        ],
    )
    def test_provider_error_shapes_raise_structured_stream_error(
        self, adapter, provider, data
    ):
        with pytest.raises(OpenAIResponsesStreamError) as exc_info:
            adapter.parse_response(data, provider)

        assert exc_info.value.error_type == "rate_limit_exceeded"
        assert exc_info.value.message == "backend failed"
        assert exc_info.value.status == HTTPStatus.TOO_MANY_REQUESTS

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
            mock_api.post(OPENAI_RESPONSES_PATH).mock(
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
            mock_api.post(OPENAI_RESPONSES_PATH).mock(
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
    async def test_streaming_rate_limit_event_retries_then_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        async def _no_sleep(_: float) -> None:
            pass

        monkeypatch.setattr("vibe.core.utils.retry.asyncio.sleep", _no_sleep)
        retry_reasons: list[RetryReason] = []

        async def record_retry(reason: RetryReason) -> None:
            retry_reasons.append(reason)

        error_response = httpx.Response(
            status_code=200,
            stream=httpx.ByteStream(
                b'data: {"type":"response.created","response":{"id":"resp_failed"}}\n\n'
                b'data: {"type":"response.in_progress","response":{"id":"resp_failed"}}\n\n'
                b'data: {"type":"response.output_item.added","output_index":0,"item":{"type":"message","phase":"final_answer"}}\n\n'
                b'data: {"type":"response.content_part.added","output_index":0}\n\n'
                b'data: {"type":"response.output_text.delta","output_index":0,"delta":""}\n\n'
                b'data: {"type":"response.function_call_arguments.delta","output_index":0,"delta":""}\n\n'
                b'data: {"type":"error","code":"rate_limit_exceeded","message":"Rate limit exceeded"}\n\n'
            ),
            headers={"Content-Type": "text/event-stream"},
        )
        success_response = httpx.Response(
            status_code=200,
            stream=httpx.ByteStream(
                b'data: {"type":"response.created","response":{"id":"resp_1"}}\n\n'
                b'data: {"type":"response.output_text.delta","output_index":0,"content_index":0,"delta":"hi"}\n\n'
                b'data: {"type":"response.completed","response":{"id":"resp_1","output":[{"type":"message","content":[{"type":"output_text","text":"hi"}],"role":"assistant"}],"usage":{"input_tokens":10,"output_tokens":5}}}\n\n'
                b"data: [DONE]\n\n"
            ),
            headers={"Content-Type": "text/event-stream"},
        )
        base_url = OPENAI_BASE_URL
        with respx.mock(base_url=base_url) as mock_api:
            route = mock_api.post(OPENAI_RESPONSES_PATH).mock(
                side_effect=[error_response, success_response]
            )
            backend = GenericBackend(
                provider=_make_provider(base_url), on_retry=record_retry
            )
            messages = [LLMMessage(role=Role.user, content="Just say hi")]

            results: list[LLMChunk] = []
            async for result in backend.complete_streaming(
                model=_make_model(),
                messages=messages,
                temperature=0.2,
                tools=None,
                max_tokens=None,
                tool_choice=None,
                extra_headers=None,
            ):
                results.append(result)

        assert route.call_count == 2
        assert retry_reasons == [RetryReason(RetryCategory.RATE_LIMITED, "HTTP 429")]
        assert "".join(result.message.content or "" for result in results) == "hi"

    @pytest.mark.asyncio
    async def test_transport_retry_resets_parser_state(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        async def _no_sleep(_: float) -> None:
            pass

        monkeypatch.setattr("vibe.core.utils.retry.asyncio.sleep", _no_sleep)
        failed_stream = TrackingAsyncStream(
            [
                b'data: {"type":"response.created","response":{"id":"resp_failed"}}\n\n'
                b'data: {"type":"response.output_item.added","output_index":0,"item":{"type":"message","phase":"commentary"}}\n\n'
            ],
            httpx.ReadError("stream reset"),
        )
        success_response = httpx.Response(
            status_code=200,
            stream=httpx.ByteStream(
                b'data: {"type":"response.output_text.delta","output_index":0,"content_index":0,"delta":"answer"}\n\n'
                b'data: {"type":"response.completed","response":{"id":"resp_1","output":[{"type":"message","content":[{"type":"output_text","text":"answer"}],"role":"assistant"}],"usage":{"input_tokens":10,"output_tokens":5}}}\n\n'
                b"data: [DONE]\n\n"
            ),
            headers={"Content-Type": "text/event-stream"},
        )

        with respx.mock(base_url=OPENAI_BASE_URL) as mock_api:
            route = mock_api.post(OPENAI_RESPONSES_PATH).mock(
                side_effect=[
                    httpx.Response(
                        status_code=200,
                        stream=failed_stream,
                        headers={"Content-Type": "text/event-stream"},
                    ),
                    success_response,
                ]
            )
            backend = _make_backend()
            results = [
                result
                async for result in backend.complete_streaming(
                    model=_make_model(),
                    messages=[LLMMessage(role=Role.user, content="hi")],
                )
            ]

        assert route.call_count == 2
        assert failed_stream.closed is True
        assert "".join(result.message.content or "" for result in results) == "answer"
        assert (
            "".join(result.message.reasoning_content or "" for result in results) == ""
        )

    @pytest.mark.asyncio
    async def test_stream_close_closes_http_response(self):
        response_stream = TrackingAsyncStream([
            b'data: {"type":"response.created","response":{"id":"resp_1"}}\n\n'
            b'data: {"type":"response.output_text.delta","output_index":0,"content_index":0,"delta":"hi"}\n\n'
            b'data: {"type":"response.completed","response":{"id":"resp_1","output":[]}}\n\n'
        ])
        with respx.mock(base_url=OPENAI_BASE_URL) as mock_api:
            mock_api.post(OPENAI_RESPONSES_PATH).mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=response_stream,
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            stream = _make_backend().complete_streaming(
                model=_make_model(), messages=[LLMMessage(role=Role.user, content="hi")]
            )
            await anext(stream)
            await stream.aclose()

        assert response_stream.closed is True

    @pytest.mark.asyncio
    async def test_streaming_rate_limit_event_exhausts_retries(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        # Budget-bounded retry retries for as long as the budget allows, then
        # re-raises. Drive a fake clock so a small budget elapses deterministically
        # instead of relying on real wall-clock time.
        now = [0.0]
        monkeypatch.setattr("vibe.core.utils.retry.time.monotonic", lambda: now[0])

        async def _advance(seconds: float) -> None:
            now[0] += seconds

        monkeypatch.setattr("vibe.core.utils.retry.asyncio.sleep", _advance)

        base_url = OPENAI_BASE_URL
        with respx.mock(base_url=base_url) as mock_api:
            route = mock_api.post(OPENAI_RESPONSES_PATH).mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(
                        b'data: {"type":"response.created","response":{"id":"resp_failed"}}\n\n'
                        b'data: {"type":"response.failed","response":{"error":{"code":"rate_limit_exceeded","message":"Rate limit exceeded"}}}\n\n'
                    ),
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            backend = GenericBackend(
                provider=_make_provider(base_url), retry_max_elapsed_time=1.0
            )
            messages = [LLMMessage(role=Role.user, content="Just say hi")]

            with pytest.raises(BackendError) as exc_info:
                async for _ in backend.complete_streaming(
                    model=_make_model(),
                    messages=messages,
                    temperature=0.2,
                    tools=None,
                    max_tokens=None,
                    tool_choice=None,
                    extra_headers=None,
                ):
                    pass

        error = exc_info.value
        assert error.status == HTTPStatus.TOO_MANY_REQUESTS
        assert error.reason == "rate_limit_exceeded"
        assert error.parsed_error == "Rate limit exceeded"
        # Retried at least once, then stopped once the 1s budget elapsed.
        assert route.call_count > 1
        assert now[0] >= 1.0

    @pytest.mark.asyncio
    async def test_streaming_rate_limit_after_output_is_not_retried(self):
        base_url = OPENAI_BASE_URL
        with respx.mock(base_url=base_url) as mock_api:
            route = mock_api.post(OPENAI_RESPONSES_PATH).mock(
                return_value=httpx.Response(
                    status_code=200,
                    stream=httpx.ByteStream(
                        b'data: {"type":"response.created","response":{"id":"resp_failed"}}\n\n'
                        b'data: {"type":"response.output_text.delta","output_index":0,"content_index":0,"delta":"partial"}\n\n'
                        b'data: {"type":"error","code":"rate_limit_exceeded","message":"Rate limit exceeded"}\n\n'
                    ),
                    headers={"Content-Type": "text/event-stream"},
                )
            )
            backend = _make_backend(base_url)
            messages = [LLMMessage(role=Role.user, content="Just say hi")]
            results: list[LLMChunk] = []

            with pytest.raises(BackendError):
                async for result in backend.complete_streaming(
                    model=_make_model(),
                    messages=messages,
                    temperature=0.2,
                    tools=None,
                    max_tokens=None,
                    tool_choice=None,
                    extra_headers=None,
                ):
                    results.append(result)

        assert route.call_count == 1
        assert "".join(result.message.content or "" for result in results) == "partial"

    @pytest.mark.asyncio
    async def test_streaming_payload_includes_stream_flag(self):
        base_url = OPENAI_BASE_URL
        with respx.mock(base_url=base_url) as mock_api:
            route = mock_api.post(OPENAI_RESPONSES_PATH).mock(
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
