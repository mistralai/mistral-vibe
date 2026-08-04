from __future__ import annotations

import asyncio

import pytest

from vibe.app_server.models import MentionStats, PreparedPrompt
from vibe.cli.textual_ui.message_queue import (
    MessageQueue,
    QueueController,
    QueuedItem,
    QueuedItemKind,
    QueuePorts,
)
from vibe.cli.textual_ui.widgets.messages import UserMessage


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


@pytest.mark.asyncio
async def test_inject_head_item_uses_prepared_prompt() -> None:
    prepared_prompt = PreparedPrompt(
        display_text="display",
        prompt_text="rendered prompt",
        mentions=MentionStats(count=1, context_types={"file": 1}),
    )
    injected: dict[str, object] = {}
    telemetry: dict[str, object] = {}

    async def noop_async(*args, **kwargs) -> None:
        return None

    def noop_task(*args, **kwargs) -> asyncio.Task[None]:
        return asyncio.create_task(noop_async())

    async def inject_queued_prompt(content: str, **kwargs) -> None:
        injected["content"] = content
        injected["images"] = kwargs["images"]
        injected["client_message_id"] = kwargs["client_message_id"]
        injected["mention_stats"] = kwargs["mention_stats"]

    def send_skill_telemetry(skill_name: str | None) -> None:
        telemetry["skill_name"] = skill_name

    controller = QueueController(
        QueuePorts(
            mount_and_scroll=noop_async,
            agent_running=lambda: False,
            bash_task=lambda: None,
            active_model=lambda: None,
            remove_loading_widget=noop_async,
            set_loading_queue_count=lambda count: None,
            inject_queued_prompt=inject_queued_prompt,
            start_agent_turn=noop_task,
            await_agent_turn=noop_async,
            run_bash=noop_task,
            maybe_show_feedback_bar=noop_async,
            send_skill_telemetry=send_skill_telemetry,
        )
    )
    item = QueuedItem(
        QueuedItemKind.PROMPT,
        "raw prompt",
        skill_name="skill",
        prepared_prompt=prepared_prompt,
    )
    widget = UserMessage("raw prompt", pending=True)

    await controller._inject_head_item(item, widget)

    assert widget.history_entry_id == injected["client_message_id"]
    assert injected["content"] == "rendered prompt"
    assert isinstance(injected["client_message_id"], str)
    assert injected["mention_stats"] == prepared_prompt.mentions
    assert telemetry == {"skill_name": "skill"}
