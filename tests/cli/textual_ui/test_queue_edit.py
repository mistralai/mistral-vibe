from __future__ import annotations

import asyncio

import pytest

from vibe.app_server.models import PreparedPrompt
from vibe.cli.textual_ui.message_queue import MessageQueue, QueueController, QueuePorts
from vibe.cli.textual_ui.queue_kinds import QueuedItemKind
from vibe.cli.textual_ui.widgets.messages import BashOutputMessage


def _make_controller() -> QueueController:
    async def noop(*args, **kwargs):
        pass

    def noop_task(*args, **kwargs):
        return asyncio.create_task(noop())

    return QueueController(
        QueuePorts(
            mount_and_scroll=noop,
            agent_running=lambda: False,
            bash_task=lambda: None,
            active_model=lambda: None,
            remove_loading_widget=noop,
            set_loading_queue_count=lambda count: None,
            inject_queued_prompt=noop,
            start_agent_turn=noop_task,
            await_agent_turn=noop,
            run_bash=noop_task,
            run_command=noop,
            maybe_show_feedback_bar=noop,
            send_skill_telemetry=lambda name: None,
        )
    )


def test_update_prompt_item_replaces_content() -> None:
    queue = MessageQueue()
    queue.append_prompt("hello")
    queue.update_prompt_item(0, "world")
    assert queue.items[0].content == "world"
    assert queue.items[0].kind == QueuedItemKind.PROMPT


def test_update_prompt_item_preserves_kind_and_skill() -> None:
    queue = MessageQueue()
    queue.append_prompt("hello", skill_name="my-skill")
    queue.update_prompt_item(0, "edited")
    item = queue.items[0]
    assert item.content == "edited"
    assert item.skill_name == "my-skill"
    assert item.kind == QueuedItemKind.PROMPT


def test_update_prompt_item_only_affects_target_index() -> None:
    queue = MessageQueue()
    queue.append_prompt("a")
    queue.append_prompt("b")
    queue.append_prompt("c")
    queue.update_prompt_item(1, "edited")
    assert queue.items[0].content == "a"
    assert queue.items[1].content == "edited"
    assert queue.items[2].content == "c"


def test_prompt_item_texts_returns_only_prompts_with_indices() -> None:
    controller = _make_controller()
    controller._queue.append_prompt("first")
    controller._queue.append_bash("ls")
    controller._queue.append_prompt("second")

    texts = controller.prompt_item_texts()
    assert texts == [(0, "first"), (2, "second")]


def test_prompt_item_texts_empty_queue() -> None:
    controller = _make_controller()
    assert controller.prompt_item_texts() == []


def test_update_prompt_item_updates_prepared_prompt() -> None:
    queue = MessageQueue()
    queue.append_prompt("hello")
    assert queue.items[0].prepared_prompt is None
    prepared = PreparedPrompt(display_text="d", prompt_text="p")
    queue.update_prompt_item(0, "hello", prepared_prompt=prepared)
    assert queue.items[0].prepared_prompt is prepared


def test_pop_at_removes_item_at_index() -> None:
    queue = MessageQueue()
    queue.append_prompt("a")
    queue.append_prompt("b")
    queue.append_prompt("c")
    popped = queue.pop_at(1)
    assert popped is not None
    assert popped.content == "b"
    assert [item.content for item in queue.items] == ["a", "c"]


def test_pop_at_first_item() -> None:
    queue = MessageQueue()
    queue.append_prompt("a")
    queue.append_prompt("b")
    popped = queue.pop_at(0)
    assert popped is not None
    assert popped.content == "a"
    assert [item.content for item in queue.items] == ["b"]


def test_pop_at_last_item_clears_paused() -> None:
    queue = MessageQueue()
    queue.append_prompt("a")
    queue.pause()
    queue.pop_at(0)
    assert not queue.paused


def test_pop_at_out_of_range_returns_none() -> None:
    queue = MessageQueue()
    queue.append_prompt("a")
    assert queue.pop_at(1) is None
    assert queue.pop_at(-1) is None


def test_queue_item_texts_returns_all_items() -> None:
    controller = _make_controller()
    controller._queue.append_prompt("first")
    controller._queue.append_bash("ls")
    controller._queue.append_prompt("second")

    texts = controller.queue_item_texts()
    assert texts == [(0, "first"), (1, "ls"), (2, "second")]


def test_queue_item_texts_empty_queue() -> None:
    controller = _make_controller()
    assert controller.queue_item_texts() == []


def test_controller_widgets_returns_copy() -> None:
    controller = _make_controller()
    widgets1 = controller.widgets
    widgets2 = controller.widgets
    assert widgets1 is not widgets2
    assert widgets1 == widgets2


def test_queue_items_returns_index_kind_content() -> None:
    controller = _make_controller()
    controller._queue.append_prompt("first")
    controller._queue.append_bash("ls")
    controller._queue.append_prompt("second")

    items = controller.queue_items()
    assert items == [
        (0, QueuedItemKind.PROMPT, "first"),
        (1, QueuedItemKind.BASH, "ls"),
        (2, QueuedItemKind.PROMPT, "second"),
    ]


def test_queue_items_empty_queue() -> None:
    controller = _make_controller()
    assert controller.queue_items() == []


@pytest.mark.asyncio
async def test_update_item_bash_updates_command_and_kind() -> None:
    controller = _make_controller()
    await controller.enqueue_bash("ls", "/tmp")

    await controller.update_item(0, "ls -la")

    item = controller._queue.items[0]
    assert item.content == "ls -la"
    assert item.kind == QueuedItemKind.BASH
    widget = controller.widgets[0]
    assert isinstance(widget, BashOutputMessage)
    assert widget._command == "ls -la"


@pytest.mark.asyncio
async def test_update_item_prompt_preserves_kind_with_prepared() -> None:
    controller = _make_controller()
    await controller.enqueue_prompt("hello")

    prepared = PreparedPrompt(display_text="d", prompt_text="p")
    await controller.update_item(0, "edited", prepared_prompt=prepared)

    item = controller._queue.items[0]
    assert item.content == "edited"
    assert item.kind == QueuedItemKind.PROMPT
    assert item.prepared_prompt is prepared


@pytest.mark.asyncio
async def test_queued_bash_prompt_shows_bang_not_spinner() -> None:
    # A bash item queued before mount must render the "!" prompt, not the
    # PULSE spinner square glyph (set_queued runs before compose, so compose
    # must honor _queued over _pending).
    from textual.app import App, ComposeResult

    from vibe.cli.textual_ui.widgets.messages import NonSelectableStatic

    class _MountApp(App):
        def compose(self) -> ComposeResult:
            widget = BashOutputMessage("pwd", "/tmp", pending=True)
            widget.set_queued(True)
            yield widget

    app = _MountApp()
    async with app.run_test() as pilot:
        await pilot.pause(0.05)
        prompt = app.query_one(".bash-prompt", NonSelectableStatic)
        assert str(prompt.content) == "! "
