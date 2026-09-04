from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from uuid import uuid4

from textual.widget import Widget

from vibe.app_server.models import (
    FileImageSource,
    ImageAttachment,
    InlineImageSource,
    MentionStats,
    PreparedPrompt,
    PublicQueuedTurn,
    PublicTurnQueue,
    SessionImageContentBlock,
    SessionTextContentBlock,
    TurnUserInputEntry,
)
from vibe.cli.textual_ui.widgets.messages import QueueHeaderMessage, UserMessage
from vibe.observability.logging import logger
from vibe.utils.paths import file_uri_to_path

if TYPE_CHECKING:
    from vibe.cli.commands import Command


# Queued prompts merged into one turn join with a blank line, matching how the
# app server renders a restored multi-block user entry.
_MERGE_SEPARATOR = "\n\n"


def _queued_turn_user_entry(queued_turn: PublicQueuedTurn) -> TurnUserInputEntry | None:
    return next(
        (
            entry
            for entry in reversed(queued_turn.entries)
            if isinstance(entry, TurnUserInputEntry)
        ),
        None,
    )


def _queued_image_attachment(block: SessionImageContentBlock) -> ImageAttachment:
    if block.uri.startswith("data:"):
        header, separator, data = block.uri.partition(",")
        if not separator or not header.endswith(";base64"):
            raise ValueError("Queued image URI is not base64 data")
        media_type = block.media_type or header[5:-7]
        if not media_type:
            raise ValueError("Queued image URI has no media type")
        return ImageAttachment(
            source=InlineImageSource(data=data),
            alias=block.alt_text or "image",
            mime_type=media_type,
        )

    parsed = urlparse(block.uri)
    if parsed.scheme not in {"", "file"}:
        raise ValueError(f"Queued image URI is not local: {block.uri!r}")
    path = file_uri_to_path(block.uri) if parsed.scheme == "file" else block.uri
    if block.media_type is None:
        raise ValueError("Queued image URI has no media type")
    return ImageAttachment(
        source=FileImageSource(path=path),
        alias=block.alt_text or Path(path).name or "image",
        mime_type=block.media_type,
    )


@dataclass(frozen=True, slots=True)
class _QueuedPrompt:
    content: str
    skill_name: str | None = None
    prepared_prompt: PreparedPrompt | None = None
    message_entry_id: str | None = None


@dataclass(frozen=True)
class QueuePorts:
    mount_and_scroll: Callable[..., Awaitable[None]]
    current_turn_queue: Callable[[], PublicTurnQueue]
    enqueue_turn: Callable[..., Awaitable[PublicQueuedTurn]]
    replace_queued_turn: Callable[..., Awaitable[PublicQueuedTurn | None]]
    remove_queued_turn: Callable[[str], Awaitable[bool]]
    resume_turn_queue: Callable[[], Awaitable[PublicTurnQueue]]
    steer_turn: Callable[..., Awaitable[None]]
    turn_has_started: Callable[[str], bool]
    set_loading_queue_count: Callable[[int], None]
    maybe_show_feedback_bar: Callable[[], Awaitable[None]]
    send_mention_telemetry: Callable[[MentionStats, str | None], None]
    send_skill_telemetry: Callable[[str | None], None]


@dataclass(slots=True)
class _Pending:
    prompt: _QueuedPrompt
    widget: UserMessage


@dataclass(slots=True)
class _MergedEntry:
    prompt: _QueuedPrompt
    widget: UserMessage


@dataclass(slots=True)
class _MergedTurn:
    """The single server queue item that all busy-time prompts merge into.

    Each queued prompt keeps its own ``UserMessage`` widget so the queue
    selection/edit UX can navigate, edit, and remove prompts individually, but
    they all share one ``item_id``: the CLI folds every prompt into that item
    with ``queue/replace`` so the server promotes them together as one turn.
    ``item_id`` is ``None`` only until the first enqueue returns.
    """

    entries: list[_MergedEntry]
    message_entry_id: str
    item_id: str | None = None


class QueueController:
    """Merge busy-time prompts into one app-server turn.

    The app server keeps a FIFO queue and promotes one item at a time, which the
    desktop app relies on for turn-by-turn delivery. The CLI wants queued prompts
    delivered together, so it keeps a single queue item and folds each new prompt
    into it with ``queue/replace``. The server queue behaviour is unchanged: it
    still promotes exactly one (already-merged) item, and each queued prompt keeps
    its own widget so it stays individually editable and removable.
    """

    def __init__(self, ports: QueuePorts) -> None:
        self._ports = ports
        self._server_queue = PublicTurnQueue()
        self._merged: _MergedTurn | None = None
        # Optimistic prompts sent while idle: rendered as normal messages and
        # promoted immediately, so they own their own queue item and never merge.
        self._optimistic: dict[str, _Pending] = {}
        self._header: QueueHeaderMessage | None = None
        # Serialize mutations so app-server events (sync / turn_started) cannot
        # interleave with an in-flight enqueue or replace.
        self._lock = asyncio.Lock()

    @property
    def header(self) -> QueueHeaderMessage | None:
        return self._header

    @property
    def paused(self) -> bool:
        return self._server_queue.paused

    @property
    def has_server_work(self) -> bool:
        return bool(self)

    @property
    def has_removable(self) -> bool:
        return self._merged is not None and self._merged.item_id is not None

    def __bool__(self) -> bool:
        return self._merged is not None or bool(self._optimistic)

    def __len__(self) -> int:
        merged = len(self._merged.entries) if self._merged is not None else 0
        return merged + len(self._optimistic)

    def pin_target(self, messages_area: Widget) -> Widget | None:
        target: Widget | None = self._header
        if target is None:
            target = self._first_pending_widget()
        if target is not None and target.parent is messages_area:
            return target
        return None

    def quit_warning_extra(self) -> str:
        if not self:
            return ""
        count = len(self)
        plural = "s" if count != 1 else ""
        return f"{count} queued message{plural} will be discarded"

    def notify_busy_changed(self) -> None:
        self._push_loading_queue_count()

    async def enqueue_prompt(
        self,
        content: str,
        *,
        skill_name: str | None = None,
        prepared_prompt: PreparedPrompt | None = None,
        optimistic_start: bool = False,
    ) -> None:
        prompt = _QueuedPrompt(
            content=content,
            skill_name=skill_name,
            prepared_prompt=prepared_prompt,
            message_entry_id=str(uuid4()),
        )
        async with self._lock:
            if optimistic_start and not self:
                await self._enqueue_optimistic(prompt)
            else:
                await self._enqueue_merged(prompt)

    async def sync_server_queue(self, queue: PublicTurnQueue) -> None:
        async with self._lock:
            self._server_queue = queue.model_copy(deep=True)
            # Incremental queue updates never remove the merged block: promotion
            # pops the queue item immediately before ``TurnStarted``, so a missing
            # item does not mean the prompt was discarded. ``turn_started`` clears
            # a promoted block and ``clear_server_queue`` clears a reset one.
            known = set(self._optimistic)
            if self._merged is not None and self._merged.item_id is not None:
                known.add(self._merged.item_id)
            for item in queue.items:
                if item.id in known:
                    continue
                if self._ports.turn_has_started(item.id):
                    continue
                # A queued item we do not track yet: restore it as one block.
                # The CLI only ever creates a single item, so this is the resume
                # path, where the merged item is one combined user entry.
                if self._merged is None:
                    await self._restore_server_item(item)
                    known.add(item.id)

            if self._header is not None:
                self._header.set_paused(self.paused)
            await self._remove_header_if_empty()
            self._push_loading_queue_count()

    async def clear_server_queue(self) -> None:
        """Forget queued prompts after a server-side session reset."""
        async with self._lock:
            widgets: list[UserMessage] = []
            if self._merged is not None:
                widgets.extend(entry.widget for entry in self._merged.entries)
            widgets.extend(pending.widget for pending in self._optimistic.values())

            self._server_queue = PublicTurnQueue()
            self._merged = None
            self._optimistic.clear()

            removed: set[int] = set()
            for widget in widgets:
                if id(widget) in removed:
                    continue
                removed.add(id(widget))
                await widget.remove()

            await self._remove_header_if_empty()
            self._push_loading_queue_count()

    async def turn_started(self, queue_item_id: str | None) -> None:
        async with self._lock:
            await self._turn_started_locked(queue_item_id)

    async def pop_last(self) -> bool:
        async with self._lock:
            merged = self._merged
            if merged is None or merged.item_id is None or not merged.entries:
                return False
            if self._ports.turn_has_started(merged.item_id):
                return False
            return await self._drop_entry_locked(len(merged.entries) - 1)

    async def steer_pending(self) -> bool:
        """Send the queued prompts into the active turn as steering.

        Removes the merged queue item first so it cannot also promote as its own
        turn, then steers its combined text/images into the running turn. If the
        steer fails (e.g. the turn ended between the guard and the steer), the
        block is re-enqueued so it still promotes as the next turn -- nothing is
        delivered twice and nothing is lost. On success the queued widgets
        un-pend so they read as sent messages. Returns True when a steer was
        sent, False when there was nothing steerable (empty queue, or the merged
        block already started).
        """
        async with self._lock:
            merged = self._merged
            if merged is None or merged.item_id is None:
                return False
            if self._ports.turn_has_started(merged.item_id):
                await self._turn_started_locked(merged.item_id)
                return False
            removed = await self._ports.remove_queued_turn(merged.item_id)
            self._server_queue = self._ports.current_turn_queue().model_copy(deep=True)
            if not removed or self._ports.turn_has_started(merged.item_id):
                # Promoted or dropped between the guard and the remove: let the
                # normal turn-start path finalize it instead of steering, so it
                # is never delivered both as a steer and as its own turn.
                if self._ports.turn_has_started(merged.item_id):
                    await self._turn_started_locked(merged.item_id)
                return False
            entries = merged.entries
            try:
                await self._ports.steer_turn(
                    self._server_text(entries),
                    self._server_images(entries) or None,
                    merged.message_entry_id,
                )
            except Exception:
                # The item is removed but the steer failed (e.g. the turn just
                # ended). Put the block back so it still promotes as the next
                # turn; if re-enqueue also fails, drop it rather than leaving
                # ghost pending widgets tied to a removed server item.
                try:
                    await self._reenqueue_after_failed_steer(merged)
                except Exception:
                    await self._discard_merged()
                    self._push_loading_queue_count()
                    raise
                raise
            for entry in entries:
                await entry.widget.set_pending(False)
                self._report_prompt(entry.prompt)
            self._merged = None
            await self._remove_header()
            self._push_loading_queue_count()
            return True

    async def _reenqueue_after_failed_steer(self, merged: _MergedTurn) -> None:
        # Use a fresh idempotency key: the original one (derived from
        # ``message_entry_id``) was consumed by the now-removed item, so reusing
        # it would be rejected. ``message_entry_id`` stays the same for rewind.
        queued_turn = await self._ports.enqueue_turn(
            self._server_text(merged.entries),
            message_entry_id=merged.message_entry_id,
            images=self._server_images(merged.entries) or None,
            idempotency_key=str(uuid4()),
        )
        merged.item_id = queued_turn.id
        self._server_queue = self._ports.current_turn_queue().model_copy(deep=True)

    def queue_item_texts(self) -> list[tuple[int, str]]:
        return [
            (index, entry.prompt.content) for index, entry in enumerate(self._entries())
        ]

    @property
    def widgets(self) -> list[UserMessage]:
        return [entry.widget for entry in self._entries()]

    async def pop_at(self, index: int) -> bool:
        async with self._lock:
            merged = self._merged
            if merged is None or merged.item_id is None:
                return False
            if index < 0 or index >= len(merged.entries):
                return False
            if self._ports.turn_has_started(merged.item_id):
                return False
            return await self._drop_entry_locked(index)

    async def update_prompt(
        self,
        queue_index: int,
        content: str,
        *,
        prepared_prompt: PreparedPrompt | None = None,
    ) -> bool:
        async with self._lock:
            merged = self._merged
            if merged is None or merged.item_id is None:
                return False
            if queue_index < 0 or queue_index >= len(merged.entries):
                return False
            if self._ports.turn_has_started(merged.item_id):
                await self._turn_started_locked(merged.item_id)
                return False
            entry = merged.entries[queue_index]
            edited = _MergedEntry(
                replace(entry.prompt, content=content, prepared_prompt=prepared_prompt),
                entry.widget,
            )
            candidate = list(merged.entries)
            candidate[queue_index] = edited
            if not await self._replace_entries_locked(candidate):
                return False
            entry.widget.update_content(content)
            self._push_loading_queue_count()
            return True

    async def resume(self) -> None:
        async with self._lock:
            if self._server_queue.paused:
                self._server_queue = await self._ports.resume_turn_queue()
            if self._header is not None:
                self._header.set_paused(self.paused)

    # -- internal helpers (all run under ``self._lock``) -------------------

    def _entries(self) -> list[_MergedEntry]:
        return list(self._merged.entries) if self._merged is not None else []

    async def _enqueue_optimistic(self, prompt: _QueuedPrompt) -> None:
        images = (
            prompt.prepared_prompt.images if prompt.prepared_prompt is not None else []
        )
        widget = UserMessage(
            prompt.content,
            pending=False,
            history_entry_id=prompt.message_entry_id,
            images=images or None,
        )
        await self._ports.mount_and_scroll(widget)
        pending = _Pending(prompt, widget)
        try:
            queued_turn = await self._ports.enqueue_turn(
                self._server_text_of(prompt),
                message_entry_id=prompt.message_entry_id,
                images=self._server_images_of(prompt) or None,
            )
        except Exception:
            await widget.remove()
            raise
        self._optimistic[queued_turn.id] = pending
        self._server_queue = self._ports.current_turn_queue().model_copy(deep=True)
        if self._ports.turn_has_started(queued_turn.id):
            await self._turn_started_locked(queued_turn.id)
        self._push_loading_queue_count()

    async def _enqueue_merged(self, prompt: _QueuedPrompt) -> None:
        merged = self._merged
        if (
            merged is not None
            and merged.item_id is not None
            and self._ports.turn_has_started(merged.item_id)
        ):
            # The current block promoted before this prompt arrived; finalize it
            # and start a fresh block for the next turn.
            await self._turn_started_locked(merged.item_id)
            merged = self._merged

        if merged is None:
            await self._create_merged(prompt)
            return

        # Later prompts stay individually editable but share the first prompt's
        # server history entry (the merged item has one entry_id), so they get no
        # history id of their own -- a unique id would be a dangling rewind
        # target once the turn starts.
        widget = self._build_widget(prompt)
        await self._ports.mount_and_scroll(widget, after=self._last_widget())
        # Commit the new prompt to the merged item only if the replace lands.
        # If the item promotes or is removed during the round-trip, the new
        # prompt is not part of that turn, so it must not be un-pended with it.
        candidate = [*merged.entries, _MergedEntry(prompt, widget)]
        try:
            replaced = await self._replace_entries_locked(candidate)
        except Exception:
            # The widget is mounted but not committed to the merged item; drop it
            # so a failed replace does not leave an untracked pending prompt.
            await widget.remove()
            raise
        if replaced:
            self._push_loading_queue_count()
            return
        await widget.remove()
        # The previous block started or was removed mid-replace: queue this
        # prompt as a fresh block so it is not lost.
        await self._create_merged(prompt)

    async def _create_merged(self, prompt: _QueuedPrompt) -> None:
        await self._ensure_header()
        # The first prompt owns the merged item's server history entry, so its
        # widget keeps that id for rewind and history lookups.
        widget = self._build_widget(prompt, history_entry_id=prompt.message_entry_id)
        await self._ports.mount_and_scroll(widget, after=self._header)
        merged = _MergedTurn(
            entries=[_MergedEntry(prompt, widget)],
            message_entry_id=prompt.message_entry_id or str(uuid4()),
        )
        self._merged = merged
        self._relink_merged()
        try:
            queued_turn = await self._ports.enqueue_turn(
                self._server_text(merged.entries),
                message_entry_id=merged.message_entry_id,
                images=self._server_images(merged.entries) or None,
            )
        except Exception:
            await widget.remove()
            self._merged = None
            await self._remove_header_if_empty()
            raise
        merged.item_id = queued_turn.id
        self._server_queue = self._ports.current_turn_queue().model_copy(deep=True)
        if self._ports.turn_has_started(queued_turn.id):
            await self._turn_started_locked(queued_turn.id)
        self._push_loading_queue_count()

    async def _drop_entry_locked(self, index: int) -> bool:
        merged = self._merged
        if merged is None:
            return False
        entry = merged.entries[index]
        if len(merged.entries) == 1:
            return await self._remove_merged_locked()
        candidate = [e for i, e in enumerate(merged.entries) if i != index]
        if not await self._replace_entries_locked(candidate):
            return False
        await entry.widget.remove()
        self._push_loading_queue_count()
        return True

    async def _replace_entries_locked(self, entries: list[_MergedEntry]) -> bool:
        merged = self._merged
        if merged is None or merged.item_id is None:
            return False
        queued_turn = await self._ports.replace_queued_turn(
            merged.item_id,
            self._server_text(entries),
            message_entry_id=merged.message_entry_id,
            images=self._server_images(entries) or None,
        )
        self._server_queue = self._ports.current_turn_queue().model_copy(deep=True)
        if queued_turn is not None:
            merged.entries = entries
            self._relink_merged()
            return True
        # not_found: the item started or was removed.
        if self._ports.turn_has_started(merged.item_id):
            await self._turn_started_locked(merged.item_id)
        else:
            await self._discard_merged()
        self._push_loading_queue_count()
        return False

    async def _remove_merged_locked(self) -> bool:
        merged = self._merged
        if merged is None or merged.item_id is None:
            return False
        removed = await self._ports.remove_queued_turn(merged.item_id)
        self._server_queue = self._ports.current_turn_queue().model_copy(deep=True)
        if not removed or self._ports.turn_has_started(merged.item_id):
            return False
        await self._discard_merged()
        self._push_loading_queue_count()
        return True

    async def _discard_merged(self) -> None:
        merged = self._merged
        if merged is None:
            return
        self._merged = None
        for entry in merged.entries:
            await entry.widget.remove()
        await self._remove_header_if_empty()

    async def _turn_started_locked(self, queue_item_id: str | None) -> None:
        if queue_item_id is None:
            return
        pending = self._optimistic.pop(queue_item_id, None)
        if pending is not None:
            await pending.widget.set_pending(False)
            self._report_prompt(pending.prompt)
            await self._ports.maybe_show_feedback_bar()
            await self._reset_header_position()
            self._push_loading_queue_count()
            return
        merged = self._merged
        if merged is None or merged.item_id != queue_item_id:
            return
        for entry in merged.entries:
            await entry.widget.set_pending(False)
            self._report_prompt(entry.prompt)
        self._merged = None
        await self._ports.maybe_show_feedback_bar()
        await self._reset_header_position()
        self._push_loading_queue_count()

    async def _restore_server_item(self, queued_turn: PublicQueuedTurn) -> None:
        user_entry = _queued_turn_user_entry(queued_turn)
        if user_entry is None:
            return
        content = _MERGE_SEPARATOR.join(
            block.text
            for block in user_entry.content
            if isinstance(block, SessionTextContentBlock)
        )
        images = [
            _queued_image_attachment(block)
            for block in user_entry.content
            if isinstance(block, SessionImageContentBlock)
        ]
        prompt = _QueuedPrompt(content, message_entry_id=user_entry.entry_id)
        widget = UserMessage(
            content,
            pending=True,
            history_entry_id=user_entry.entry_id,
            images=images or None,
        )
        self._merged = _MergedTurn(
            entries=[_MergedEntry(prompt, widget)],
            message_entry_id=user_entry.entry_id or str(uuid4()),
            item_id=queued_turn.id,
        )
        self._relink_merged()
        await self._ensure_header()
        await self._ports.mount_and_scroll(widget, after=self._header)

    def _build_widget(
        self, prompt: _QueuedPrompt, *, history_entry_id: str | None = None
    ) -> UserMessage:
        images = (
            prompt.prepared_prompt.images if prompt.prepared_prompt is not None else []
        )
        return UserMessage(
            prompt.content,
            pending=True,
            history_entry_id=history_entry_id,
            images=images or None,
        )

    def _relink_merged(self) -> None:
        """Render the merged prompts as one visual block.

        Each queued prompt keeps its own widget, but consecutive prompts in the
        same merged turn hide the separator between them and mark themselves as
        continuations, so they read as a single grouped message (matching the
        legacy queued-prompt rendering) even though they stay individually
        selectable, editable, and removable.
        """
        if self._merged is None:
            return
        widgets = [entry.widget for entry in self._merged.entries]
        last = len(widgets) - 1
        # The merged turn has one server history entry. Only the first widget
        # is rewindable; re-assign so popping the oldest prompt does not lose
        # the id (later widgets are mounted with history_entry_id=None).
        rewind_id = self._merged.message_entry_id
        for index, widget in enumerate(widgets):
            widget.set_follows_previous(index > 0)
            widget.set_show_separator(index == last)
            widget.history_entry_id = rewind_id if index == 0 else None

    def _report_prompt(self, prompt: _QueuedPrompt) -> None:
        prepared = prompt.prepared_prompt
        if prepared is not None:
            self._ports.send_mention_telemetry(
                prepared.mentions, prompt.message_entry_id
            )
        self._ports.send_skill_telemetry(prompt.skill_name)

    @staticmethod
    def _server_text_of(prompt: _QueuedPrompt) -> str:
        prepared = prompt.prepared_prompt
        return prepared.prompt_text if prepared is not None else prompt.content

    @staticmethod
    def _server_images_of(prompt: _QueuedPrompt) -> list[ImageAttachment]:
        prepared = prompt.prepared_prompt
        return list(prepared.images) if prepared is not None else []

    def _server_text(self, entries: list[_MergedEntry]) -> str:
        return _MERGE_SEPARATOR.join(
            self._server_text_of(entry.prompt) for entry in entries
        )

    def _server_images(self, entries: list[_MergedEntry]) -> list[ImageAttachment]:
        images: list[ImageAttachment] = []
        for entry in entries:
            images.extend(self._server_images_of(entry.prompt))
        return images

    def _last_widget(self) -> UserMessage | QueueHeaderMessage | None:
        if self._merged is not None and self._merged.entries:
            return self._merged.entries[-1].widget
        return self._header

    def _first_pending_widget(self) -> UserMessage | None:
        if self._merged is not None and self._merged.entries:
            return self._merged.entries[0].widget
        if self._optimistic:
            return next(iter(self._optimistic.values())).widget
        return None

    async def _ensure_header(self) -> None:
        if self._header is not None:
            return
        header = QueueHeaderMessage(paused=self.paused)
        self._header = header
        await self._ports.mount_and_scroll(header)

    async def _reset_header_position(self) -> None:
        await self._remove_header()
        first_pending = self._first_pending_widget()
        if first_pending is None:
            return
        header = QueueHeaderMessage(paused=self.paused)
        self._header = header
        await self._ports.mount_and_scroll(header, before=first_pending)

    async def _remove_header_if_empty(self) -> None:
        if self or self._header is None:
            return
        await self._remove_header()

    async def _remove_header(self) -> None:
        if self._header is None:
            return
        header = self._header
        self._header = None
        if header.parent is not None:
            await header.remove()

    def _push_loading_queue_count(self) -> None:
        self._ports.set_loading_queue_count(len(self))


@dataclass(frozen=True)
class SideChannelPorts:
    """Callbacks for side-channel slash command execution."""

    invoke_command: Callable[[str, Command, str, str], Awaitable[bool]]


@dataclass(slots=True)
class SideChannelItem:
    cmd_name: str
    command: Command
    cmd_args: str
    display_text: str


class SideChannelController:
    """Run one allowlisted slash command alongside the active job."""

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
