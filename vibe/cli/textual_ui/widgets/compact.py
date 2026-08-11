from __future__ import annotations

from vibe.cli.textual_ui.widgets.status_message import StatusMessage


class CompactMessage(StatusMessage):
    def __init__(self) -> None:
        super().__init__()
        self.add_class("compact-message")
        self.error_message: str | None = None

    def get_content(self) -> str:
        if self._is_spinning:
            return "Compacting conversation history..."

        if self.error_message:
            return f"Error: {self.error_message}"

        return "Compaction completed."

    def set_complete(self) -> None:
        self.stop_spinning(success=True)

    def set_error(self, error_message: str) -> None:
        self.error_message = error_message
        self.stop_spinning(success=False)
