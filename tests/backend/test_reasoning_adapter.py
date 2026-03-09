from __future__ import annotations

import json

import pytest

from vibe.core.config import ProviderConfig
from vibe.core.llm.backend.reasoning_adapter import ReasoningAdapter
from vibe.core.types import (
    AvailableFunction,
    AvailableTool,
    FunctionCall,
    LLMMessage,
    Role,
    ToolCall,
)


@pytest.fixture
def adapter():
    return ReasoningAdapter()


@pytest.fixture
def provider():
    return ProviderConfig(
        name="test-reasoning",
        api_base="https://api.example.com/v1",
        api_key_env_var="TEST_API_KEY",
        api_style="reasoning",
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


class TestReasoningEffort:
    @pytest.mark.parametrize("level", ["low", "medium", "high"])
    def test_sets_reasoning_effort(self, adapter, provider, level):
        payload = _prepare(
            adapter,
            provider,
            [LLMMessage(role=Role.user, content="Hi")],
            thinking=level,
        )
        assert payload["reasoning_effort"] == level

    def test_omitted_when_off(self, adapter, provider):
        payload = _prepare(
            adapter,
            provider,
            [LLMMessage(role=Role.user, content="Hi")],
            thinking="off",
        )
        assert "reasoning_effort" not in payload


class TestThinkingBlocksConversion:
    def test_assistant_with_reasoning_to_content_blocks(self, adapter, provider):
        messages = [
            LLMMessage(role=Role.user, content="Hi"),
            LLMMessage(
                role=Role.assistant,
                content="Answer",
                reasoning_content="Let me think...",
            ),
        ]
        payload = _prepare(adapter, provider, messages)
        msg = payload["messages"][1]
        assert msg["content"] == [
            {
                "type": "thinking",
                "thinking": [{"type": "text", "text": "Let me think..."}],
            },
            {"type": "text", "text": "Answer"},
        ]

    def test_assistant_without_reasoning_is_plain_string(self, adapter, provider):
        messages = [
            LLMMessage(role=Role.user, content="Hi"),
            LLMMessage(role=Role.assistant, content="Hello"),
        ]
        payload = _prepare(adapter, provider, messages)
        assert payload["messages"][1]["content"] == "Hello"

    def test_assistant_with_reasoning_and_tool_calls(self, adapter, provider):
        messages = [
            LLMMessage(role=Role.user, content="Hi"),
            LLMMessage(
                role=Role.assistant,
                content="Let me search.",
                reasoning_content="I should look this up.",
                tool_calls=[
                    ToolCall(
                        id="tc_1",
                        index=0,
                        function=FunctionCall(name="search", arguments='{"q": "test"}'),
                    )
                ],
            ),
        ]
        payload = _prepare(adapter, provider, messages)
        msg = payload["messages"][1]
        assert msg["content"][0]["type"] == "thinking"
        assert msg["content"][1] == {"type": "text", "text": "Let me search."}
        assert msg["tool_calls"][0]["id"] == "tc_1"
        assert msg["tool_calls"][0]["function"]["name"] == "search"

    def test_tools_in_payload(self, adapter, provider):
        tools = [
            AvailableTool(
                function=AvailableFunction(
                    name="search",
                    description="Search things",
                    parameters={"type": "object", "properties": {}},
                )
            )
        ]
        payload = _prepare(
            adapter, provider, [LLMMessage(role=Role.user, content="Hi")], tools=tools
        )
        assert len(payload["tools"]) == 1
        assert payload["tools"][0]["function"]["name"] == "search"


class TestParseThinkingBlocks:
    def test_string_content(self, adapter, provider):
        data = {
            "choices": [{"message": {"role": "assistant", "content": "Hello!"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Hello!"
        assert chunk.message.reasoning_content is None

    def test_thinking_and_text_blocks(self, adapter, provider):
        data = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": [
                                    {"type": "text", "text": "Let me reason..."}
                                ],
                            },
                            {"type": "text", "text": "Final answer"},
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Final answer"
        assert chunk.message.reasoning_content == "Let me reason..."

    def test_multiple_thinking_inner_blocks(self, adapter, provider):
        data = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": [
                                    {"type": "text", "text": "Step 1. "},
                                    {"type": "text", "text": "Step 2."},
                                ],
                            },
                            {"type": "text", "text": "Done"},
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.reasoning_content == "Step 1. Step 2."

    def test_tool_calls_in_response(self, adapter, provider):
        data = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": [
                                    {"type": "text", "text": "need to search"}
                                ],
                            },
                            {"type": "text", "text": "Searching..."},
                        ],
                        "tool_calls": [
                            {
                                "id": "tc_1",
                                "index": 0,
                                "function": {
                                    "name": "search",
                                    "arguments": '{"q": "test"}',
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.reasoning_content == "need to search"
        assert chunk.message.content == "Searching..."
        assert chunk.message.tool_calls[0].function.name == "search"

    def test_streaming_text_delta_is_plain_string(self, adapter, provider):
        data = {"choices": [{"delta": {"role": "assistant", "content": "Hi"}}]}
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.content == "Hi"
        assert chunk.message.reasoning_content is None

    def test_thinking_delta_streaming(self, adapter, provider):
        data = {
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": [{"type": "text", "text": "hmm"}],
                            }
                        ],
                    }
                }
            ]
        }
        chunk = adapter.parse_response(data, provider)
        assert chunk.message.reasoning_content == "hmm"
