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
        api_style="azure-openai",
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


class TestBuildPayload:
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
        assert "model" not in payload
        assert payload["messages"] == messages
        assert payload["temperature"] == 0.5
        assert payload["max_tokens"] == 1024


class TestPrepareRequest:
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

    def test_missing_resource_name(self, adapter):
        provider = ProviderConfig(
            name="azure",
            api_base="",
            api_key_env_var="AZURE_API_KEY",
            api_style="azure-openai",
            api_version="2024-02-01",
        )
        with pytest.raises(ValueError, match="resource_name"):
            adapter.prepare_request(
                model_name="gpt-4",
                messages=[LLMMessage(role=Role.user, content="Hello")],
                temperature=0.5,
                tools=None,
                max_tokens=1024,
                tool_choice=None,
                enable_streaming=False,
                provider=provider,
            )

    def test_missing_api_version(self, adapter):
        provider = ProviderConfig(
            name="azure",
            api_base="",
            api_key_env_var="AZURE_API_KEY",
            api_style="azure-openai",
            resource_name="test-resource",
        )
        with pytest.raises(ValueError, match="api_version"):
            adapter.prepare_request(
                model_name="gpt-4",
                messages=[LLMMessage(role=Role.user, content="Hello")],
                temperature=0.5,
                tools=None,
                max_tokens=1024,
                tool_choice=None,
                enable_streaming=False,
                provider=provider,
            )
