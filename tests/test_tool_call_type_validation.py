"""Test for tool call type validation fix.

Issue: https://github.com/mistralai/mistral-vibe/issues/474
When using vLLM with certain models (e.g., Qwen), tool calls may have
type=None which caused Pydantic validation errors.
"""

import pytest

from vibe.core.types import ToolCall, LLMMessage, Role, FunctionCall


class TestToolCallTypeValidation:
    """Test that ToolCall handles None type values gracefully."""

    def test_tool_call_with_explicit_function_type(self) -> None:
        """Tool call with explicit type='function' works."""
        tc = ToolCall(
            id="call_1",
            index=0,
            type="function",
            function=FunctionCall(name="test", arguments="{}")
        )
        assert tc.type == "function"

    def test_tool_call_with_none_type_gets_default(self) -> None:
        """Tool call with type=None defaults to 'function'."""
        tc = ToolCall(
            id="call_1",
            index=0,
            type=None,  # type: ignore[arg-type]
            function=FunctionCall(name="test", arguments="{}")
        )
        assert tc.type == "function"

    def test_tool_call_without_type_field(self) -> None:
        """Tool call without type field defaults to 'function'."""
        tc = ToolCall.model_validate({
            "id": "call_1",
            "index": 0,
            "function": {"name": "test", "arguments": "{}"}
        })
        assert tc.type == "function"

    def test_llm_message_with_tool_calls_having_none_type(self) -> None:
        """LLMMessage accepts tool_calls with type=None (vLLM compatibility)."""
        msg = LLMMessage.model_validate({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "index": 0,
                "type": None,
                "function": {"name": "test", "arguments": "{}"}
            }]
        })
        assert msg.role == Role.assistant
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].type == "function"

    def test_llm_message_with_multiple_tool_calls_some_none_type(self) -> None:
        """LLMMessage handles mix of tool calls with and without type."""
        msg = LLMMessage.model_validate({
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call_1", "index": 0, "type": None, "function": {"name": "tool1", "arguments": "{}"}},
                {"id": "call_2", "index": 1, "type": "function", "function": {"name": "tool2", "arguments": "{}"}},
                {"id": "call_3", "index": 2, "function": {"name": "tool3", "arguments": "{}"}},
            ]
        })
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 3
        for tc in msg.tool_calls:
            assert tc.type == "function"
