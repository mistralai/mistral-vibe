from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from vibe.core.config import ProviderConfig
from vibe.core.llm.backend.vertex import (
    VertexAnthropicAdapter,
    build_vertex_base_url,
    build_vertex_endpoint,
)
from vibe.core.types import AvailableFunction, AvailableTool, LLMMessage, Role


@pytest.fixture
def adapter():
    return VertexAnthropicAdapter()


@pytest.fixture
def provider():
    return ProviderConfig(
        name="vertex",
        api_base="",
        project_id="test-project",
        region="us-central1",
        api_style="vertex-anthropic",
    )


class TestBuildVertexEndpoint:
    def test_non_streaming(self):
        endpoint = build_vertex_endpoint(
            "us-central1", "my-project", "claude-3-5-sonnet"
        )
        assert endpoint == (
            "/v1/projects/my-project/locations/us-central1/"
            "publishers/anthropic/models/claude-3-5-sonnet:rawPredict"
        )

    def test_streaming(self):
        endpoint = build_vertex_endpoint(
            "us-central1", "my-project", "claude-3-5-sonnet", streaming=True
        )
        assert endpoint == (
            "/v1/projects/my-project/locations/us-central1/"
            "publishers/anthropic/models/claude-3-5-sonnet:streamRawPredict"
        )

    def test_base_url(self):
        base = build_vertex_base_url("us-central1")
        assert base == "https://us-central1-aiplatform.googleapis.com"

    def test_global_endpoint(self):
        endpoint = build_vertex_endpoint("global", "my-project", "claude-3-5-sonnet")
        assert endpoint == (
            "/v1/projects/my-project/locations/global/"
            "publishers/anthropic/models/claude-3-5-sonnet:rawPredict"
        )

    def test_global_base_url(self):
        base = build_vertex_base_url("global")
        assert base == "https://aiplatform.googleapis.com"


class TestPrepareRequest:
    @patch(
        "vibe.core.llm.backend.vertex.get_vertex_access_token",
        return_value="fake-token",
    )
    def test_basic_request(self, mock_token, adapter, provider):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        req = adapter.prepare_request(
            model_name="claude-3-5-sonnet",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=1024,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
        )

        payload = json.loads(req.body)
        assert payload["anthropic_version"] == "vertex-2023-10-16"
        assert "model" not in payload
        assert payload["max_tokens"] == 1024
        assert payload["temperature"] == 0.5
        assert req.headers["Authorization"] == "Bearer fake-token"
        assert req.headers["anthropic-beta"] == adapter.BETA_FEATURES
        assert "rawPredict" in req.endpoint
        assert "streamRawPredict" not in req.endpoint
        assert req.base_url == "https://us-central1-aiplatform.googleapis.com"

    @patch(
        "vibe.core.llm.backend.vertex.get_vertex_access_token",
        return_value="fake-token",
    )
    def test_streaming_request(self, mock_token, adapter, provider):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        req = adapter.prepare_request(
            model_name="claude-3-5-sonnet",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=1024,
            tool_choice=None,
            enable_streaming=True,
            provider=provider,
        )

        payload = json.loads(req.body)
        assert payload.get("stream") is True
        assert "streamRawPredict" in req.endpoint

    @patch(
        "vibe.core.llm.backend.vertex.get_vertex_access_token",
        return_value="fake-token",
    )
    def test_no_beta_features_for_vertex(self, mock_token, adapter, provider):
        """Vertex AI doesn't support the same beta features as direct Anthropic API."""
        messages = [LLMMessage(role=Role.user, content="Hello")]
        req = adapter.prepare_request(
            model_name="claude-3-5-sonnet",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=1024,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
        )

        # Vertex AI doesn't support prompt-caching or other beta features
        assert req.headers.get("anthropic-beta", "") == ""

    @patch(
        "vibe.core.llm.backend.vertex.get_vertex_access_token",
        return_value="fake-token",
    )
    def test_with_extended_thinking(self, mock_token, adapter, provider):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        req = adapter.prepare_request(
            model_name="claude-3-5-sonnet",
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

    @patch(
        "vibe.core.llm.backend.vertex.get_vertex_access_token",
        return_value="fake-token",
    )
    def test_with_tools(self, mock_token, adapter, provider):
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
            model_name="claude-3-5-sonnet",
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

    def test_missing_project_id(self, adapter):
        provider = ProviderConfig(
            name="vertex",
            api_base="",
            region="us-central1",
            api_style="vertex-anthropic",
        )
        with pytest.raises(ValueError, match="project_id"):
            adapter.prepare_request(
                model_name="claude-3-5-sonnet",
                messages=[LLMMessage(role=Role.user, content="Hello")],
                temperature=0.5,
                tools=None,
                max_tokens=1024,
                tool_choice=None,
                enable_streaming=False,
                provider=provider,
            )

    def test_missing_region(self, adapter):
        provider = ProviderConfig(
            name="vertex",
            api_base="",
            project_id="test-project",
            api_style="vertex-anthropic",
        )
        with pytest.raises(ValueError, match="region"):
            adapter.prepare_request(
                model_name="claude-3-5-sonnet",
                messages=[LLMMessage(role=Role.user, content="Hello")],
                temperature=0.5,
                tools=None,
                max_tokens=1024,
                tool_choice=None,
                enable_streaming=False,
                provider=provider,
            )

    @patch(
        "vibe.core.llm.backend.vertex.get_vertex_access_token",
        return_value="fake-token",
    )
    def test_default_max_tokens(self, mock_token, adapter, provider):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        req = adapter.prepare_request(
            model_name="claude-3-5-sonnet",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=None,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
        )

        payload = json.loads(req.body)
        assert payload["max_tokens"] == adapter.DEFAULT_MAX_TOKENS


class TestParseFullResponse:
    def test_simple_text_response(self, adapter, provider):
        data = {
            "content": [{"type": "text", "text": "Hello there!"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Hello there!"
        assert chunk.usage.prompt_tokens == 10
        assert chunk.usage.completion_tokens == 5

    def test_response_with_tool_calls(self, adapter, provider):
        data = {
            "content": [
                {"type": "text", "text": "Let me help."},
                {
                    "type": "tool_use",
                    "id": "tool_123",
                    "name": "search",
                    "input": {"query": "test"},
                },
            ],
            "usage": {"input_tokens": 20, "output_tokens": 15},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Let me help."
        assert len(chunk.message.tool_calls) == 1
        assert chunk.message.tool_calls[0].id == "tool_123"
        assert chunk.message.tool_calls[0].function.name == "search"
        assert json.loads(chunk.message.tool_calls[0].function.arguments) == {
            "query": "test"
        }

    def test_response_with_thinking(self, adapter, provider):
        data = {
            "content": [
                {
                    "type": "thinking",
                    "thinking": "Let me think...",
                    "signature": "sig123",
                },
                {"type": "text", "text": "Here's my answer."},
            ],
            "usage": {"input_tokens": 30, "output_tokens": 20},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Here's my answer."
        assert chunk.message.reasoning_content == "Let me think..."
        assert chunk.message.reasoning_signature == "sig123"

    def test_response_with_cache_tokens(self, adapter, provider):
        data = {
            "content": [{"type": "text", "text": "Hello"}],
            "usage": {
                "input_tokens": 10,
                "cache_creation_input_tokens": 5,
                "cache_read_input_tokens": 3,
                "output_tokens": 7,
            },
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.usage.prompt_tokens == 18
        assert chunk.usage.completion_tokens == 7

    def test_response_with_redacted_thinking(self, adapter, provider):
        data = {
            "content": [
                {"type": "redacted_thinking", "data": "redacted_data_here"},
                {"type": "text", "text": "Answer."},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Answer."
        assert chunk.message.reasoning_content is None

    def test_response_empty_usage(self, adapter, provider):
        data = {"content": [{"type": "text", "text": "Hello"}], "usage": {}}
        chunk = adapter.parse_response(data, provider)
        assert chunk.usage.prompt_tokens == 0
        assert chunk.usage.completion_tokens == 0


class TestStreamingEvents:
    def test_message_start(self, adapter, provider):
        data = {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 100,
                    "cache_creation_input_tokens": 20,
                    "cache_read_input_tokens": 10,
                }
            },
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.usage is not None
        assert chunk.usage.prompt_tokens == 130
        assert chunk.usage.completion_tokens == 0

    def test_message_start_without_usage(self, adapter, provider):
        data = {"type": "message_start", "message": {}}
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.role == Role.assistant

    def test_content_block_start_tool_use(self, adapter, provider):
        data = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "tool_use", "id": "tool_abc", "name": "search"},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.tool_calls is not None
        assert len(chunk.message.tool_calls) == 1
        assert chunk.message.tool_calls[0].id == "tool_abc"
        assert chunk.message.tool_calls[0].function.name == "search"
        assert chunk.message.tool_calls[0].index == 0

    def test_content_block_start_thinking(self, adapter, provider):
        data = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "thinking", "thinking": ""},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.reasoning_content is not None

    def test_content_block_start_redacted_thinking(self, adapter, provider):
        data = {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "redacted_thinking", "data": "abc"},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content is None
        assert chunk.message.reasoning_content is None

    def test_content_block_delta_text(self, adapter, provider):
        data = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Hello"

    def test_content_block_delta_thinking(self, adapter, provider):
        data = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "I think..."},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.reasoning_content == "I think..."

    def test_content_block_delta_input_json(self, adapter, provider):
        data = {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": '{"key":'},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.tool_calls is not None
        assert chunk.message.tool_calls[0].function.arguments == '{"key":'

    def test_content_block_stop(self, adapter, provider):
        data = {"type": "content_block_stop", "index": 0}
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content is None
        assert chunk.message.reasoning_content is None

    def test_message_delta_with_usage(self, adapter, provider):
        data = {"type": "message_delta", "usage": {"output_tokens": 42}}
        chunk = adapter.parse_response(data, provider)
        assert chunk.usage is not None
        assert chunk.usage.completion_tokens == 42
        assert chunk.usage.prompt_tokens == 0

    def test_message_delta_without_usage(self, adapter, provider):
        data = {"type": "message_delta", "usage": {}}
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.role == Role.assistant

    def test_unknown_event_returns_empty_chunk(self, adapter, provider):
        data = {"type": "ping"}
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.role == Role.assistant
        assert chunk.message.content is None

    def test_signature_delta(self, adapter, provider):
        data = {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "signature_delta", "signature": "sig_abc"},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.reasoning_signature == "sig_abc"

    def test_message_start_resets_state(self, adapter, provider):
        adapter._current_index = 5

        data = {"type": "message_start", "message": {"usage": {"input_tokens": 10}}}
        adapter.parse_response(data, provider)
        assert adapter._current_index == 0

    def test_full_streaming_sequence(self, adapter, provider):
        chunks = []

        # message_start
        chunks.append(
            adapter.parse_response(
                {"type": "message_start", "message": {"usage": {"input_tokens": 50}}},
                provider,
            )
        )
        assert chunks[-1].usage.prompt_tokens == 50

        # thinking block
        adapter.parse_response(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "thinking", "thinking": ""},
            },
            provider,
        )
        chunks.append(
            adapter.parse_response(
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": "Analyzing..."},
                },
                provider,
            )
        )
        assert chunks[-1].message.reasoning_content == "Analyzing..."
        adapter.parse_response({"type": "content_block_stop", "index": 0}, provider)

        # text block
        chunks.append(
            adapter.parse_response(
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": "Here's the result."},
                },
                provider,
            )
        )
        assert chunks[-1].message.content == "Here's the result."

        # tool use
        chunks.append(
            adapter.parse_response(
                {
                    "type": "content_block_start",
                    "index": 2,
                    "content_block": {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "search",
                    },
                },
                provider,
            )
        )
        assert chunks[-1].message.tool_calls[0].function.name == "search"

        # message_delta with final usage
        chunks.append(
            adapter.parse_response(
                {"type": "message_delta", "usage": {"output_tokens": 100}}, provider
            )
        )
        assert chunks[-1].usage.completion_tokens == 100


class TestHelperMethods:
    def test_has_thinking_content_true(self, adapter):
        messages = [
            {"role": "user", "content": "Hello"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Let me think..."},
                    {"type": "text", "text": "Answer"},
                ],
            },
        ]
        assert adapter._has_thinking_content(messages) is True

    def test_has_thinking_content_false(self, adapter):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Just text"},
        ]
        assert adapter._has_thinking_content(messages) is False

    def test_has_thinking_content_empty(self, adapter):
        assert adapter._has_thinking_content([]) is False

    def test_has_thinking_content_non_list_content(self, adapter):
        messages = [
            {"role": "assistant", "content": [{"type": "text", "text": "no thinking"}]}
        ]
        assert adapter._has_thinking_content(messages) is False

    def test_add_cache_control_to_last_user_message(self, adapter):
        messages = [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]
        adapter._add_cache_control_to_last_user_message(messages)
        assert messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_add_cache_control_skips_non_user(self, adapter):
        messages = [
            {"role": "assistant", "content": [{"type": "text", "text": "Hello"}]}
        ]
        adapter._add_cache_control_to_last_user_message(messages)
        assert "cache_control" not in messages[0]["content"][0]

    def test_add_cache_control_skips_string_content(self, adapter):
        messages = [{"role": "user", "content": "Hello"}]
        adapter._add_cache_control_to_last_user_message(messages)
        assert messages[0]["content"] == "Hello"

    def test_add_cache_control_tool_result(self, adapter):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "123", "content": "result"}
                ],
            }
        ]
        adapter._add_cache_control_to_last_user_message(messages)
        assert messages[0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_add_cache_control_empty_messages(self, adapter):
        messages: list[dict] = []
        adapter._add_cache_control_to_last_user_message(messages)
        assert messages == []
