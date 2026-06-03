from __future__ import annotations

import pytest

from vibe.cli.textual_ui.message_queue import MessageQueue, QueuedItemKind


def test_empty_queue_is_falsy() -> None:
    queue = MessageQueue()
    assert not queue
    assert len(queue) == 0
    assert not queue.paused


def test_append_prompt_increases_length() -> None:
    queue = MessageQueue()
    queue.append_prompt("hello")
    assert len(queue) == 1
    assert queue.items[0].kind == QueuedItemKind.PROMPT
    assert queue.items[0].content == "hello"


def test_append_bash_marks_kind() -> None:
    queue = MessageQueue()
    queue.append_bash("ls")
    assert queue.items[0].kind == QueuedItemKind.BASH


def test_pop_last_returns_newest() -> None:
    queue = MessageQueue()
    queue.append_prompt("a")
    queue.append_prompt("b")
    queue.append_prompt("c")

    popped = queue.pop_last()
    assert popped is not None
    assert popped.content == "c"
    assert [item.content for item in queue.items] == ["a", "b"]


def test_pop_first_returns_oldest() -> None:
    queue = MessageQueue()
    queue.append_prompt("a")
    queue.append_bash("ls")
    queue.append_prompt("c")

    first = queue.pop_first()
    assert first is not None
    assert first.content == "a"
    assert first.kind == QueuedItemKind.PROMPT

    second = queue.pop_first()
    assert second is not None
    assert second.content == "ls"
    assert second.kind == QueuedItemKind.BASH


def test_pop_from_empty_returns_none() -> None:
    queue = MessageQueue()
    assert queue.pop_first() is None
    assert queue.pop_last() is None


def test_pause_and_resume() -> None:
    queue = MessageQueue()
    queue.append_prompt("a")

    queue.pause()
    assert queue.paused

    queue.resume()
    assert not queue.paused


def test_pause_is_idempotent() -> None:
    queue = MessageQueue()
    calls = []
    queue.set_change_listener(lambda: calls.append(None))
    queue.pause()
    queue.pause()
    assert len(calls) == 1


def test_clear_resets_state() -> None:
    queue = MessageQueue()
    queue.append_prompt("a")
    queue.pause()
    queue.clear()
    assert not queue
    assert not queue.paused


def test_change_listener_fires_on_mutations() -> None:
    queue = MessageQueue()
    calls: list[None] = []
    queue.set_change_listener(lambda: calls.append(None))

    queue.append_prompt("a")
    assert len(calls) == 1

    queue.append_bash("ls")
    assert len(calls) == 2

    queue.pop_last()
    assert len(calls) == 3

    queue.pause()
    assert len(calls) == 4

    queue.resume()
    assert len(calls) == 5


def test_change_listener_can_be_cleared() -> None:
    queue = MessageQueue()
    calls: list[None] = []
    queue.set_change_listener(lambda: calls.append(None))
    queue.set_change_listener(None)
    queue.append_prompt("a")
    assert calls == []


def test_items_returns_copy() -> None:
    queue = MessageQueue()
    queue.append_prompt("a")
    snapshot = queue.items
    queue.append_prompt("b")
    assert len(snapshot) == 1


@pytest.mark.parametrize(
    "kind,content",
    [(QueuedItemKind.PROMPT, "hello world"), (QueuedItemKind.BASH, "echo 'hi'")],
)
def test_item_kinds_round_trip(kind: QueuedItemKind, content: str) -> None:
    queue = MessageQueue()
    if kind == QueuedItemKind.PROMPT:
        queue.append_prompt(content)
    else:
        queue.append_bash(content)
    item = queue.pop_first()
    assert item is not None
    assert item.kind == kind
    assert item.content == content
