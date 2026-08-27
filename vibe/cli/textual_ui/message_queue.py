from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING
from uuid import uuid4

from textual.widget import Widget

from vibe.app_server.models import PreparedPrompt
from vibe.cli.textual_ui.queue_kinds import QueuedItemKind
from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.messages import (
    BashOutputMessage,
    ErrorMessage,
    QueueHeaderMessage,
    SlashCommandMessage,
    UserMessage,
)
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.app_server.config import ModelConfigView
    from vibe.cli.commands import Command


async def _noop_async() -> None:
    pass


@dataclass(frozen=True, slots=True)
class QueuedItem:
    kind: QueuedItemKind
    content: str
    skill_name: str | None = None
    prepared_prompt: PreparedPrompt | None = None
    # When set, content is cosmetic (display only) and the callback is what
    # actually executes at drain time. When None, content is replayed via
    # _dispatch_idle_input.
    command_payload: Callable[[], Awaitable[None]] | None = None
    # Invoked if this item is discarded before it drains (e.g. via Ctrl+C
    # pop_last) so the caller can revert any speculative state it applied at
    # enqueue time and clear its pending flag. Without this the payload's
    # finally never runs, so the speculative change would stay applied but
    # unpersisted. Defaults to a noop so callers that have nothing to revert
    # need not pass anything.
    on_discard: Callable[[], Awaitable[None]] = _noop_async
    # Set on lifecycle commands that reset the conversation (e.g. /clear). At
    # drain time the queue drops any prompts queued before such a command
    # instead of running an LLM turn on the widgets the command tears down.
    flushes_pending: bool = False


@dataclass(slots=True)
class MessageQueue:
    _items: list[QueuedItem] = field(default_factory=list)
    _paused: bool = False

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    @property
    def items(self) -> list[QueuedItem]:
        return list(self._items)

    @property
    def paused(self) -> bool:
        return self._paused

    def append_prompt(
        self,
        content: str,
        *,
        skill_name: str | None = None,
        prepared_prompt: PreparedPrompt | None = None,
    ) -> None:
        self._items.append(
            QueuedItem(
                QueuedItemKind.PROMPT,
                content,
                skill_name,
                prepared_prompt=prepared_prompt,
            )
        )

    def append_bash(self, content: str) -> None:
        self._items.append(QueuedItem(QueuedItemKind.BASH, content))

    def append_command(
        self,
        content: str,
        *,
        command_payload: Callable[[], Awaitable[None]] | None = None,
        on_discard: Callable[[], Awaitable[None]] | None = None,
        flushes_pending: bool = False,
    ) -> None:
        self._items.append(
            QueuedItem(
                QueuedItemKind.COMMAND,
                content,
                command_payload=command_payload,
                on_discard=on_discard or _noop_async,
                flushes_pending=flushes_pending,
            )
        )

    def prepend_prompts(self, items: list[QueuedItem]) -> None:
        if not items:
            return
        self._items[:0] = items

    def pop_last(self) -> QueuedItem | None:
        if not self._items:
            return None
        item = self._items.pop()
        if not self._items:
            self._paused = False
        return item

    def pop_first(self) -> QueuedItem | None:
        if not self._items:
            return None
        return self._items.pop(0)

    def pop_at(self, index: int) -> QueuedItem | None:
        if index < 0 or index >= len(self._items):
            return None
        item = self._items.pop(index)
        if not self._items:
            self._paused = False
        return item

    def update_prompt_item(
        self, index: int, content: str, *, prepared_prompt: PreparedPrompt | None = None
    ) -> None:
        old = self._items[index]
        self._items[index] = replace(
            old, content=content, prepared_prompt=prepared_prompt
        )

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def clear(self) -> None:
        self._items.clear()
        self._paused = False


@dataclass(frozen=True)
class QueuePorts:
    """Callbacks the controller uses to reach back into the app.

    Everything the drain engine needs that only ``VibeApp`` can provide is
    funnelled through here, so the controller never touches app internals
    directly. The app keeps ownership of the things it must (the agent task
    handle, the loading widget, the remote/feedback managers).
    """

    mount_and_scroll: Callable[..., Awaitable[None]]
    agent_running: Callable[[], bool]
    bash_task: Callable[[], asyncio.Task | None]
    active_model: Callable[[], ModelConfigView | None]
    remove_loading_widget: Callable[[], Awaitable[None]]
    set_loading_queue_count: Callable[[int], None]
    inject_queued_prompt: Callable[..., Awaitable[None]]
    start_agent_turn: Callable[..., asyncio.Task]
    await_agent_turn: Callable[[], Awaitable[None]]
    run_bash: Callable[..., asyncio.Task]
    run_command: Callable[[str, Callable[[], Awaitable[None]] | None], Awaitable[None]]
    maybe_show_feedback_bar: Callable[[], Awaitable[None]]
    send_skill_telemetry: Callable[[str | None], None]
    # Awaited after a queued command runs, in case it opened a picker (e.g.
    # /mcp, /resume) that must block the drain until the user dismisses it.
    # No-op when no picker is open, so commands that don't open a picker
    # return immediately.
    await_input_app: Callable[[], Awaitable[None]] = _noop_async


@dataclass(slots=True)
class _Pending:
    item: QueuedItem
    widget: UserMessage


class QueueController:
    """Owns the queued-input lifecycle: data, pending widgets, header, drain.

    ``MessageQueue`` stays a pure data structure; this controller keeps the
    parallel list of pending widgets in lockstep with it, manages the header
    widget, and runs the drain engine that turns queued items into real turns.
    """

    def __init__(self, ports: QueuePorts) -> None:
        self._ports = ports
        self._queue = MessageQueue()
        self._widgets: list[Widget] = []
        self._header: QueueHeaderMessage | None = None
        self._drain_task: asyncio.Task | None = None
        self._drain_enabled = True

    @property
    def queue(self) -> MessageQueue:
        return self._queue

    @property
    def header(self) -> QueueHeaderMessage | None:
        return self._header

    def __bool__(self) -> bool:
        return bool(self._queue)

    def __len__(self) -> int:
        return len(self._queue)

    # -- pin target (used by the app's _mount_and_scroll) ------------------

    def pin_target(self, messages_area: Widget) -> Widget | None:
        target: Widget | None = self._header
        if target is None and self._widgets:
            target = self._widgets[0]
        if target is not None and target.parent is messages_area:
            return target
        return None

    def _last_queue_anchor(self) -> Widget | None:
        if self._widgets:
            return self._widgets[-1]
        return self._header

    # -- quit / count helpers --------------------------------------------

    def quit_warning_extra(self) -> str:
        if not self._queue:
            return ""
        n = len(self._queue)
        plural = "s" if n != 1 else ""
        return f"{n} queued message{plural} will be discarded"

    def _push_loading_queue_count(self) -> None:
        self._ports.set_loading_queue_count(len(self._queue))

    def notify_busy_changed(self) -> None:
        self._push_loading_queue_count()

    # -- enqueue ----------------------------------------------------------

    async def enqueue_prompt(
        self,
        content: str,
        *,
        skill_name: str | None = None,
        prepared_prompt: PreparedPrompt | None = None,
    ) -> None:
        self._queue.append_prompt(
            content, skill_name=skill_name, prepared_prompt=prepared_prompt
        )
        await self._ensure_header()
        images = prepared_prompt.images if prepared_prompt is not None else []
        widget = UserMessage(content, pending=True, images=images or None)
        anchor = self._last_queue_anchor()
        self._widgets.append(widget)
        await self._ports.mount_and_scroll(widget, after=anchor)
        self._push_loading_queue_count()
        self.start_drain_if_needed()

    async def enqueue_bash(self, content: str, workdir: str) -> None:
        self._queue.append_bash(content)
        await self._ensure_header()
        widget = BashOutputMessage(content, workdir, pending=True)
        widget.set_queued(True)
        anchor = self._last_queue_anchor()
        self._widgets.append(widget)
        await self._ports.mount_and_scroll(widget, after=anchor)
        self._push_loading_queue_count()
        self.start_drain_if_needed()

    async def enqueue_command(
        self,
        content: str,
        *,
        command_payload: Callable[[], Awaitable[None]] | None = None,
        on_discard: Callable[[], Awaitable[None]] | None = None,
        flushes_pending: bool = False,
    ) -> None:
        self._queue.append_command(
            content,
            command_payload=command_payload,
            on_discard=on_discard,
            flushes_pending=flushes_pending,
        )
        await self._ensure_header()
        widget = SlashCommandMessage(content, pending=True)
        anchor = self._last_queue_anchor()
        self._widgets.append(widget)
        await self._ports.mount_and_scroll(widget, after=anchor)
        self._push_loading_queue_count()
        self.start_drain_if_needed()

    async def pop_last(self) -> bool:
        item = self._queue.pop_last()
        if item is None:
            return False
        await item.on_discard()
        widget = self._widgets.pop() if self._widgets else None
        if widget is not None:
            await widget.remove()
        await self._remove_header_if_empty()
        self._push_loading_queue_count()
        return True

    def prompt_item_texts(self) -> list[tuple[int, str]]:
        """Return (queue_index, content) for PROMPT items, in queue order."""
        return [
            (i, item.content)
            for i, item in enumerate(self._queue._items)
            if item.kind == QueuedItemKind.PROMPT
        ]

    def queue_item_texts(self) -> list[tuple[int, str]]:
        """Return (queue_index, content) for all items, in queue order."""
        return [(i, item.content) for i, item in enumerate(self._queue._items)]

    def queue_items(self) -> list[tuple[int, QueuedItemKind, str]]:
        """Return (queue_index, kind, content) for all items, in queue order."""
        return [
            (i, item.kind, item.content) for i, item in enumerate(self._queue._items)
        ]

    @property
    def widgets(self) -> list[Widget]:
        return list(self._widgets)

    async def pop_at(self, index: int) -> bool:
        item = self._queue.pop_at(index)
        if item is None:
            return False
        await item.on_discard()
        if index < len(self._widgets):
            widget = self._widgets.pop(index)
            await widget.remove()
        await self._remove_header_if_empty()
        self._push_loading_queue_count()
        return True

    async def update_prompt_item(
        self,
        queue_index: int,
        content: str,
        *,
        prepared_prompt: PreparedPrompt | None = None,
    ) -> None:
        self._queue.update_prompt_item(
            queue_index, content, prepared_prompt=prepared_prompt
        )
        widget = self._widgets[queue_index]
        if isinstance(widget, UserMessage):
            widget.update_content(content)

    async def update_item(
        self,
        queue_index: int,
        content: str,
        *,
        prepared_prompt: PreparedPrompt | None = None,
    ) -> None:
        """In-place edit of a queued item, refreshing its on-screen widget.

        Prompts re-prepare mentions/images; bash commands refresh the rendered
        command line. The widget list and queue positions are unchanged.
        """
        item = self._queue._items[queue_index]
        if item.kind == QueuedItemKind.BASH:
            self._queue.update_prompt_item(queue_index, content)
            widget = self._widgets[queue_index]
            if isinstance(widget, BashOutputMessage):
                widget.update_command(content)
            return
        self._queue.update_prompt_item(
            queue_index, content, prepared_prompt=prepared_prompt
        )
        widget = self._widgets[queue_index]
        if isinstance(widget, UserMessage):
            widget.update_content(content)

    # -- header lifecycle -------------------------------------------------

    async def _ensure_header(self) -> None:
        if self._header is not None:
            return
        header = QueueHeaderMessage(paused=self._queue.paused)
        self._header = header
        await self._ports.mount_and_scroll(header)

    async def _remove_header_if_empty(self) -> None:
        if self._queue or self._header is None:
            return
        await self._remove_header()

    async def _remove_header(self) -> None:
        if self._header is None:
            return
        header = self._header
        self._header = None
        await header.remove()

    def set_paused(self, paused: bool) -> None:
        if paused:
            self._queue.pause()
        else:
            self._queue.resume()
        if self._header is not None:
            self._header.set_paused(self._queue.paused)

    # -- drain engine -----------------------------------------------------

    def start_drain_if_needed(self) -> None:
        if not self._drain_enabled:
            return
        if self._drain_task is not None and not self._drain_task.done():
            return
        if not self._queue or self._queue.paused:
            return
        if self._ports.agent_running():
            return
        bash_task = self._ports.bash_task()
        if bash_task is not None and not bash_task.done():
            return
        self._drain_task = asyncio.create_task(self._drain())

    @property
    def draining(self) -> bool:
        return self._drain_task is not None and not self._drain_task.done()

    async def shutdown(self) -> None:
        self._drain_enabled = False
        drain_task = self._drain_task
        if drain_task is None or drain_task.done():
            return
        drain_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await drain_task

    async def _drain(self) -> None:
        try:
            while self._drain_enabled and self._queue and not self._queue.paused:
                # Block while a side-channel picker (e.g. /theme opened while
                # busy) is on screen, so the drain doesn't run a queued turn
                # behind it. Returns immediately when the input app is active.
                await self._ports.await_input_app()
                await self._remove_header()
                pending = await self._consume_until_bash_or_empty()
                if not pending:
                    continue
                if self._queue.paused:
                    self._requeue(pending)
                    continue
                await self._run_pending_as_llm_turn(pending)
        except Exception:
            logger.exception("Queue drain crashed")
        finally:
            self._drain_task = None
            self.notify_busy_changed()
            await self._remove_header_if_empty()

    async def _consume_until_bash_or_empty(self) -> list[_Pending]:
        pending: list[_Pending] = []
        while self._queue and not self._queue.paused:
            item = self._queue.pop_first()
            if item is None:
                break
            widget = self._widgets.pop(0) if self._widgets else None
            if item.kind == QueuedItemKind.BASH:
                # A bash item is a turn boundary: run any preceding prompts as
                # a real LLM turn before executing the command. Injecting them
                # without a turn (the old flush) silently dropped prompts that
                # immediately preceded a bash item — the model never answered.
                if pending:
                    await self._run_pending_as_llm_turn(pending)
                    pending = []
                if widget is not None:
                    await widget.remove()
                if not await self._run_bash(item.content):
                    return []
            elif item.kind == QueuedItemKind.COMMAND:
                # Commands are side effects, not turn boundaries. Keep
                # preceding prompts so they still get an LLM turn, and wait
                # if the command started an agent (e.g. /compact, /retry) or
                # opened a picker (e.g. /mcp, /resume) that must block the drain
                # until the user dismisses it. A flushes_pending command
                # (e.g. /clear) resets the conversation, so drop any prompts
                # queued before it instead of running a turn on the widgets it
                # is about to tear down.
                if item.flushes_pending:
                    pending = []
                elif pending:
                    # Run preceding prompts as a real LLM turn before the
                    # command's side effect. Without this, a queued [/mcp]
                    # following a prompt opens its picker first and the prompt
                    # only runs after the user dismisses it — out of FIFO order.
                    await self._run_pending_as_llm_turn(pending)
                    pending = []
                if widget is not None:
                    await widget.remove()
                await self._ports.run_command(item.content, item.command_payload)
                await self._await_tail_turn()
                await self._ports.await_input_app()
            elif isinstance(widget, UserMessage):
                pending.append(_Pending(item, widget))
        return pending

    def _requeue(self, pending: list[_Pending]) -> None:
        self._queue.prepend_prompts([p.item for p in pending])
        self._widgets[:0] = [p.widget for p in pending]

    async def _run_pending_as_llm_turn(self, pending: list[_Pending]) -> None:
        if not await self._gate_queued_images_for_vision(pending):
            return
        head, tail = pending[:-1], pending[-1]
        for p in head:
            await self._inject_head_item(p.item, p.widget)
            await p.widget.set_pending(False)
        self._link_consecutive_user_messages([p.widget for p in pending])
        await self._run_tail_prompt(tail.item, tail.widget)
        await self._await_tail_turn()

    async def _await_tail_turn(self) -> None:
        try:
            await self._ports.await_agent_turn()
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            self._push_loading_queue_count()

    async def _gate_queued_images_for_vision(self, pending: list[_Pending]) -> bool:
        if not any(
            p.item.prepared_prompt and p.item.prepared_prompt.images for p in pending
        ):
            return True
        active_model = self._ports.active_model()
        if active_model is None or active_model.supports_images:
            return True
        self._requeue(pending)
        self.set_paused(True)
        await self._ensure_header()
        await self._ports.mount_and_scroll(
            ErrorMessage(
                shortcut_hint(
                    f"Model `{active_model.display_name}` does not support images. "
                    f"Switch with /model, then press {shortcut('Enter')} "
                    "to resume the queue."
                ),
                show_border=False,
            )
        )
        return False

    async def _inject_head_item(self, item: QueuedItem, widget: UserMessage) -> None:
        message_id = str(uuid4())
        widget.history_entry_id = message_id
        prepared = item.prepared_prompt
        await self._ports.inject_queued_prompt(
            prepared.prompt_text if prepared is not None else item.content,
            images=prepared.images if prepared is not None else None,
            client_message_id=message_id,
            mention_stats=prepared.mentions if prepared is not None else None,
        )
        self._ports.send_skill_telemetry(item.skill_name)

    async def _run_tail_prompt(self, item: QueuedItem, widget: UserMessage) -> None:
        message_id = str(uuid4())
        widget.history_entry_id = message_id
        await widget.set_pending(False)
        await self._ports.maybe_show_feedback_bar()

        await self._ports.remove_loading_widget()
        self._ports.start_agent_turn(
            item.content,
            prepared_prompt=item.prepared_prompt,
            client_message_id=message_id,
        )
        self._ports.send_skill_telemetry(item.skill_name)
        self.notify_busy_changed()

    async def _run_bash(self, command: str) -> bool:
        bash_task = self._ports.run_bash(command)
        self.notify_busy_changed()
        try:
            await bash_task
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            return False
        return True

    @staticmethod
    def _link_consecutive_user_messages(widgets: list[UserMessage]) -> None:
        for prev, curr in zip(widgets, widgets[1:], strict=False):
            prev.set_show_separator(False)
            curr.set_follows_previous(True)


@dataclass(frozen=True)
class SideChannelPorts:
    """Callbacks for side-channel slash command execution.

    The side channel runs allowlisted slash commands while the agent or bash
    is busy. It does not wait for the agent to become idle — that's the point.
    Commands that need idle (lifecycle ops, config reloads) go to the main
    queue instead. Commands that persist config defer to the main queue via
    a COMMAND item with payload.
    """

    invoke_command: Callable[[str, Command, str, str], Awaitable[bool]]


@dataclass(slots=True)
class SideChannelItem:
    cmd_name: str
    command: Command
    cmd_args: str
    display_text: str


class SideChannelController:
    """Single-slot runner for side-channel slash commands.

    Only one side-channel command runs at a time; new submissions are rejected
    while one is in flight. Does not check ``agent_running`` or ``bash_task`` —
    the whole purpose is concurrency with the agent loop.

    Commands that need to persist config changes enqueue a COMMAND item on
    the main queue with a payload. The main queue drains when idle, so
    persistence never hits CONFLICT.
    """

    def __init__(self, ports: SideChannelPorts) -> None:
        self._ports = ports
        self._task: asyncio.Task | None = None
        self._enabled = True

    def __bool__(self) -> bool:
        return self.draining

    def __len__(self) -> int:
        return 1 if self.draining else 0

    @property
    def draining(self) -> bool:
        return self._task is not None and not self._task.done()

    def enqueue(
        self, cmd_name: str, command: Command, cmd_args: str, display_text: str
    ) -> bool:
        if not self._enabled or self.draining:
            return False
        item = SideChannelItem(cmd_name, command, cmd_args, display_text)
        self._task = asyncio.create_task(self._run(item))
        return True

    async def _run(self, item: SideChannelItem) -> None:
        try:
            await self._ports.invoke_command(
                item.cmd_name, item.command, item.cmd_args, item.display_text
            )
        except Exception:
            logger.exception("Side-channel command failed")
        finally:
            self._task = None

    async def shutdown(self) -> None:
        self._enabled = False
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
