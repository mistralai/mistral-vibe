from __future__ import annotations

import json

import pytest

from vibe.core.config import ProviderConfig
from vibe.core.llm.backend.azure import (
    AzureOpenAIAdapter,
    build_azure_base_url,
    build_azure_endpoint,
)
from vibe.core.types import AvailableFunction, AvailableTool, LLMMessage, Role


@pytest.fixture
def adapter():
    return AzureOpenAIAdapter()


@pytest.fixture
def provider():
    return ProviderConfig(
        name="azure",
        api_base="",
        api_key_env_var="AZURE_API_KEY",
        api_style="azure",
        resource_name="test-resource",
        api_version="2024-02-01",
    )


class TestBuildAzureEndpoint:
    def test_endpoint_url(self):
        endpoint = build_azure_endpoint("gpt-4", "2024-02-01")
        assert (
            endpoint
            == "/openai/deployments/gpt-4/chat/completions?api-version=2024-02-01"
        )

    def test_base_url(self):
        base_url = build_azure_base_url("test-resource")
        assert base_url == "https://test-resource.openai.azure.com"


class TestAdapterBuildPayload:
    def test_basic_payload(self, adapter):
        messages = [{"role": "user", "content": "Hello"}]
        payload = adapter.build_payload(
            model_name="gpt-4",
            converted_messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=1024,
            tool_choice=None,
        )
        assert payload["model"] == "gpt-4"
        assert payload["messages"] == messages
        assert payload["temperature"] == 0.5
        assert payload["max_tokens"] == 1024

    def test_with_tools(self, adapter):
        tools = [
            AvailableTool(
                function=AvailableFunction(
                    name="test_tool",
                    description="A test tool",
                    parameters={"type": "object", "properties": {}},
                )
            )
        ]
        messages = [{"role": "user", "content": "Hello"}]
        payload = adapter.build_payload(
            model_name="gpt-4",
            converted_messages=messages,
            temperature=0.5,
            tools=tools,
            max_tokens=1024,
            tool_choice=None,
        )
        assert "tools" in payload
        assert len(payload["tools"]) == 1
        assert payload["tools"][0]["function"]["name"] == "test_tool"

    def test_with_tool_choice(self, adapter):
        tools = [
            AvailableTool(
                function=AvailableFunction(
                    name="test_tool",
                    description="A test tool",
                    parameters={"type": "object", "properties": {}},
                )
            )
        ]
        messages = [{"role": "user", "content": "Hello"}]
        payload = adapter.build_payload(
            model_name="gpt-4",
            converted_messages=messages,
            temperature=0.5,
            tools=tools,
            max_tokens=1024,
            tool_choice="auto",
        )
        assert payload["tool_choice"] == "auto"

    def test_without_max_tokens(self, adapter):
        messages = [{"role": "user", "content": "Hello"}]
        payload = adapter.build_payload(
            model_name="gpt-4",
            converted_messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=None,
            tool_choice=None,
        )
        assert "max_tokens" not in payload


class TestAdapterBuildHeaders:
    def test_basic_headers(self, adapter):
        headers = adapter.build_headers()
        assert headers["Content-Type"] == "application/json"
        assert "Authorization" not in headers

    def test_with_api_key(self, adapter):
        headers = adapter.build_headers(api_key="test-key-123")
        assert headers["Content-Type"] == "application/json"
        assert headers["Authorization"] == "Bearer test-key-123"


class TestAdapterReasoningConversion:
    def test_reasoning_to_api_default(self, adapter):
        msg_dict = {
            "role": "assistant",
            "content": "Hello",
            "reasoning_content": "thinking...",
        }
        result = adapter._reasoning_to_api(msg_dict, "reasoning_content")
        assert result == msg_dict

    def test_reasoning_to_api_custom_field(self, adapter):
        msg_dict = {
            "role": "assistant",
            "content": "Hello",
            "reasoning_content": "thinking...",
        }
        result = adapter._reasoning_to_api(msg_dict, "custom_reasoning")
        assert "reasoning_content" not in result
        assert result["custom_reasoning"] == "thinking..."

    def test_reasoning_from_api_default(self, adapter):
        msg_dict = {
            "role": "assistant",
            "content": "Hello",
            "reasoning_content": "thinking...",
        }
        result = adapter._reasoning_from_api(msg_dict, "reasoning_content")
        assert result == msg_dict

    def test_reasoning_from_api_custom_field(self, adapter):
        msg_dict = {
            "role": "assistant",
            "content": "Hello",
            "custom_reasoning": "thinking...",
        }
        result = adapter._reasoning_from_api(msg_dict, "custom_reasoning")
        assert "custom_reasoning" not in result
        assert result["reasoning_content"] == "thinking..."


class TestAdapterPrepareRequest:
    def test_basic_request(self, adapter, provider):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        req = adapter.prepare_request(
            model_name="gpt-4",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=1024,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
        )

        payload = json.loads(req.body)
        assert payload["model"] == "gpt-4"
        assert payload["max_tokens"] == 1024
        assert payload["temperature"] == 0.5
        assert (
            req.endpoint
            == "/openai/deployments/gpt-4/chat/completions?api-version=2024-02-01"
        )
        assert req.base_url == "https://test-resource.openai.azure.com"
        assert req.headers["Content-Type"] == "application/json"

    def test_streaming_request(self, adapter, provider):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        req = adapter.prepare_request(
            model_name="gpt-4",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=1024,
            tool_choice=None,
            enable_streaming=True,
            provider=provider,
        )

        payload = json.loads(req.body)
        assert payload["stream"] is True
        assert "stream_options" in payload
        assert payload["stream_options"]["include_usage"] is True

    def test_with_api_key(self, adapter, provider):
        messages = [LLMMessage(role=Role.user, content="Hello")]
        req = adapter.prepare_request(
            model_name="gpt-4",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=1024,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
            api_key="test-key-123",
        )
        assert req.headers["Authorization"] == "Bearer test-key-123"

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
            model_name="gpt-4",
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
        assert payload["tools"][0]["function"]["name"] == "test_tool"

    def test_with_reasoning_field(self, adapter, provider):
        provider.reasoning_field_name = "custom_reasoning"
        messages = [
            LLMMessage(role=Role.user, content="Hello"),
            LLMMessage(
                role=Role.assistant, content="Answer", reasoning_content="thinking..."
            ),
        ]
        req = adapter.prepare_request(
            model_name="gpt-4",
            messages=messages,
            temperature=0.5,
            tools=None,
            max_tokens=1024,
            tool_choice=None,
            enable_streaming=False,
            provider=provider,
        )
        payload = json.loads(req.body)
        assert payload["messages"][1]["custom_reasoning"] == "thinking..."
        assert "reasoning_content" not in payload["messages"][1]


class TestAdapterParseMessage:
    def test_parse_message_with_choices(self, adapter):
        data = {
            "choices": [{"message": {"role": "assistant", "content": "Hello there!"}}]
        }
        message = adapter._parse_message(data, "reasoning_content")
        assert message is not None
        assert message.role == Role.assistant
        assert message.content == "Hello there!"

    def test_parse_message_with_delta(self, adapter):
        data = {"choices": [{"delta": {"role": "assistant", "content": "Hello"}}]}
        message = adapter._parse_message(data, "reasoning_content")
        assert message is not None
        assert message.role == Role.assistant
        assert message.content == "Hello"

    def test_parse_message_direct_message(self, adapter):
        data = {"message": {"role": "assistant", "content": "Direct message"}}
        message = adapter._parse_message(data, "reasoning_content")
        assert message is not None
        assert message.role == Role.assistant
        assert message.content == "Direct message"

    def test_parse_message_direct_delta(self, adapter):
        data = {"delta": {"role": "assistant", "content": "Delta content"}}
        message = adapter._parse_message(data, "reasoning_content")
        assert message is not None
        assert message.role == Role.assistant
        assert message.content == "Delta content"

    def test_parse_message_none(self, adapter):
        data = {"some_other_field": "value"}
        message = adapter._parse_message(data, "reasoning_content")
        assert message is None

    def test_parse_message_with_reasoning_field(self, adapter):
        data = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Answer",
                        "custom_reasoning": "thinking...",
                    }
                }
            ]
        }
        message = adapter._parse_message(data, "custom_reasoning")
        assert message is not None
        assert message.reasoning_content == "thinking..."
        assert "custom_reasoning" not in message.model_dump()


class TestAdapterParseResponse:
    def test_non_streaming_response(self, adapter, provider):
        data = {
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Hello!"
        assert chunk.usage.prompt_tokens == 10
        assert chunk.usage.completion_tokens == 5

    def test_streaming_response(self, adapter, provider):
        data = {"choices": [{"delta": {"role": "assistant", "content": "Hi"}}]}
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Hi"
        assert chunk.usage.prompt_tokens == 0
        assert chunk.usage.completion_tokens == 0

    def test_response_with_reasoning(self, adapter, provider):
        provider.reasoning_field_name = "custom_reasoning"
        data = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "Answer",
                        "custom_reasoning": "thinking...",
                    }
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Answer"
        assert chunk.message.reasoning_content == "thinking..."
        assert chunk.usage.prompt_tokens == 10

    def test_empty_response_returns_default_message(self, adapter, provider):
        data = {"some_other_field": "value"}
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.role == Role.assistant
        assert chunk.message.content == ""
        assert chunk.usage.prompt_tokens == 0
        assert chunk.usage.completion_tokens == 0
