from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from vibe.app_server.models import (
    ImageAttachment,
    InlineImageSource,
    MentionStats,
    PreparedPrompt,
    PublicQueuedTurn,
    PublicTurnQueue,
)
from vibe.app_server.protocol import SessionTextContentBlock, TurnUserInputEntry
from vibe.cli.commands import Command
from vibe.cli.textual_ui.message_queue import (
    QueueController,
    QueuePorts,
    SideChannelController,
    SideChannelPorts,
)
from vibe.cli.textual_ui.widgets.messages import UserMessage


def _server_turn(
    item_id: str, text: str, message_entry_id: str | None = None
) -> PublicQueuedTurn:
    return PublicQueuedTurn(
        id=item_id,
        created_at=1,
        entries=[
            TurnUserInputEntry(
                entry_id=message_entry_id, content=[SessionTextContentBlock(text=text)]
            )
        ],
    )


async def _noop_async(*_args, **_kwargs) -> None:
    return None


def _image(alias: str) -> ImageAttachment:
    return ImageAttachment(
        source=InlineImageSource(data="Zm9v"), alias=alias, mime_type="image/png"
    )


def _queue_controller(
    *,
    enqueue_turn: Callable[..., Awaitable[PublicQueuedTurn]] | None = None,
    replace_queued_turn: Callable[..., Awaitable[PublicQueuedTurn | None]]
    | None = None,
    current_turn_queue: Callable[[], PublicTurnQueue] = PublicTurnQueue,
    remove_queued_turn: Callable[[str], Awaitable[bool]] | None = None,
    resume_turn_queue: Callable[[], Awaitable[PublicTurnQueue]] | None = None,
    steer_turn: Callable[..., Awaitable[None]] | None = None,
    turn_has_started: Callable[[str], bool] = lambda _queue_item_id: False,
) -> QueueController:
    async def default_enqueue_turn(content: str, **kwargs) -> PublicQueuedTurn:
        return _server_turn(
            f"queue-{content}", content, message_entry_id=kwargs.get("message_entry_id")
        )

    async def default_remove_queued_turn(_queue_item_id: str) -> bool:
        return True

    async def default_replace_queued_turn(
        _queue_item_id: str, _content: str, **_kwargs
    ) -> PublicQueuedTurn | None:
        return None

    async def default_resume_turn_queue() -> PublicTurnQueue:
        return PublicTurnQueue()

    return QueueController(
        QueuePorts(
            mount_and_scroll=_noop_async,
            current_turn_queue=current_turn_queue,
            enqueue_turn=enqueue_turn or default_enqueue_turn,
            replace_queued_turn=replace_queued_turn or default_replace_queued_turn,
            remove_queued_turn=remove_queued_turn or default_remove_queued_turn,
            resume_turn_queue=resume_turn_queue or default_resume_turn_queue,
            steer_turn=steer_turn or _noop_async,
            turn_has_started=turn_has_started,
            set_loading_queue_count=lambda _count: None,
            maybe_show_feedback_bar=_noop_async,
            send_mention_telemetry=lambda _mentions, _message_id: None,
            send_skill_telemetry=lambda _skill_name: None,
        )
    )


@pytest.mark.asyncio
async def test_enqueue_prompt_uses_prepared_prompt_and_delays_prompt_telemetry() -> (
    None
):
    prepared_prompt = PreparedPrompt(
        display_text="display",
        prompt_text="rendered prompt",
        mentions=MentionStats(count=1, context_types={"file": 1}),
    )
    telemetry: dict[str, object] = {}
    controller, calls = _merging_controller(
        send_mention_telemetry=lambda mentions, message_id: telemetry.update(
            mentions=mentions, message_id=message_id
        ),
        send_skill_telemetry=lambda skill_name: telemetry.update(skill_name=skill_name),
    )

    await controller.enqueue_prompt(
        "raw prompt", skill_name="skill", prepared_prompt=prepared_prompt
    )

    widget = controller.widgets[0]
    content, images = calls["enqueue"][0]
    assert content == "rendered prompt"
    assert images is None
    assert isinstance(widget.history_entry_id, str)
    assert telemetry == {}

    await controller.turn_started("item-1")

    assert not widget.pending
    assert telemetry == {
        "mentions": prepared_prompt.mentions,
        "message_id": widget.history_entry_id,
        "skill_name": "skill",
    }


async def _none_queued_turn() -> PublicQueuedTurn | None:
    return None


async def _true() -> bool:
    return True


async def _turn_queue(queue: PublicTurnQueue) -> PublicTurnQueue:
    return queue


def _fake_side_channel_command() -> Command:
    return Command(
        aliases=frozenset(["/test"]),
        description="test",
        handler="_test_handler",
        side_channel=True,
    )


@pytest.mark.asyncio
async def test_side_channel_enqueue_runs_command() -> None:
    calls: list[tuple[str, str, str, str]] = []
    done = asyncio.Event()

    async def invoke(
        cmd_name: str, command: Command, cmd_args: str, display: str
    ) -> bool:
        calls.append((cmd_name, command.handler, cmd_args, display))
        done.set()
        return True

    controller = SideChannelController(SideChannelPorts(invoke_command=invoke))
    assert not controller
    assert len(controller) == 0

    assert controller.enqueue("test", _fake_side_channel_command(), "", "test")
    assert controller
    assert len(controller) == 1

    await asyncio.wait_for(done.wait(), timeout=2.0)
    assert calls == [("test", "_test_handler", "", "test")]
    assert not controller


@pytest.mark.asyncio
async def test_side_channel_rejects_second_when_busy() -> None:
    block = asyncio.Event()

    async def invoke(
        _cmd_name: str, _command: Command, _cmd_args: str, _display: str
    ) -> bool:
        await block.wait()
        return True

    controller = SideChannelController(SideChannelPorts(invoke_command=invoke))
    assert controller.enqueue("a", _fake_side_channel_command(), "", "a")

    assert not controller.enqueue("b", _fake_side_channel_command(), "", "b")
    assert controller
    assert len(controller) == 1

    block.set()
    await asyncio.sleep(0.05)
    assert not controller


@pytest.mark.asyncio
async def test_side_channel_shutdown_cancels_running_command() -> None:
    block = asyncio.Event()

    async def invoke(
        _cmd_name: str, _command: Command, _cmd_args: str, _display: str
    ) -> bool:
        await block.wait()
        return True

    controller = SideChannelController(SideChannelPorts(invoke_command=invoke))
    controller.enqueue("a", _fake_side_channel_command(), "", "a")
    assert controller.draining

    await controller.shutdown()
    assert not controller.draining


@pytest.mark.asyncio
async def test_pop_last_keeps_prompt_promoted_during_remove(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued_turn = _server_turn("queue-1", "queued", "message-1")
    current_queue = PublicTurnQueue(items=[queued_turn])
    started = False
    removed_widgets: list[UserMessage] = []

    async def record_remove(widget: UserMessage) -> None:
        removed_widgets.append(widget)

    monkeypatch.setattr(UserMessage, "remove", record_remove)
    controller = _queue_controller(
        current_turn_queue=lambda: current_queue,
        turn_has_started=lambda _queue_item_id: started,
    )
    await controller.sync_server_queue(current_queue)
    widget = controller.widgets[0]

    started = True
    current_queue = PublicTurnQueue()

    assert not await controller.pop_last()
    assert removed_widgets == []
    assert controller.widgets == [widget]


@pytest.mark.asyncio
async def test_clear_server_queue_removes_stale_pending_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued_turn = _server_turn("queue-1", "queued", "message-1")
    current_queue = PublicTurnQueue(items=[queued_turn])
    removed_widgets: list[UserMessage] = []

    async def record_remove(widget: UserMessage) -> None:
        removed_widgets.append(widget)

    monkeypatch.setattr(UserMessage, "remove", record_remove)
    controller = _queue_controller(current_turn_queue=lambda: current_queue)
    await controller.sync_server_queue(current_queue)
    widget = controller.widgets[0]

    await controller.clear_server_queue()

    assert removed_widgets == [widget]
    assert not controller.has_server_work
    assert not controller


@pytest.mark.asyncio
async def test_sync_server_queue_keeps_prompt_until_turn_started() -> None:
    started = False
    queued_turn = _server_turn("queue-1", "queued", "message-1")
    controller = _queue_controller(turn_has_started=lambda _queue_item_id: started)
    await controller.sync_server_queue(PublicTurnQueue(items=[queued_turn]))
    widget = controller.widgets[0]

    # The item leaves the queue as it promotes; the block stays until the turn
    # actually starts so it does not flicker out and back in.
    started = True
    await controller.sync_server_queue(PublicTurnQueue())
    assert controller.widgets == [widget]

    await controller.turn_started("queue-1")

    assert not widget.pending
    assert not controller.has_server_work


@pytest.mark.asyncio
async def test_update_prompt_does_not_restore_removed_server_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(UserMessage, "remove", _noop_async)
    queued_turn = _server_turn("queue-1", "queued", "message-1")
    current_queue = PublicTurnQueue(items=[queued_turn])

    async def replace_queued_turn(
        _queue_item_id: str, _content: str, **_kwargs
    ) -> PublicQueuedTurn | None:
        nonlocal current_queue
        current_queue = PublicTurnQueue()
        return None

    controller = _queue_controller(
        replace_queued_turn=replace_queued_turn,
        current_turn_queue=lambda: current_queue,
    )
    await controller.sync_server_queue(PublicTurnQueue(items=[queued_turn]))

    assert not await controller.update_prompt(0, "edited")
    assert not controller.has_server_work


@pytest.mark.asyncio
async def test_resume_uses_app_server_queue() -> None:
    resumed = False

    async def resume_turn_queue() -> PublicTurnQueue:
        nonlocal resumed
        resumed = True
        return PublicTurnQueue()

    controller = _queue_controller(resume_turn_queue=resume_turn_queue)
    await controller.sync_server_queue(PublicTurnQueue(paused=True))

    await controller.resume()

    assert resumed
    assert not controller.paused


def _merging_controller(
    *,
    send_mention_telemetry=lambda _mentions, _message_id: None,
    send_skill_telemetry=lambda _skill_name: None,
    steer_turn: Callable[..., Awaitable[None]] | None = None,
    turn_has_started: Callable[[str], bool] = lambda _queue_item_id: False,
) -> tuple[QueueController, dict[str, list]]:
    """A controller whose fake server keeps a single live queue in sync.

    ``enqueue``/``replace``/``remove`` mutate a shared ``PublicTurnQueue`` so the
    controller's post-call ``current_turn_queue`` refresh sees the real state.
    """
    calls: dict[str, list] = {"enqueue": [], "replace": [], "remove": [], "steer": []}
    queue = PublicTurnQueue()

    async def default_steer_turn(content, images=None, message_entry_id=None) -> None:
        calls["steer"].append((content, images, message_entry_id))

    async def enqueue_turn(content: str, **kwargs) -> PublicQueuedTurn:
        nonlocal queue
        calls["enqueue"].append((content, kwargs.get("images")))
        turn = _server_turn(
            "item-1", content, message_entry_id=kwargs.get("message_entry_id")
        )
        queue = PublicTurnQueue(items=[turn])
        return turn

    async def replace_queued_turn(
        queue_item_id: str, content: str, **kwargs
    ) -> PublicQueuedTurn | None:
        nonlocal queue
        calls["replace"].append((queue_item_id, content, kwargs.get("images")))
        turn = _server_turn(
            queue_item_id, content, message_entry_id=kwargs.get("message_entry_id")
        )
        queue = PublicTurnQueue(items=[turn])
        return turn

    async def remove_queued_turn(queue_item_id: str) -> bool:
        nonlocal queue
        calls["remove"].append(queue_item_id)
        queue = PublicTurnQueue(
            items=[item for item in queue.items if item.id != queue_item_id]
        )
        return True

    controller = QueueController(
        QueuePorts(
            mount_and_scroll=_noop_async,
            current_turn_queue=lambda: queue,
            enqueue_turn=enqueue_turn,
            replace_queued_turn=replace_queued_turn,
            remove_queued_turn=remove_queued_turn,
            resume_turn_queue=lambda: _turn_queue(queue),
            steer_turn=steer_turn or default_steer_turn,
            turn_has_started=turn_has_started,
            set_loading_queue_count=lambda _count: None,
            maybe_show_feedback_bar=_noop_async,
            send_mention_telemetry=send_mention_telemetry,
            send_skill_telemetry=send_skill_telemetry,
        )
    )
    return controller, calls


@pytest.mark.asyncio
async def test_second_queued_prompt_merges_via_replace() -> None:
    controller, calls = _merging_controller()

    await controller.enqueue_prompt("first")
    await controller.enqueue_prompt("second")

    assert [content for content, _images in calls["enqueue"]] == ["first"]
    assert [content for _id, content, _images in calls["replace"]] == [
        "first\n\nsecond"
    ]
    # The prompts keep separate widgets but share one merged server item.
    assert [widget.get_content() for widget in controller.widgets] == [
        "first",
        "second",
    ]
    assert len(controller) == 2


@pytest.mark.asyncio
async def test_merged_prompt_combines_images() -> None:
    controller, calls = _merging_controller()
    first = PreparedPrompt(
        display_text="a",
        prompt_text="a",
        mentions=MentionStats(),
        images=[_image("one.png")],
    )
    second = PreparedPrompt(
        display_text="b",
        prompt_text="b",
        mentions=MentionStats(),
        images=[_image("two.png")],
    )

    await controller.enqueue_prompt("a", prepared_prompt=first)
    await controller.enqueue_prompt("b", prepared_prompt=second)

    _id, content, images = calls["replace"][-1]
    assert content == "a\n\nb"
    assert [image.alias for image in images] == ["one.png", "two.png"]


@pytest.mark.asyncio
async def test_turn_started_unpends_block_and_reports_each_prompt() -> None:
    mentions: list = []
    skills: list = []
    controller, _calls = _merging_controller(
        send_mention_telemetry=lambda stats, message_id: mentions.append(message_id),
        send_skill_telemetry=lambda skill_name: skills.append(skill_name),
    )
    prepared = PreparedPrompt(
        display_text="p", prompt_text="p", mentions=MentionStats(count=1)
    )

    await controller.enqueue_prompt("first", prepared_prompt=prepared)
    await controller.enqueue_prompt("second", skill_name="review")
    widgets = controller.widgets

    await controller.turn_started("item-1")

    assert [widget.pending for widget in widgets] == [False, False]
    assert not controller.has_server_work
    assert len(mentions) == 1
    assert skills == [None, "review"]


@pytest.mark.asyncio
async def test_pop_last_peels_newest_merged_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(UserMessage, "remove", _noop_async)
    controller, calls = _merging_controller()

    await controller.enqueue_prompt("first")
    await controller.enqueue_prompt("second")

    assert await controller.pop_last()

    assert [content for _id, content, _images in calls["replace"]][-1] == "first"
    assert [widget.get_content() for widget in controller.widgets] == ["first"]
    assert len(controller) == 1


@pytest.mark.asyncio
async def test_pop_last_removes_item_when_last_prompt_peeled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(UserMessage, "remove", _noop_async)
    controller, calls = _merging_controller()

    await controller.enqueue_prompt("only")

    assert await controller.pop_last()

    assert calls["remove"] == ["item-1"]
    assert not controller
    assert controller.widgets == []


@pytest.mark.asyncio
async def test_pop_at_removes_selected_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(UserMessage, "remove", _noop_async)
    controller, calls = _merging_controller()

    await controller.enqueue_prompt("first")
    await controller.enqueue_prompt("second")

    assert await controller.pop_at(0)

    # Removing one of several prompts re-writes the merged item, it does not
    # remove it.
    assert calls["remove"] == []
    assert [content for _id, content, _images in calls["replace"]][-1] == "second"
    assert [widget.get_content() for widget in controller.widgets] == ["second"]
    assert len(controller) == 1


@pytest.mark.asyncio
async def test_update_prompt_edits_selected_prompt() -> None:
    controller, calls = _merging_controller()

    await controller.enqueue_prompt("first")
    await controller.enqueue_prompt("second")

    assert await controller.update_prompt(0, "edited")

    assert [content for _id, content, _images in calls["replace"]][-1] == (
        "edited\n\nsecond"
    )
    assert [widget.get_content() for widget in controller.widgets] == [
        "edited",
        "second",
    ]
    assert len(controller) == 2


@pytest.mark.asyncio
async def test_optimistic_first_message_stays_separate_from_merged_queue() -> None:
    controller, calls = _merging_controller()

    await controller.enqueue_prompt("optimistic", optimistic_start=True)

    # The idle first message renders immediately (not pending) and owns its
    # server item; it is not part of the mergeable queue.
    assert controller.widgets == []
    assert [content for content, _images in calls["enqueue"]] == ["optimistic"]
    assert calls["replace"] == []


@pytest.mark.asyncio
async def test_sync_restores_single_merged_widget_on_resume() -> None:
    controller, _calls = _merging_controller()

    await controller.sync_server_queue(
        PublicTurnQueue(items=[_server_turn("item-1", "a\n\nb", "entry-1")])
    )

    assert len(controller.widgets) == 1
    assert controller.widgets[0].get_content() == "a\n\nb"


@pytest.mark.asyncio
async def test_append_promoted_mid_replace_requeues_new_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(UserMessage, "remove", _noop_async)
    state = {"started": False, "queue": PublicTurnQueue(), "next": 0}
    calls: dict[str, list] = {"enqueue": [], "replace": []}

    def turn_has_started(item_id: str) -> bool:
        return state["started"] and item_id == "item-0"

    async def enqueue_turn(content: str, **kwargs) -> PublicQueuedTurn:
        item_id = f"item-{state['next']}"
        state["next"] += 1
        calls["enqueue"].append(content)
        turn = _server_turn(item_id, content, kwargs.get("message_entry_id"))
        state["queue"] = PublicTurnQueue(items=[turn])
        return turn

    async def replace_queued_turn(
        _queue_item_id: str, content: str, **_kwargs
    ) -> PublicQueuedTurn | None:
        # Simulate the item promoting during the replace round-trip.
        calls["replace"].append(content)
        state["started"] = True
        state["queue"] = PublicTurnQueue()
        return None

    controller = QueueController(
        QueuePorts(
            mount_and_scroll=_noop_async,
            current_turn_queue=lambda: state["queue"],
            enqueue_turn=enqueue_turn,
            replace_queued_turn=replace_queued_turn,
            remove_queued_turn=lambda _id: _true(),
            resume_turn_queue=lambda: _turn_queue(state["queue"]),
            steer_turn=_noop_async,
            turn_has_started=turn_has_started,
            set_loading_queue_count=lambda _count: None,
            maybe_show_feedback_bar=_noop_async,
            send_mention_telemetry=lambda _mentions, _message_id: None,
            send_skill_telemetry=lambda _skill_name: None,
        )
    )

    await controller.enqueue_prompt("first")
    first_widget = controller.widgets[0]

    await controller.enqueue_prompt("second")

    # "first" promoted mid-replace: it un-pends with its turn, while "second"
    # (never part of that turn) is re-queued as a fresh block, not lost or
    # falsely marked as sent.
    assert not first_widget.pending
    assert [widget.get_content() for widget in controller.widgets] == ["second"]
    assert controller.widgets[0].pending
    assert calls["enqueue"] == ["first", "second"]


@pytest.mark.asyncio
async def test_merged_prompts_render_as_one_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(UserMessage, "remove", _noop_async)
    controller, _calls = _merging_controller()

    await controller.enqueue_prompt("first")
    await controller.enqueue_prompt("second")

    first, second = controller.widgets
    # Consecutive queued prompts group visually: the earlier one drops its
    # separator, the later one marks itself a continuation.
    assert first.has_class("no-separator")
    assert not first.has_class("follows-user")
    assert not second.has_class("no-separator")
    assert second.has_class("follows-user")

    # Removing the newest restores the survivor to a standalone message.
    assert await controller.pop_last()
    (only,) = controller.widgets
    assert not only.has_class("no-separator")
    assert not only.has_class("follows-user")


@pytest.mark.asyncio
async def test_replace_error_removes_uncommitted_widget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    removed: list[UserMessage] = []

    async def record_remove(self: UserMessage) -> None:
        removed.append(self)

    monkeypatch.setattr(UserMessage, "remove", record_remove)
    queue = PublicTurnQueue()

    async def enqueue_turn(content: str, **kwargs) -> PublicQueuedTurn:
        nonlocal queue
        turn = _server_turn("item-1", content, kwargs.get("message_entry_id"))
        queue = PublicTurnQueue(items=[turn])
        return turn

    async def replace_queued_turn(
        _queue_item_id: str, _content: str, **_kwargs
    ) -> PublicQueuedTurn | None:
        raise RuntimeError("replace failed")

    controller = QueueController(
        QueuePorts(
            mount_and_scroll=_noop_async,
            current_turn_queue=lambda: queue,
            enqueue_turn=enqueue_turn,
            replace_queued_turn=replace_queued_turn,
            remove_queued_turn=lambda _id: _true(),
            resume_turn_queue=lambda: _turn_queue(queue),
            steer_turn=_noop_async,
            turn_has_started=lambda _queue_item_id: False,
            set_loading_queue_count=lambda _count: None,
            maybe_show_feedback_bar=_noop_async,
            send_mention_telemetry=lambda _mentions, _message_id: None,
            send_skill_telemetry=lambda _skill_name: None,
        )
    )

    await controller.enqueue_prompt("first")
    first = controller.widgets[0]

    with pytest.raises(RuntimeError, match="replace failed"):
        await controller.enqueue_prompt("second")

    # A failed replace drops the uncommitted widget and leaves the first prompt
    # intact -- no ghost pending message.
    assert [widget.get_content() for widget in removed] == ["second"]
    assert first not in removed
    assert [widget.get_content() for widget in controller.widgets] == ["first"]


@pytest.mark.asyncio
async def test_only_first_merged_widget_has_history_id() -> None:
    controller, _calls = _merging_controller()

    await controller.enqueue_prompt("first")
    await controller.enqueue_prompt("second")
    first, second = controller.widgets

    # The merged item has one server history entry (the first prompt's), so only
    # the first widget carries a rewindable id; later widgets carry none.
    assert first.history_entry_id is not None
    assert second.history_entry_id is None

    await controller.turn_started("item-1")

    assert not first.pending
    assert not second.pending
    assert first.history_entry_id is not None
    assert second.history_entry_id is None


@pytest.mark.asyncio
async def test_pop_at_moves_rewind_id_to_new_first_widget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(UserMessage, "remove", _noop_async)
    controller, _calls = _merging_controller()

    await controller.enqueue_prompt("first")
    await controller.enqueue_prompt("second")
    first, second = controller.widgets
    rewind_id = first.history_entry_id
    assert rewind_id is not None
    assert second.history_entry_id is None

    # Dropping the oldest prompt must move the merged turn's single rewindable
    # id onto the new first widget, not leave the survivor with none.
    assert await controller.pop_at(0)

    (only,) = controller.widgets
    assert only is second
    assert only.history_entry_id == rewind_id

    await controller.turn_started("item-1")

    assert not only.pending
    assert only.history_entry_id == rewind_id


@pytest.mark.asyncio
async def test_steer_pending_steers_merged_prompts_and_unpends() -> None:
    controller, calls = _merging_controller()

    await controller.enqueue_prompt("first")
    await controller.enqueue_prompt("second")
    widgets = controller.widgets

    assert await controller.steer_pending()

    # The merged item is removed from the queue so it cannot also promote as its
    # own turn, and its combined text is steered into the active turn.
    assert calls["remove"] == ["item-1"]
    ((content, images, message_entry_id),) = calls["steer"]
    assert content == "first\n\nsecond"
    assert images is None
    assert isinstance(message_entry_id, str)
    # The queued widgets un-pend to read as sent, and the block is cleared.
    assert [widget.pending for widget in widgets] == [False, False]
    assert controller.widgets == []
    assert not controller.has_server_work


@pytest.mark.asyncio
async def test_steer_pending_combines_images() -> None:
    controller, calls = _merging_controller()
    first = PreparedPrompt(
        display_text="a",
        prompt_text="a",
        mentions=MentionStats(),
        images=[_image("one.png")],
    )
    second = PreparedPrompt(
        display_text="b",
        prompt_text="b",
        mentions=MentionStats(),
        images=[_image("two.png")],
    )

    await controller.enqueue_prompt("a", prepared_prompt=first)
    await controller.enqueue_prompt("b", prepared_prompt=second)

    assert await controller.steer_pending()

    ((content, images, _entry),) = calls["steer"]
    assert content == "a\n\nb"
    assert [image.alias for image in images] == ["one.png", "two.png"]


@pytest.mark.asyncio
async def test_steer_pending_returns_false_when_nothing_queued() -> None:
    controller, calls = _merging_controller()

    assert not await controller.steer_pending()

    assert calls["steer"] == []
    assert calls["remove"] == []


@pytest.mark.asyncio
async def test_steer_pending_finalizes_block_that_already_started() -> None:
    started = {"value": False}
    controller, calls = _merging_controller(
        turn_has_started=lambda _queue_item_id: started["value"]
    )

    await controller.enqueue_prompt("queued")
    widget = controller.widgets[0]

    # The block promoted before the steer arrived: finalize it as a normal turn
    # start instead of steering (and instead of losing it).
    started["value"] = True
    assert not await controller.steer_pending()

    assert calls["steer"] == []
    assert calls["remove"] == []
    assert not widget.pending
    assert not controller.has_server_work


@pytest.mark.asyncio
async def test_steer_pending_reenqueues_when_steer_fails() -> None:
    async def failing_steer(*_args, **_kwargs) -> None:
        raise RuntimeError("steer boom")

    controller, calls = _merging_controller(steer_turn=failing_steer)

    await controller.enqueue_prompt("first")
    await controller.enqueue_prompt("second")

    with pytest.raises(RuntimeError, match="steer boom"):
        await controller.steer_pending()

    # Remove-first: the item is removed then re-enqueued (fresh idempotency
    # key), so the prompts survive and stay queued to promote as the next turn.
    assert calls["remove"] == ["item-1"]
    assert controller.has_removable
    assert [widget.pending for widget in controller.widgets] == [True, True]
    assert [widget.get_content() for widget in controller.widgets] == [
        "first",
        "second",
    ]


@pytest.mark.asyncio
async def test_steer_pending_discards_block_when_reenqueue_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(UserMessage, "remove", _noop_async)
    enqueue_calls = {"n": 0}

    async def enqueue_turn(content: str, **kwargs) -> PublicQueuedTurn:
        enqueue_calls["n"] += 1
        if enqueue_calls["n"] > 1:
            raise RuntimeError("enqueue boom")
        return _server_turn(
            "item-1", content, message_entry_id=kwargs.get("message_entry_id")
        )

    async def failing_steer(*_args, **_kwargs) -> None:
        raise RuntimeError("steer boom")

    controller = _queue_controller(enqueue_turn=enqueue_turn, steer_turn=failing_steer)
    await controller.enqueue_prompt("only")

    # Steer fails, then re-enqueue also fails: the block is dropped (no ghost
    # pending widget tied to a removed server item) and the failure surfaces.
    with pytest.raises(RuntimeError, match="enqueue boom"):
        await controller.steer_pending()

    assert not controller.has_removable
    assert controller.widgets == []


@pytest.mark.asyncio
async def test_steer_pending_reports_prompt_telemetry() -> None:
    mentions: list = []
    skills: list = []
    controller, _calls = _merging_controller(
        send_mention_telemetry=lambda _stats, message_id: mentions.append(message_id),
        send_skill_telemetry=lambda skill_name: skills.append(skill_name),
    )
    prepared = PreparedPrompt(
        display_text="p", prompt_text="p", mentions=MentionStats(count=1)
    )

    await controller.enqueue_prompt("first", prepared_prompt=prepared)
    await controller.enqueue_prompt("second", skill_name="review")

    assert await controller.steer_pending()

    assert len(mentions) == 1
    assert skills == [None, "review"]
