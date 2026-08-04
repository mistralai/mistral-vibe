from __future__ import annotations

from textual.content import Content
from textual.visual import VisualType
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.links import LinkStatic, link_content
from vibe.cli.textual_ui.widgets.status_message import StatusMessage


class TeleportMessage(StatusMessage):
    def __init__(self) -> None:
        super().__init__()
        self.add_class("teleport-message")
        self._status: str = "Teleporting..."
        self._final_url: str | None = None
        self._error: str | None = None

    def get_content(self) -> str:
        if self._error:
            return f"Teleport failed: {self._error}"
        if self._final_url:
            return f"Teleported to Vibe Code Web: {self._final_url}"
        return self._status

    def _make_text_widget(self) -> Static:
        return LinkStatic("", classes="status-indicator-text")

    def _format_text(self, content: str) -> VisualType:
        if not self._final_url or self._error:
            return content
        return Content("Teleported to ") + link_content(
            "Vibe Code Web", self._final_url
        )

    def set_status(self, status: str) -> None:
        self._status = status
        self.update_display()

    def set_complete(self, url: str) -> None:
        self._final_url = url
        self.stop_spinning(success=True)

    def set_error(self, error: str) -> None:
        self._error = error
        self.stop_spinning(success=False)
