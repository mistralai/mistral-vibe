from __future__ import annotations

from textual import events
from textual.message import Message

from vibe.cli.textual_ui.widgets.collapsible import ClickWithoutDragMixin
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic

TODO_STATUS_ROW_ID = "todo-status-row"


# CSS caps this at one row: it sits above the input, which growing would shift.
class TodoStatusRow(ClickWithoutDragMixin, NoMarkupStatic):
    class Activated(Message):
        pass

    def __init__(self) -> None:
        super().__init__("", id=TODO_STATUS_ROW_ID)
        self.display = False

    def set_summary(self, summary: str | None) -> None:
        self.display = summary is not None
        if summary is not None:
            self.update(summary)

    def on_click(self, event: events.Click) -> None:
        if self._click_is_passive(event):
            return
        event.stop()
        self.post_message(self.Activated())
