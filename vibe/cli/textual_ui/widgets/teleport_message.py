from __future__ import annotations

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
            return f"Teleported to Nuage: {self._final_url}"
        return self._status

    def set_status(self, status: str) -> None:
        self._status = status
        self.update_display()

    def set_complete(self, url: str) -> None:
        self._final_url = url
        self.stop_spinning(success=True)

    def set_error(self, error: str) -> None:
        self._error = error
        self.stop_spinning(success=False)
