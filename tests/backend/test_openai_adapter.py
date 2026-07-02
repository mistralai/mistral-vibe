from __future__ import annotations

import json

import pytest

from vibe.core.config import ProviderConfig
from vibe.core.llm.backend.generic import OpenAIAdapter
from vibe.core.types import LLMMessage, Role


@pytest.fixture
def adapter():
    return OpenAIAdapter()


@pytest.fixture
def provider():
    return ProviderConfig(
        name="test-openai",
        api_base="https://api.example.com/v1",
        api_key_env_var="TEST_API_KEY",
    )


def _prepare(adapter, provider, messages, **kwargs):
    defaults = dict(
        model_name="m",
        messages=messages,
        temperature=0,
        tools=None,
        max_tokens=None,
        tool_choice=None,
        enable_streaming=False,
        provider=provider,
    )
    defaults.update(kwargs)
    return json.loads(adapter.prepare_request(**defaults).body)


def _history_with_reasoning() -> list[LLMMessage]:
    return [
        LLMMessage(role=Role.user, content="Hi"),
        LLMMessage(
            role=Role.assistant, content="Answer", reasoning_content="Let me think..."
        ),
    ]


class TestReasoningContentForwarding:
    def test_reasoning_dropped_when_thinking_off(self, adapter, provider):
        payload = _prepare(adapter, provider, _history_with_reasoning(), thinking="off")
        msg = payload["messages"][1]
        assert msg["content"] == "Answer"
        assert "reasoning_content" not in msg

    def test_reasoning_kept_when_thinking_enabled(self, adapter, provider):
        payload = _prepare(
            adapter, provider, _history_with_reasoning(), thinking="medium"
        )
        msg = payload["messages"][1]
        assert msg["reasoning_content"] == "Let me think..."

    def test_reasoning_renamed_and_dropped_with_custom_field_name(self, adapter):
        provider = ProviderConfig(
            name="test-openai",
            api_base="https://api.example.com/v1",
            api_key_env_var="TEST_API_KEY",
            reasoning_field_name="reasoning",
        )
        kept = _prepare(adapter, provider, _history_with_reasoning(), thinking="high")
        assert kept["messages"][1]["reasoning"] == "Let me think..."

        dropped = _prepare(adapter, provider, _history_with_reasoning(), thinking="off")
        assert "reasoning" not in dropped["messages"][1]
        assert "reasoning_content" not in dropped["messages"][1]
