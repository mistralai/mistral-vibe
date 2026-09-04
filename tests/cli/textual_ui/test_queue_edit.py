from __future__ import annotations

import pytest

from vibe.app_server.models import PreparedPrompt, PublicQueuedTurn, PublicTurnQueue
from vibe.app_server.protocol import SessionTextContentBlock, TurnUserInputEntry
from vibe.cli.textual_ui.message_queue import QueueController, QueuePorts
from vibe.cli.textual_ui.widgets.messages import UserMessage


def _make_controller() -> QueueController:
    turn_queue = PublicTurnQueue()

    async def noop(*_args, **_kwargs) -> None:
        return None

    async def enqueue_turn(content: str, **kwargs) -> PublicQueuedTurn:
        nonlocal turn_queue
        queued_turn = PublicQueuedTurn(
            id=f"queue-{len(turn_queue.items)}",
            created_at=1,
            entries=[
                TurnUserInputEntry(
                    entry_id=kwargs.get("message_entry_id"),
                    content=[SessionTextContentBlock(text=content)],
                )
            ],
        )
        turn_queue = turn_queue.model_copy(
            update={"items": [*turn_queue.items, queued_turn]}
        )
        return queued_turn

    async def replace_queued_turn(
        queue_item_id: str, content: str, **kwargs
    ) -> PublicQueuedTurn | None:
        nonlocal turn_queue
        updated_items: list[PublicQueuedTurn] = []
        replaced: PublicQueuedTurn | None = None
        for item in turn_queue.items:
            if item.id != queue_item_id:
                updated_items.append(item)
                continue
            replaced = item.model_copy(
                update={
                    "entries": [
                        TurnUserInputEntry(
                            entry_id=kwargs.get("message_entry_id"),
                            content=[SessionTextContentBlock(text=content)],
                        )
                    ]
                }
            )
            updated_items.append(replaced)
        if replaced is None:
            return None
        turn_queue = turn_queue.model_copy(update={"items": updated_items})
        return replaced

    async def remove_queued_turn(queue_item_id: str) -> bool:
        nonlocal turn_queue
        remaining = [item for item in turn_queue.items if item.id != queue_item_id]
        if len(remaining) == len(turn_queue.items):
            return False
        turn_queue = turn_queue.model_copy(update={"items": remaining})
        return True

    async def resume_turn_queue() -> PublicTurnQueue:
        return turn_queue

    return QueueController(
        QueuePorts(
            mount_and_scroll=noop,
            current_turn_queue=lambda: turn_queue,
            enqueue_turn=enqueue_turn,
            replace_queued_turn=replace_queued_turn,
            remove_queued_turn=remove_queued_turn,
            resume_turn_queue=resume_turn_queue,
            steer_turn=noop,
            turn_has_started=lambda _queue_item_id: False,
            set_loading_queue_count=lambda _count: None,
            maybe_show_feedback_bar=noop,
            send_mention_telemetry=lambda _mentions, _message_id: None,
            send_skill_telemetry=lambda _name: None,
        )
    )


@pytest.fixture(autouse=True)
def unmounted_widget_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    async def noop_remove(_self: object) -> None:
        return None

    monkeypatch.setattr(UserMessage, "remove", noop_remove)


def test_queue_item_texts_empty_queue() -> None:
    controller = _make_controller()
    assert controller.queue_item_texts() == []


def test_controller_widgets_returns_copy() -> None:
    controller = _make_controller()
    assert controller.widgets is not controller.widgets
    assert controller.widgets == controller.widgets


@pytest.mark.asyncio
async def test_update_prompt_updates_prepared_prompt() -> None:
    controller = _make_controller()
    await controller.enqueue_prompt("hello")

    prepared = PreparedPrompt(display_text="display", prompt_text="rendered")
    assert await controller.update_prompt(0, "edited", prepared_prompt=prepared)

    assert controller._merged is not None
    entry = controller._merged.entries[0]
    assert entry.prompt.content == "edited"
    assert entry.prompt.prepared_prompt is prepared


@pytest.mark.asyncio
async def test_update_prompt_preserves_server_fifo_order() -> None:
    controller = _make_controller()
    await controller.enqueue_prompt("A")
    await controller.enqueue_prompt("B")
    await controller.enqueue_prompt("C")
    original_ids = [item.id for item in controller._server_queue.items]

    assert await controller.update_prompt(0, "A-edited")

    assert [content for _, content in controller.queue_item_texts()] == [
        "A-edited",
        "B",
        "C",
    ]
    assert [item.id for item in controller._server_queue.items] == original_ids
    assert [widget.get_content() for widget in controller.widgets] == [
        "A-edited",
        "B",
        "C",
    ]


@pytest.mark.asyncio
async def test_pop_at_removes_server_prompt() -> None:
    controller = _make_controller()
    await controller.enqueue_prompt("A")
    await controller.enqueue_prompt("B")
    await controller.enqueue_prompt("C")

    assert await controller.pop_at(1)
    assert [content for _, content in controller.queue_item_texts()] == ["A", "C"]


@pytest.mark.asyncio
async def test_pop_at_rejects_out_of_range_index() -> None:
    controller = _make_controller()
    await controller.enqueue_prompt("A")

    assert not await controller.pop_at(-1)
    assert not await controller.pop_at(1)
    assert controller.queue_item_texts() == [(0, "A")]
