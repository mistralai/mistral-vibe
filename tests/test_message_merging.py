from __future__ import annotations

from vibe.core.llm.message_utils import merge_consecutive_user_messages
from vibe.core.types import ImageContentPart, LLMMessage, Role


def test_merge_consecutive_user_messages() -> None:
    messages = [
        LLMMessage(role=Role.system, content="System"),
        LLMMessage(role=Role.user, content="User 1"),
        LLMMessage(role=Role.user, content="User 2"),
        LLMMessage(role=Role.assistant, content="Assistant"),
    ]
    result = merge_consecutive_user_messages(messages)
    assert len(result) == 3
    assert result[1].content == "User 1\n\nUser 2"


def test_preserves_non_consecutive_user_messages() -> None:
    messages = [
        LLMMessage(role=Role.user, content="User 1"),
        LLMMessage(role=Role.assistant, content="Assistant"),
        LLMMessage(role=Role.user, content="User 2"),
    ]
    result = merge_consecutive_user_messages(messages)
    assert len(result) == 3


def test_empty_messages() -> None:
    assert merge_consecutive_user_messages([]) == []


def test_single_message() -> None:
    messages = [LLMMessage(role=Role.user, content="Only one")]
    result = merge_consecutive_user_messages(messages)
    assert len(result) == 1


def test_three_consecutive_user_messages() -> None:
    messages = [
        LLMMessage(role=Role.user, content="A"),
        LLMMessage(role=Role.user, content="B"),
        LLMMessage(role=Role.user, content="C"),
    ]
    result = merge_consecutive_user_messages(messages)
    assert len(result) == 1
    assert result[0].content == "A\n\nB\n\nC"


def test_merge_consecutive_user_messages_preserves_image_parts() -> None:
    first_image = ImageContentPart(image_url="data:image/png;base64,AAAA")
    second_image = ImageContentPart(image_url="data:image/jpeg;base64,BBBB")
    messages = [
        LLMMessage(role=Role.user, content="Describe this", image_parts=[first_image]),
        LLMMessage(role=Role.user, content="and this", image_parts=[second_image]),
    ]

    result = merge_consecutive_user_messages(messages)

    assert len(result) == 1
    assert result[0].content == "Describe this\n\nand this"
    assert result[0].image_parts == [first_image, second_image]
