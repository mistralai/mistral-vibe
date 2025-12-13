from __future__ import annotations

from vibe.cli.textual_ui.widgets.status_message import StatusMessage


class CompactMessage(StatusMessage):
    def __init__(self) -> None:
        super().__init__()
        self.add_class("compact-message")
        self.old_tokens: int | None = None
        self.new_tokens: int | None = None
        self.error_message: str | None = None

    def get_content(self) -> str:
        if self._is_spinning:
            return "Compacting conversation history..."

        if self.error_message:
            return f"Error: {self.error_message}"

        if self.old_tokens is not None and self.new_tokens is not None:
            reduction = self.old_tokens - self.new_tokens
            reduction_pct = (
                (reduction / self.old_tokens * 100) if self.old_tokens > 0 else 0
            )
            return (
                f"Compaction complete: {self.old_tokens:,} → "
                f"{self.new_tokens:,} tokens (-{reduction_pct:.1f}%)"
            )

        return "Compaction complete"

    def set_complete(
        self, old_tokens: int | None = None, new_tokens: int | None = None
    ) -> None:
        self.old_tokens = old_tokens
        self.new_tokens = new_tokens
        self.stop_spinning(success=True)

    def set_error(self, error_message: str) -> None:
        self.error_message = error_message
        self.stop_spinning(success=False)
