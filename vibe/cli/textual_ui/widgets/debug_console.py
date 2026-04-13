from __future__ import annotations

import bisect
from collections.abc import Callable

from rich.markup import escape
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.cache import LRUCache
from textual.containers import Vertical
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Static

from vibe.core.log_reader import LogEntry, LogReader
from vibe.core.logger import decode_log_message

LOG_LEVEL_COLORS: dict[str, str] = {
    "DEBUG": "dim",
    "INFO": "cyan",
    "WARNING": "yellow",
    "ERROR": "red",
    "CRITICAL": "bold red",
}

DEFAULT_LOG_PAGE_SIZE = 30


class _LogView(ScrollView, can_focus=True):
    def __init__(
        self,
        load_page: Callable[[], None],
        has_more: Callable[[], bool],
        *,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._lines: list[str] = []
        self._wrap_counts: list[int] = []
        self._wrap_prefix: list[int] = [0]
        self._total_visual: int = 0
        self._cached_width: int = 0
        self._render_line_cache: LRUCache[int, Strip] = LRUCache(1024)
        self._load_page = load_page
        self._has_more = has_more

    def _wrap_markup(self, markup: str) -> int:
        """Return the number of visual lines this markup produces at current width."""
        width = self._cached_width
        if width <= 0:
            return 1

        text = Text.from_markup(markup, style=self.rich_style)

        return len(text.wrap(self.app.console, width))

    def _recompute_prefix(self) -> None:
        self._wrap_prefix = [0]
        for count in self._wrap_counts:
            self._wrap_prefix.append(self._wrap_prefix[-1] + count)
        self._total_visual = self._wrap_prefix[-1]

    def _reflow(self) -> None:
        """Re-wrap all lines at current widget width."""
        width = self.size.width
        if width <= 0:
            return
        self._cached_width = width
        self._render_line_cache.clear()
        self._wrap_counts = [self._wrap_markup(m) for m in self._lines]
        self._recompute_prefix()
        self.virtual_size = Size(width, self._total_visual)

    def write_line(self, markup: str, scroll_end: bool | None = None) -> None:
        at_bottom = self.is_vertical_scroll_end
        width = self._cached_width or self.size.width
        self._cached_width = width

        self._lines.append(markup)
        count = self._wrap_markup(markup)
        self._wrap_counts.append(count)
        self._wrap_prefix.append(self._wrap_prefix[-1] + count)
        self._total_visual += count
        self.virtual_size = Size(width, self._total_visual)

        if scroll_end or (scroll_end is None and at_bottom):
            self.scroll_end(animate=False, immediate=True, x_axis=False)

    def prepend_lines(self, markups: list[str]) -> None:
        if not markups:
            return
        width = self._cached_width or self.size.width
        self._cached_width = width

        new_counts = [self._wrap_markup(m) for m in markups]
        new_visual = sum(new_counts)

        self._lines[0:0] = markups
        self._wrap_counts[0:0] = new_counts
        self._recompute_prefix()
        self._render_line_cache.clear()
        self.virtual_size = Size(width, self._total_visual)
        self.scroll_to(y=self.scroll_y + new_visual, animate=False, immediate=True)

    def render_line(self, y: int) -> Strip:
        _, scroll_y = self.scroll_offset
        abs_y = scroll_y + y
        width = self.size.width
        wrap_width = self._cached_width or width
        rich_style = self.rich_style

        if abs_y >= self._total_visual:
            return Strip.blank(width, rich_style)
        if abs_y in self._render_line_cache:
            return self._render_line_cache[abs_y]

        logical_idx = bisect.bisect_right(self._wrap_prefix, abs_y) - 1
        text = Text.from_markup(self._lines[logical_idx], style=rich_style)
        wrapped = text.wrap(self.app.console, wrap_width)

        base = self._wrap_prefix[logical_idx]
        for i, line_text in enumerate(wrapped):
            strip = Strip(line_text.render(self.app.console), line_text.cell_len)
            strip = strip.crop_extend(0, width, rich_style)
            self._render_line_cache[base + i] = strip

        try:
            return self._render_line_cache[abs_y]
        except KeyError:
            return Strip.blank(width, rich_style)

    def notify_style_update(self) -> None:
        super().notify_style_update()
        self._render_line_cache.clear()

    def on_resize(self, event: events.Resize) -> None:
        if event.size.width != self._cached_width:
            self._reflow()

    def on_click(self, event: events.Click) -> None:
        _, scroll_y = self.scroll_offset
        visual_y = scroll_y + event.y
        logical_idx = bisect.bisect_right(self._wrap_prefix, visual_y) - 1
        if 0 <= logical_idx < len(self._lines):
            plain = Text.from_markup(self._lines[logical_idx]).plain
            self.app.copy_to_clipboard(plain)
            self.app.notify("Copied to clipboard", timeout=2.0)

    def _try_load_previous(self) -> None:
        if not self._has_more() or self.scroll_y > 0:
            return
        self._load_page()

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        super()._on_mouse_scroll_up(event)
        self._try_load_previous()

    def action_scroll_up(self) -> None:
        super().action_scroll_up()
        self._try_load_previous()

    def action_page_up(self) -> None:
        super().action_page_up()
        self._try_load_previous()

    def action_scroll_home(self) -> None:
        super().action_scroll_home()
        self._try_load_previous()


class DebugConsole(Vertical):
    def __init__(
        self, log_reader: LogReader, page_size: int = DEFAULT_LOG_PAGE_SIZE
    ) -> None:
        super().__init__(id="debug-console")
        self._log_reader = log_reader
        self._log_view: _LogView | None = None
        self._cursor: int | None = None
        self._has_more: bool = True
        self._page_size = page_size

    def compose(self) -> ComposeResult:
        yield Static(
            "Debug Console  [dim](ctrl+\\ to close)[/dim]", id="debug-console-header"
        )
        self._log_view = _LogView(
            load_page=self._load_page,
            has_more=lambda: self._has_more and self._cursor is not None,
            id="debug-console-log",
        )
        yield self._log_view

    def on_mount(self) -> None:
        self._fill_viewport()
        self._log_reader.set_consumer(self._on_log_entry)
        self._log_reader.start_watching()

    def on_unmount(self) -> None:
        self._log_reader.set_consumer(None)
        self._log_reader.stop_watching()

    def _load_page(self) -> None:
        if self._log_view is None:
            return
        result = self._log_reader.get_logs(
            limit=self._page_size, offset=self._cursor or 0
        )
        self._cursor = result.cursor
        self._has_more = result.has_more
        markups = [self._format_entry(e) for e in reversed(result.entries)]
        self._log_view.prepend_lines(markups)

    def _fill_viewport(self) -> None:
        """Load enough logs to fill the viewport, then scroll to the bottom."""
        if self._log_view is None or not self._has_more:
            return
        self.call_after_refresh(self._check_and_fill)

    def _check_and_fill(self) -> None:
        if self._log_view is None or not self._has_more:
            return
        if self._log_view.virtual_size.height <= self._log_view.size.height:
            self._load_page()
            self._fill_viewport()
        else:
            self._log_view.scroll_end(animate=False)

    def _on_log_entry(self, entry: LogEntry) -> None:
        self.app.call_from_thread(self._append_log_entry, entry)

    def _append_log_entry(self, entry: LogEntry) -> None:
        if self._log_view is None:
            return
        self._log_view.write_line(self._format_entry(entry))

    @staticmethod
    def _format_entry(entry: LogEntry) -> str:
        color = LOG_LEVEL_COLORS.get(entry.level, "dim")
        ts = entry.timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        message = decode_log_message(entry.message)
        safe_message = escape(message)
        return f"[dim]{ts}[/dim] [{color}]{entry.level:<8}[/{color}] {safe_message}"
