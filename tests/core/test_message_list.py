from __future__ import annotations

from vibe.core.types import LLMMessage, MessageList, Role


def test_update_system_prompt_replaces_existing_system_slot() -> None:
    messages = MessageList(
        initial=[
            LLMMessage(role=Role.system, content="old"),
            LLMMessage(role=Role.user, content="hi"),
        ]
    )

    messages.update_system_prompt("new")

    assert len(messages) == 2
    assert messages[0].role == Role.system
    assert messages[0].content == "new"
    assert messages[1].content == "hi"


def test_update_system_prompt_inserts_without_clobbering_when_no_system() -> None:
    messages = MessageList(
        initial=[
            LLMMessage(role=Role.user, content="Hello"),
            LLMMessage(role=Role.assistant, content="Hi there!"),
        ]
    )

    messages.update_system_prompt("system prompt")

    assert len(messages) == 3
    assert messages[0].role == Role.system
    assert messages[1].content == "Hello"
    assert messages[2].content == "Hi there!"


def test_update_system_prompt_inserts_into_empty_list() -> None:
    messages = MessageList()

    messages.update_system_prompt("system prompt")

    assert len(messages) == 1
    assert messages[0].role == Role.system


def test_update_system_prompt_notifies_only_when_requested() -> None:
    observed: list[LLMMessage] = []
    messages = MessageList(observer=observed.append)

    messages.update_system_prompt("silent")
    assert observed == []

    messages.update_system_prompt("loud", notify=True)
    assert len(observed) == 1
    assert observed[0].content == "loud"
