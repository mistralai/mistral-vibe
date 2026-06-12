from __future__ import annotations

import pytest

from vibe.cli.textual_ui.message_queue import MessageQueue, QueuedItem, QueuedItemKind


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


def test_pop_last_resumes_when_queue_becomes_empty() -> None:
    queue = MessageQueue()
    queue.append_prompt("a")
    queue.pause()

    queue.pop_last()

    assert not queue
    assert not queue.paused


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
    queue.pause()
    queue.pause()
    assert queue.paused


def test_clear_resets_state() -> None:
    queue = MessageQueue()
    queue.append_prompt("a")
    queue.pause()
    queue.clear()
    assert not queue
    assert not queue.paused


def test_prepend_prompts_inserts_at_head_preserving_order() -> None:
    queue = MessageQueue()
    queue.append_prompt("x")
    queue.append_prompt("y")
    queue.prepend_prompts([
        QueuedItem(QueuedItemKind.PROMPT, "a"),
        QueuedItem(QueuedItemKind.PROMPT, "b"),
    ])
    assert [item.content for item in queue.items] == ["a", "b", "x", "y"]


def test_prepend_prompts_empty_is_noop() -> None:
    queue = MessageQueue()
    queue.append_prompt("x")
    queue.prepend_prompts([])
    assert [item.content for item in queue.items] == ["x"]


def test_append_prompt_with_skill_name() -> None:
    queue = MessageQueue()
    queue.append_prompt("expanded prompt", skill_name="my-skill")
    item = queue.items[0]
    assert item.skill_name == "my-skill"
    assert item.content == "expanded prompt"


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
