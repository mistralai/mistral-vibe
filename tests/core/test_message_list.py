from __future__ import annotations

import threading

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


def test_reset_preserving_system_keeps_system_and_replaces_tail() -> None:
    messages = MessageList(
        initial=[
            LLMMessage(role=Role.system, content="sys"),
            LLMMessage(role=Role.user, content="old user"),
        ]
    )

    tail = [
        LLMMessage(role=Role.user, content="loaded user"),
        LLMMessage(role=Role.assistant, content="loaded assistant"),
    ]
    messages.reset_preserving_system(tail)

    assert [m.role for m in messages] == [Role.system, Role.user, Role.assistant]
    assert messages[0].content == "sys"
    assert messages[1].content == "loaded user"
    assert messages[2].content == "loaded assistant"


def test_reset_preserving_system_without_existing_system() -> None:
    messages = MessageList(initial=[LLMMessage(role=Role.user, content="old")])

    tail = [LLMMessage(role=Role.user, content="loaded")]
    messages.reset_preserving_system(tail)

    assert [m.role for m in messages] == [Role.user]
    assert messages[0].content == "loaded"


def test_reset_preserving_system_is_safe_under_concurrent_prompt_updates() -> None:
    # Regression: on resume the deferred-init thread updates the system prompt
    # while the resume coroutine replaces the message tail. Without shared
    # locking the read-then-replace raced with insert(0) and could raise
    # "list changed size during iteration".
    messages = MessageList(initial=[LLMMessage(role=Role.user, content="old")])
    tail = [LLMMessage(role=Role.user, content="loaded")]
    errors: list[BaseException] = []
    stop = threading.Event()

    def hammer_prompt() -> None:
        try:
            while not stop.is_set():
                messages.update_system_prompt("prompt")
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=hammer_prompt)
    worker.start()
    try:
        for _ in range(2000):
            messages.reset_preserving_system(tail)
    finally:
        stop.set()
        worker.join()

    assert errors == []
    assert messages[0].role == Role.system
    assert messages[-1].content == "loaded"


def test_iteration_is_stable_under_concurrent_prompt_insert() -> None:
    # Regression: during deferred init a reader iterates the list (e.g. a
    # role-filtered count) while the init thread inserts the system prompt at
    # index 0. __iter__ must snapshot so readers never see a duplicate, a
    # miss, or "list changed size during iteration".
    non_system = 50
    messages = MessageList(
        initial=[LLMMessage(role=Role.user, content=f"m{i}") for i in range(non_system)]
    )
    errors: list[BaseException] = []
    stop = threading.Event()

    def toggle_prompt() -> None:
        try:
            while not stop.is_set():
                messages.update_system_prompt("prompt")
                messages.reset([m for m in messages if m.role is not Role.system])
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=toggle_prompt)
    worker.start()
    try:
        for _ in range(2000):
            counted = sum(1 for m in messages if m.role is Role.user)
            assert counted == non_system
    finally:
        stop.set()
        worker.join()

    assert errors == []
