from __future__ import annotations

from dataclasses import dataclass

from textual.widget import Widget

from vibe.cli.textual_ui.widgets.load_more import HistoryLoadMoreMessage
from vibe.core.types import LLMMessage

HISTORY_RESUME_TAIL_MESSAGES = 20
LOAD_MORE_BATCH_SIZE = 10


@dataclass(frozen=True)
class LoadMoreBatch:
    start_index: int
    messages: list[LLMMessage]


class SessionWindowing:
    def __init__(self, load_more_batch_size: int) -> None:
        self.load_more_batch_size = load_more_batch_size
        self._backfill_messages: list[LLMMessage] = []
        self._backfill_cursor = 0

    @property
    def remaining(self) -> int:
        return self._backfill_cursor

    @property
    def has_backfill(self) -> bool:
        return self._backfill_cursor > 0

    def reset(self) -> None:
        self._backfill_messages = []
        self._backfill_cursor = 0

    def set_backfill(self, backfill_messages: list[LLMMessage]) -> None:
        self._backfill_messages = backfill_messages
        self._backfill_cursor = len(backfill_messages)

    def next_load_more_batch(self) -> LoadMoreBatch | None:
        if self._backfill_cursor == 0:
            return None
        start_index = max(self._backfill_cursor - self.load_more_batch_size, 0)
        batch = self._backfill_messages[start_index : self._backfill_cursor]
        self._backfill_cursor = start_index
        if not batch:
            return None
        return LoadMoreBatch(start_index=start_index, messages=batch)

    def recompute_backfill(
        self,
        history_messages: list[LLMMessage],
        visible_indices: list[int],
        visible_history_widgets_count: int,
    ) -> bool:
        if not history_messages:
            self._backfill_messages = []
            self._backfill_cursor = 0
            return False
        if visible_indices:
            oldest_widget = min(visible_indices)
            # _backfill_cursor is the first history index in the loaded window (tail + prepends).
            # Oldest widgets can start *after* that when the tail begins with injected-only slots
            # (no widgets). Using min(visible_indices) alone would shrink the backfill prefix into
            # the already-mounted tail and break load-more batch start_index alignment.
            if oldest_widget > self._backfill_cursor:
                prefix = history_messages[self._backfill_cursor : oldest_widget]
                backfill_end = (
                    self._backfill_cursor
                    if prefix and all(m.injected for m in prefix)
                    else oldest_widget
                )
            else:
                backfill_end = self._backfill_cursor
        else:
            backfill_end = max(len(history_messages) - visible_history_widgets_count, 0)
        backfill_end = min(backfill_end, len(history_messages))
        self._backfill_messages = history_messages[:backfill_end]
        self._backfill_cursor = len(self._backfill_messages)
        return self._backfill_cursor > 0


class HistoryLoadMoreManager:
    def __init__(self) -> None:
        self.widget: HistoryLoadMoreMessage | None = None

    async def show(self, messages_area: Widget, remaining: int) -> None:
        if self.widget is None:
            widget = HistoryLoadMoreMessage()
            await messages_area.mount(widget, before=0)
            self.widget = widget
        self.set_remaining(remaining)

    async def hide(self) -> None:
        if self.widget is None:
            return
        if self.widget.parent:
            await self.widget.remove()
        self.widget = None

    async def set_visible(
        self, messages_area: Widget, *, visible: bool, remaining: int
    ) -> None:
        if visible:
            await self.show(messages_area, remaining)
            return
        await self.hide()

    def set_enabled(self, enabled: bool) -> None:
        if self.widget is None:
            return
        self.widget.set_enabled(enabled)

    def set_remaining(self, remaining: int) -> None:
        if self.widget is None:
            return
        self.widget.set_remaining(remaining)
