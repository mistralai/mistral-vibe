from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum, auto
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from textual.widget import Widget

from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.messages import (
    BashOutputMessage,
    ErrorMessage,
    QueueHeaderMessage,
    UserMessage,
)
from vibe.core.autocompletion.path_prompt import PathPromptPayload
from vibe.core.logger import logger
from vibe.core.types import ImageAttachment

if TYPE_CHECKING:
    from vibe.core.config import ModelConfig


class QueuedItemKind(StrEnum):
    PROMPT = auto()
    BASH = auto()


@dataclass(frozen=True, slots=True)
class QueuedItem:
    kind: QueuedItemKind
    content: str
    skill_name: str | None = None
    images: list[ImageAttachment] | None = None
    payload: PathPromptPayload | None = None


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
        images: list[ImageAttachment] | None = None,
        payload: PathPromptPayload | None = None,
    ) -> None:
        self._items.append(
            QueuedItem(
                QueuedItemKind.PROMPT,
                content,
                skill_name,
                images=images,
                payload=payload,
            )
        )

    def append_bash(self, content: str) -> None:
        self._items.append(QueuedItem(QueuedItemKind.BASH, content))

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
    active_model: Callable[[], ModelConfig | None]
    remove_loading_widget: Callable[[], Awaitable[None]]
    set_loading_queue_count: Callable[[int], None]
    inject_user_context: Callable[..., Awaitable[None]]
    next_message_index: Callable[[], int]
    start_agent_turn: Callable[..., asyncio.Task]
    await_agent_turn: Callable[[], Awaitable[None]]
    run_bash: Callable[..., asyncio.Task]
    maybe_show_feedback_bar: Callable[[], None]
    send_skill_telemetry: Callable[[str | None], None]
    send_at_mention_telemetry: Callable[[PathPromptPayload, str], None]
    render_payload: Callable[[PathPromptPayload], str]


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
        images: list[ImageAttachment] | None = None,
        payload: PathPromptPayload | None = None,
    ) -> None:
        self._queue.append_prompt(
            content, skill_name=skill_name, images=images, payload=payload
        )
        await self._ensure_header()
        widget = UserMessage(content, pending=True, images=images or None)
        anchor = self._last_queue_anchor()
        self._widgets.append(widget)
        await self._ports.mount_and_scroll(widget, after=anchor)
        self._push_loading_queue_count()

    async def enqueue_bash(self, content: str) -> None:
        self._queue.append_bash(content)
        await self._ensure_header()
        widget = BashOutputMessage(content, str(Path.cwd()), pending=True)
        widget.set_queued(True)
        anchor = self._last_queue_anchor()
        self._widgets.append(widget)
        await self._ports.mount_and_scroll(widget, after=anchor)
        self._push_loading_queue_count()

    async def pop_last(self) -> bool:
        item = self._queue.pop_last()
        if item is None:
            return False
        widget = self._widgets.pop() if self._widgets else None
        if widget is not None:
            await widget.remove()
        await self._remove_header_if_empty()
        self._push_loading_queue_count()
        return True

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
                await self._flush_pending_prompts(pending)
                pending = []
                bash_widget = widget if isinstance(widget, BashOutputMessage) else None
                if not await self._run_bash(item.content, bash_widget):
                    return []
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

    async def _flush_pending_prompts(self, pending: list[_Pending]) -> None:
        if not await self._gate_queued_images_for_vision(pending):
            return
        for p in pending:
            await self._inject_head_item(p.item, p.widget)
            await p.widget.set_pending(False)
        self._link_consecutive_user_messages([p.widget for p in pending])

    async def _gate_queued_images_for_vision(self, pending: list[_Pending]) -> bool:
        if not any(p.item.images for p in pending):
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
                    f"Model `{active_model.alias}` does not support images. "
                    f"Switch with /model, then press {shortcut('Enter')} "
                    "to resume the queue."
                ),
                show_border=False,
            )
        )
        return False

    async def _inject_head_item(self, item: QueuedItem, widget: UserMessage) -> None:
        widget.message_index = self._ports.next_message_index()
        message_id = str(uuid4()) if item.payload is not None else None
        if item.payload is not None:
            rendered = self._ports.render_payload(item.payload)
        else:
            rendered = item.content
        await self._ports.inject_user_context(
            rendered, as_message=True, images=item.images, client_message_id=message_id
        )
        self._ports.send_skill_telemetry(item.skill_name)
        if item.payload is not None and message_id is not None:
            self._ports.send_at_mention_telemetry(item.payload, message_id)

    async def _run_tail_prompt(self, item: QueuedItem, widget: UserMessage) -> None:
        widget.message_index = self._ports.next_message_index()
        await widget.set_pending(False)
        self._ports.maybe_show_feedback_bar()

        await self._ports.remove_loading_widget()
        self._ports.start_agent_turn(
            item.content, prebuilt_images=item.images, prebuilt_payload=item.payload
        )
        self._ports.send_skill_telemetry(item.skill_name)
        self.notify_busy_changed()

    async def _run_bash(self, command: str, widget: BashOutputMessage | None) -> bool:
        if widget is not None:
            widget.set_queued(False)
        bash_task = self._ports.run_bash(command, existing_widget=widget)
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
