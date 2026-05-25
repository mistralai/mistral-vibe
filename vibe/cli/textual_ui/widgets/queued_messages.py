from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Static

from vibe.cli.textual_ui.message_queue import MessageQueue, QueuedItem, QueuedItemKind
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic


class QueuedMessageItem(Static):
    def __init__(self, item: QueuedItem) -> None:
        super().__init__()
        self.add_class("queued-message-item")
        if item.kind == QueuedItemKind.BASH:
            self.add_class("queued-message-bash")
        self._item = item

    @property
    def item(self) -> QueuedItem:
        return self._item

    def compose(self) -> ComposeResult:
        prefix = "$ " if self._item.kind == QueuedItemKind.BASH else ""
        yield NoMarkupStatic(
            f"{prefix}{self._item.content}", classes="queued-message-content"
        )


class QueuedMessages(Widget):
    DEFAULT_CSS = ""
    ID = "queued-messages"
    ID_LIST = "queued-messages-list"
    ID_HINT = "queued-messages-hint"
    ID_BOX = "queued-messages-box"

    def __init__(self) -> None:
        super().__init__(id=self.ID)
        self._queue: MessageQueue | None = None
        self._is_job_running: bool = False

    def bind(self, queue: MessageQueue) -> None:
        self._queue = queue
        self._queue.set_change_listener(self._on_queue_changed)

    def compose(self) -> ComposeResult:
        with Vertical(id=self.ID_BOX) as box:
            box.border_title = "Queue"
            yield VerticalScroll(id=self.ID_LIST)
            yield NoMarkupStatic("", id=self.ID_HINT)

    def on_mount(self) -> None:
        self._refresh()

    def set_job_running(self, running: bool) -> None:
        if self._is_job_running == running:
            return
        self._is_job_running = running
        self._refresh()

    def _on_queue_changed(self) -> None:
        if self.is_mounted:
            self._refresh()

    def _refresh(self) -> None:
        queue = self._queue
        if queue is None or len(queue) == 0:
            self.display = False
            return

        self.display = True

        try:
            box = self.query_one(f"#{self.ID_BOX}", Vertical)
            list_widget = self.query_one(f"#{self.ID_LIST}", VerticalScroll)
            hint = self.query_one(f"#{self.ID_HINT}", NoMarkupStatic)
        except Exception:
            return

        title = f"Queue ({len(queue)})"
        if queue.paused:
            title = f"{title} — paused"
        box.border_title = title

        existing = list(list_widget.query(QueuedMessageItem))
        for widget in existing:
            widget.remove()
        for item in queue.items:
            list_widget.mount(QueuedMessageItem(item))

        if queue.paused:
            hint.update("Enter send queue • Ctrl+C drop last queued")
        elif self._is_job_running:
            hint.update("Esc cancel job + pause • Ctrl+C drop last queued")
        else:
            hint.update("")
