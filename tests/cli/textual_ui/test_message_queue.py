from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from vibe.app_server.models import MentionStats, PreparedPrompt
from vibe.cli.commands import Command
from vibe.cli.textual_ui.message_queue import (
    MessageQueue,
    QueueController,
    QueuedItem,
    QueuePorts,
    SideChannelController,
    SideChannelPorts,
)
from vibe.cli.textual_ui.queue_kinds import QueuedItemKind
from vibe.cli.textual_ui.widgets.messages import (
    BashOutputMessage,
    SlashCommandMessage,
    UserMessage,
)


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
            run_command=noop_async,
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

    enqueued = controller.enqueue("test", _fake_side_channel_command(), "", "test")
    assert enqueued
    assert controller
    assert len(controller) == 1

    await asyncio.wait_for(done.wait(), timeout=2.0)
    assert calls == [("test", "_test_handler", "", "test")]
    assert not controller


@pytest.mark.asyncio
async def test_side_channel_rejects_second_when_busy() -> None:
    block = asyncio.Event()

    async def invoke(
        cmd_name: str, command: Command, cmd_args: str, display: str
    ) -> bool:
        await block.wait()
        return True

    controller = SideChannelController(SideChannelPorts(invoke_command=invoke))
    assert controller.enqueue("a", _fake_side_channel_command(), "", "a")

    second = controller.enqueue("b", _fake_side_channel_command(), "", "b")
    assert not second
    assert controller
    assert len(controller) == 1

    block.set()
    await asyncio.sleep(0.05)
    assert not controller


@pytest.mark.asyncio
async def test_side_channel_shutdown_cancels_running_command() -> None:
    block = asyncio.Event()

    async def invoke(
        cmd_name: str, command: Command, cmd_args: str, display: str
    ) -> bool:
        await block.wait()
        return True

    controller = SideChannelController(SideChannelPorts(invoke_command=invoke))
    controller.enqueue("a", _fake_side_channel_command(), "", "a")
    assert controller.draining

    await controller.shutdown()
    assert not controller.draining


async def _noop_invoke(
    cmd_name: str, command: Command, cmd_args: str, display: str
) -> bool:
    return True


async def _noop_async(*args, **kwargs) -> None:
    return None


def _noop_task(*args, **kwargs) -> asyncio.Task[None]:
    return asyncio.create_task(_noop_async())


def _queue_controller(
    *,
    inject_queued_prompt: Callable[..., Awaitable[None]] = _noop_async,
    start_agent_turn: Callable[..., asyncio.Task[None]] = _noop_task,
    await_agent_turn: Callable[[], Awaitable[None]] = _noop_async,
    run_bash: Callable[..., asyncio.Task[None]] = _noop_task,
    run_command: Callable[..., Awaitable[None]] = _noop_async,
    await_input_app: Callable[[], Awaitable[None]] = _noop_async,
) -> QueueController:
    return QueueController(
        QueuePorts(
            mount_and_scroll=_noop_async,
            agent_running=lambda: False,
            bash_task=lambda: None,
            active_model=lambda: None,
            remove_loading_widget=_noop_async,
            set_loading_queue_count=lambda count: None,
            inject_queued_prompt=inject_queued_prompt,
            start_agent_turn=start_agent_turn,
            await_agent_turn=await_agent_turn,
            run_bash=run_bash,
            run_command=run_command,
            maybe_show_feedback_bar=_noop_async,
            send_skill_telemetry=lambda skill_name: None,
            await_input_app=await_input_app,
        )
    )


@pytest.fixture
def unmounted_widget_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    async def noop_remove(self: object) -> None:
        return None

    monkeypatch.setattr(UserMessage, "remove", noop_remove)
    monkeypatch.setattr(BashOutputMessage, "remove", noop_remove)


@pytest.mark.asyncio
async def test_command_does_not_orphan_preceding_prompt(
    unmounted_widget_remove: None,
) -> None:
    injected: list[str] = []
    turns: list[str] = []
    commands: list[str] = []

    async def inject_queued_prompt(content: str, **kwargs) -> None:
        injected.append(content)

    def start_agent_turn(content: str, **kwargs) -> asyncio.Task[None]:
        turns.append(content)
        return _noop_task()

    async def run_command(content: str, payload) -> None:
        commands.append(content)
        if payload is not None:
            await payload()

    controller = _queue_controller(
        inject_queued_prompt=inject_queued_prompt,
        start_agent_turn=start_agent_turn,
        run_command=run_command,
    )
    controller.queue.append_prompt("queued prompt")
    controller.queue.append_command("theme nord")
    controller._widgets = [
        UserMessage("queued prompt", pending=True),
        SlashCommandMessage("theme nord", pending=True),
    ]

    await controller._drain()

    assert commands == ["theme nord"]
    assert injected == []
    assert turns == ["queued prompt"]
    assert not controller.queue


@pytest.mark.asyncio
async def test_command_between_prompts_runs_each_as_own_turn(
    unmounted_widget_remove: None,
) -> None:
    # A queued command is a FIFO turn boundary, not transparent to prompt
    # grouping: the prompt queued before it runs as its own LLM turn before the
    # command's side effect, and the prompt queued after it runs as a separate
    # turn afterwards. The old behaviour kept both prompts as a single turn and
    # let the command jump ahead of the preceding prompt — a FIFO violation for
    # any command that blocks (e.g. /mcp, /compact) or mutates the conversation.
    injected: list[str] = []
    turns: list[str] = []
    commands: list[str] = []

    async def inject_queued_prompt(content: str, **kwargs) -> None:
        injected.append(content)

    def start_agent_turn(content: str, **kwargs) -> asyncio.Task[None]:
        turns.append(content)
        return _noop_task()

    async def run_command(content: str, payload) -> None:
        commands.append(content)

    controller = _queue_controller(
        inject_queued_prompt=inject_queued_prompt,
        start_agent_turn=start_agent_turn,
        run_command=run_command,
    )
    controller.queue.append_prompt("first")
    controller.queue.append_command("mcp")
    controller.queue.append_prompt("second")
    controller._widgets = [
        UserMessage("first", pending=True),
        SlashCommandMessage("mcp", pending=True),
        UserMessage("second", pending=True),
    ]

    await controller._drain()

    assert commands == ["mcp"]
    assert turns == ["first", "second"]
    assert injected == []
    assert not controller.queue


@pytest.mark.asyncio
async def test_command_runs_preceding_prompt_turn_before_side_effect(
    unmounted_widget_remove: None,
) -> None:
    # Regresses a FIFO ordering bug: a prompt queued before a blocking command
    # (e.g. /mcp, which opens a picker the drain blocks on) must run as an LLM
    # turn BEFORE the command's side effect. Otherwise the picker opens first
    # and the prompt only runs after the user dismisses it — out of order.
    order: list[str] = []

    def start_agent_turn(content: str, **kwargs) -> asyncio.Task[None]:
        order.append(f"prompt:{content}")
        return _noop_task()

    async def run_command(content: str, payload) -> None:
        order.append(f"command:{content}")

    controller = _queue_controller(
        start_agent_turn=start_agent_turn, run_command=run_command
    )
    controller.queue.append_prompt("queued prompt")
    controller.queue.append_command("mcp")
    controller._widgets = [
        UserMessage("queued prompt", pending=True),
        SlashCommandMessage("mcp", pending=True),
    ]

    await controller._drain()

    assert order == ["prompt:queued prompt", "command:mcp"]
    assert not controller.queue


@pytest.mark.asyncio
async def test_drain_waits_for_command_agent_before_following_prompt(
    unmounted_widget_remove: None,
) -> None:
    release = asyncio.Event()
    order: list[str] = []
    agent_task: asyncio.Task[None] | None = None

    async def run_command(content: str, payload) -> None:
        nonlocal agent_task

        async def agent() -> None:
            order.append("command-agent-start")
            await release.wait()
            order.append("command-agent-end")

        agent_task = asyncio.create_task(agent())

    async def await_agent_turn() -> None:
        if agent_task is not None:
            await agent_task

    def start_agent_turn(content: str, **kwargs) -> asyncio.Task[None]:
        order.append(f"prompt:{content}")
        return _noop_task()

    controller = _queue_controller(
        run_command=run_command,
        await_agent_turn=await_agent_turn,
        start_agent_turn=start_agent_turn,
    )
    controller.queue.append_command("/compact")
    controller.queue.append_prompt("next")
    controller._widgets = [
        SlashCommandMessage("/compact", pending=True),
        UserMessage("next", pending=True),
    ]

    drain = asyncio.create_task(controller._drain())
    for _ in range(50):
        if agent_task is not None:
            break
        await asyncio.sleep(0)
    assert agent_task is not None
    await asyncio.sleep(0.05)
    assert order == ["command-agent-start"]
    release.set()
    await asyncio.wait_for(drain, timeout=2.0)
    assert order == ["command-agent-start", "command-agent-end", "prompt:next"]
    assert not controller.queue


@pytest.mark.asyncio
async def test_flushes_pending_command_drops_preceding_prompt(
    unmounted_widget_remove: None,
) -> None:
    # A lifecycle command that resets the conversation (e.g. /clear) must not
    # keep a prompt queued before it; otherwise the drain runs an LLM turn on
    # the widgets the command is about to tear down.
    turns: list[str] = []
    commands: list[str] = []

    def start_agent_turn(content: str, **kwargs) -> asyncio.Task[None]:
        turns.append(content)
        return _noop_task()

    async def run_command(content: str, payload) -> None:
        commands.append(content)

    controller = _queue_controller(
        start_agent_turn=start_agent_turn, run_command=run_command
    )
    controller.queue.append_prompt("doomed prompt")
    controller.queue.append_command("/clear", flushes_pending=True)
    controller._widgets = [
        UserMessage("doomed prompt", pending=True),
        SlashCommandMessage("/clear", pending=True),
    ]

    await controller._drain()

    assert commands == ["/clear"]
    assert turns == []
    assert not controller.queue


@pytest.mark.asyncio
async def test_prompt_before_bash_runs_prompt_turn(
    unmounted_widget_remove: None,
) -> None:
    # A prompt immediately followed by a bash item must still get an LLM turn;
    # the bash runs after the model answers. Regresses a bug where the prompts
    # were injected without a turn and silently dropped ("pwd executes but the
    # model never answers the prompt").
    injected: list[str] = []
    turns: list[str] = []
    bashes: list[str] = []

    async def inject_queued_prompt(content: str, **kwargs) -> None:
        injected.append(content)

    def start_agent_turn(content: str, **kwargs) -> asyncio.Task[None]:
        turns.append(content)
        return _noop_task()

    def run_bash(content: str, **kwargs) -> asyncio.Task[None]:
        bashes.append(content)
        return _noop_task()

    controller = _queue_controller(
        inject_queued_prompt=inject_queued_prompt,
        start_agent_turn=start_agent_turn,
        run_bash=run_bash,
    )
    controller.queue.append_prompt("say I love code")
    controller.queue.append_bash("pwd")
    controller._widgets = [
        UserMessage("say I love code", pending=True),
        BashOutputMessage("pwd", "/tmp", pending=True),
    ]

    await controller._drain()

    assert turns == ["say I love code"]
    assert bashes == ["pwd"]
    assert not controller.queue


@pytest.mark.asyncio
async def test_prompts_before_bash_run_as_single_turn(
    unmounted_widget_remove: None,
) -> None:
    # Multiple prompts before a bash combine into one turn (head injected, tail
    # starts the turn), then the bash runs.
    injected: list[str] = []
    turns: list[str] = []
    bashes: list[str] = []

    async def inject_queued_prompt(content: str, **kwargs) -> None:
        injected.append(content)

    def start_agent_turn(content: str, **kwargs) -> asyncio.Task[None]:
        turns.append(content)
        return _noop_task()

    def run_bash(content: str, **kwargs) -> asyncio.Task[None]:
        bashes.append(content)
        return _noop_task()

    controller = _queue_controller(
        inject_queued_prompt=inject_queued_prompt,
        start_agent_turn=start_agent_turn,
        run_bash=run_bash,
    )
    controller.queue.append_prompt("first")
    controller.queue.append_prompt("second")
    controller.queue.append_bash("pwd")
    controller._widgets = [
        UserMessage("first", pending=True),
        UserMessage("second", pending=True),
        BashOutputMessage("pwd", "/tmp", pending=True),
    ]

    await controller._drain()

    assert injected == ["first"]
    assert turns == ["second"]
    assert bashes == ["pwd"]
    assert not controller.queue


@pytest.mark.asyncio
async def test_drain_blocks_on_open_picker_before_running_turn(
    unmounted_widget_remove: None,
) -> None:
    # While a side-channel picker is open, the drain must not run the next
    # queued turn behind it; it resumes once the picker is dismissed.
    ready = asyncio.Event()
    turns: list[str] = []

    async def await_input_app() -> None:
        await ready.wait()

    def start_agent_turn(content: str, **kwargs) -> asyncio.Task[None]:
        turns.append(content)
        return _noop_task()

    controller = _queue_controller(
        await_input_app=await_input_app, start_agent_turn=start_agent_turn
    )
    controller.queue.append_prompt("queued prompt")
    controller._widgets = [UserMessage("queued prompt", pending=True)]

    controller.start_drain_if_needed()
    drain = controller._drain_task
    assert drain is not None
    await asyncio.sleep(0.05)
    assert turns == []
    assert controller.draining

    ready.set()
    await asyncio.wait_for(drain, timeout=2.0)
    assert turns == ["queued prompt"]
    assert not controller.queue


@pytest.mark.asyncio
async def test_run_queued_command_text_only_failure_does_not_raise(
    unmounted_widget_remove: None,
) -> None:
    from unittest.mock import AsyncMock, patch

    from tests.conftest import build_test_vibe_app

    app = build_test_vibe_app()
    async with app.run_test():
        with patch.object(
            app, "_dispatch_idle_input", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            # A failing text-only command (payload is None) must be caught
            # rather than propagating into _drain and aborting the queue.
            await app._run_queued_command("/clear", None)


@pytest.mark.asyncio
async def test_pop_at_fires_on_discard(unmounted_widget_remove: None) -> None:
    # Deleting a queued item via the selection UI (pop_at) must fire its
    # on_discard, the same way pop_last does for Ctrl+C — otherwise a queued
    # theme change deleted from the queue stays visually applied but never
    # reverts.
    discarded: list[str] = []

    async def on_discard() -> None:
        discarded.append("called")

    controller = _queue_controller()
    controller.queue.append_command("theme nord", on_discard=on_discard)
    controller._widgets = [SlashCommandMessage("theme nord", pending=True)]

    assert await controller.pop_at(0)
    assert not controller
    assert discarded == ["called"]


@pytest.mark.asyncio
async def test_pop_at_no_item_does_not_fire_on_discard(
    unmounted_widget_remove: None,
) -> None:
    async def on_discard() -> None:
        raise AssertionError("on_discard must not run for a missing item")

    controller = _queue_controller()
    controller.queue.append_command("theme nord", on_discard=on_discard)
    controller._widgets = [SlashCommandMessage("theme nord", pending=True)]

    # Out-of-range index: nothing removed, callback never runs.
    assert not await controller.pop_at(5)
    assert len(controller) == 1
