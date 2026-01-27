from __future__ import annotations

import json
from uuid import UUID

from vibe.core.types import (
    AssistantEvent,
    LLMMessage,
    ReasoningEvent,
    Role,
    UserMessageEvent,
)


class TestLLMMessageId:
    def test_user_message_gets_message_id(self) -> None:
        msg = LLMMessage(role=Role.user, content="Hello")
        assert msg.message_id is not None
        UUID(msg.message_id)  # Validates it's a valid UUID

    def test_assistant_message_gets_message_id(self) -> None:
        msg = LLMMessage(role=Role.assistant, content="Hi there")
        assert msg.message_id is not None
        UUID(msg.message_id)

    def test_system_message_gets_message_id(self) -> None:
        msg = LLMMessage(role=Role.system, content="You are helpful")
        assert msg.message_id is not None
        UUID(msg.message_id)

    def test_tool_message_does_not_get_message_id(self) -> None:
        msg = LLMMessage(role=Role.tool, content="result", tool_call_id="tc_123")
        assert msg.message_id is None

    def test_each_message_gets_unique_id(self) -> None:
        msg1 = LLMMessage(role=Role.user, content="First")
        msg2 = LLMMessage(role=Role.user, content="Second")
        assert msg1.message_id != msg2.message_id

    def test_message_id_preserved_from_dict(self) -> None:
        expected_id = "custom-message-id-123"
        msg = LLMMessage.model_validate({
            "role": "user",
            "content": "Hello",
            "message_id": expected_id,
        })
        assert msg.message_id == expected_id

    def test_message_id_preserved_for_tool_from_dict(self) -> None:
        expected_id = "tool-message-id"
        msg = LLMMessage.model_validate({
            "role": "tool",
            "content": "result",
            "tool_call_id": "tc_123",
            "message_id": expected_id,
        })
        assert msg.message_id == expected_id

    def test_tool_message_no_id_from_dict_without_id(self) -> None:
        msg = LLMMessage.model_validate({
            "role": "tool",
            "content": "result",
            "tool_call_id": "tc_123",
        })
        assert msg.message_id is None


class TestLLMMessageAccumulation:
    def test_message_id_preserved_on_add(self) -> None:
        msg1 = LLMMessage(role=Role.assistant, content="Hello")
        msg2 = LLMMessage(role=Role.assistant, content=" world")

        result = msg1 + msg2

        assert result.message_id == msg1.message_id
        assert result.content == "Hello world"

    def test_message_id_preserved_after_multiple_adds(self) -> None:
        msg1 = LLMMessage(role=Role.assistant, content="A")
        msg2 = LLMMessage(role=Role.assistant, content="B")
        msg3 = LLMMessage(role=Role.assistant, content="C")

        result = msg1 + msg2 + msg3

        assert result.message_id == msg1.message_id
        assert result.content == "ABC"


class TestEventMessageId:
    def test_user_message_event_has_message_id(self) -> None:
        event = UserMessageEvent(content="Hello", message_id="user-msg-id")
        assert event.message_id == "user-msg-id"
        assert event.content == "Hello"

    def test_assistant_event_has_message_id(self) -> None:
        event = AssistantEvent(content="test", message_id="test-id")
        assert event.message_id == "test-id"

    def test_assistant_event_message_id_optional(self) -> None:
        event = AssistantEvent(content="test")
        assert event.message_id is None

    def test_reasoning_event_has_message_id(self) -> None:
        event = ReasoningEvent(content="thinking...", message_id="reason-id")
        assert event.message_id == "reason-id"

    def test_reasoning_event_message_id_optional(self) -> None:
        event = ReasoningEvent(content="thinking...")
        assert event.message_id is None

    def test_assistant_event_add_preserves_message_id(self) -> None:
        event1 = AssistantEvent(content="Hello", message_id="first-id")
        event2 = AssistantEvent(content=" world", message_id="second-id")

        result = event1 + event2

        assert result.message_id == "first-id"
        assert result.content == "Hello world"


class TestMessageIdExcludedFromAPI:
    def test_message_id_excluded_with_exclude_param(self) -> None:
        msg = LLMMessage(role=Role.user, content="Hello")
        dumped = msg.model_dump(exclude_none=True, exclude={"message_id"})

        assert "message_id" not in dumped
        assert dumped["role"] == "user"
        assert dumped["content"] == "Hello"

    def test_message_id_included_in_normal_dump(self) -> None:
        msg = LLMMessage(role=Role.user, content="Hello")
        dumped = msg.model_dump(exclude_none=True)

        assert "message_id" in dumped
        assert dumped["message_id"] == msg.message_id


class TestMessageIdInLogs:
    def test_message_id_in_json_dump(self) -> None:
        msg = LLMMessage(role=Role.assistant, content="Response")
        dumped = msg.model_dump(exclude_none=True)

        json_str = json.dumps(dumped)
        loaded = json.loads(json_str)

        assert "message_id" in loaded
        assert loaded["message_id"] == msg.message_id

    def test_message_id_roundtrip(self) -> None:
        original = LLMMessage(role=Role.user, content="Test")
        original_id = original.message_id

        dumped = original.model_dump(exclude_none=True)
        restored = LLMMessage.model_validate(dumped)

        assert restored.message_id == original_id

    def test_tool_message_id_none_in_json(self) -> None:
        msg = LLMMessage(role=Role.tool, content="result", tool_call_id="tc_1")
        dumped = msg.model_dump(exclude_none=True)

        assert "message_id" not in dumped
