from __future__ import annotations

from vibe.cli.textual_ui.widgets.status_message import StatusMessage


class ReloadConfigMessage(StatusMessage):
    """Spinner shown in the conversation while ``/reload`` is in progress.

    Follows the same pattern as ``CompactMessage``: a ``StatusMessage`` that
    shows a spinner with loading text while spinning, then settles to a
    completion or error message.
    """

    def __init__(self) -> None:
        super().__init__()
        self.add_class("reload-config-message")
        self.error_message: str | None = None

    def get_content(self) -> str:
        if self._is_spinning:
            return "Reloading configuration..."

        if self.error_message:
            return f"Failed to reload config: {self.error_message}"

        return "Configuration reloaded (includes agent instructions and skills)."

    def set_complete(self) -> None:
        self.stop_spinning(success=True)

    def set_error(self, error_message: str) -> None:
        self.error_message = error_message
        self.stop_spinning(success=False)
