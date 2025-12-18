from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import App

from vibe.cli.session_selector.screen import SessionSelectorScreen

if TYPE_CHECKING:
    from vibe.core.config import SessionLoggingConfig


class SessionSelectorApp(App[Path | None]):
    """Standalone app for selecting a session to resume."""

    CSS_PATH = "session_selector.tcss"

    def __init__(self, config: SessionLoggingConfig, limit: int = 20) -> None:
        super().__init__()
        self._config = config
        self._limit = limit

    def on_mount(self) -> None:
        screen = SessionSelectorScreen(self._config, self._limit)
        self.install_screen(screen, "selector")
        self.push_screen("selector")


def select_session(config: SessionLoggingConfig, limit: int = 20) -> Path | None:
    """Run the session selector and return the selected session path."""
    app = SessionSelectorApp(config, limit)
    return app.run()
