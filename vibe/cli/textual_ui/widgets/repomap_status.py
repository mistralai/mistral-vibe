from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static

from vibe.core.types import AgentStats


class RepoMapStatus(Static):
    """Displays the current RepoMap status (token count)."""

    DEFAULT_CSS = """
    RepoMapStatus {
        width: auto;
        padding: 0 1;
        color: $text-muted;
    }
    RepoMapStatus.active {
        color: $success;
    }
    RepoMapStatus.error {
        color: $error;
    }
    """

    def __init__(self) -> None:
        super().__init__("")
        self._tokens = 0
        self._status = "ok"

    @property
    def tokens(self) -> int:
        return self._tokens

    @tokens.setter
    def tokens(self, value: int) -> None:
        if self._tokens != value:
            self._tokens = value
            self.update_display()

    @property
    def status(self) -> str:
        return self._status

    @status.setter
    def status(self, value: str) -> None:
        if self._status != value:
            self._status = value
            self.update_display()

    def update_display(self) -> None:
        if self._status != "ok":
            self.update(f"RepoMap: {self._status}")
            self.add_class("error")
            self.remove_class("active")
        elif self._tokens > 0:
            self.update(f"RepoMap: {self._tokens}")
            self.add_class("active")
            self.remove_class("error")
        else:
            self.update("")
            self.remove_class("active")
            self.remove_class("error")
