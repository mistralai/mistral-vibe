from __future__ import annotations

import time
from typing import Literal

from textual.app import App
from textual.timer import Timer

from vibe.cli.textual_ui.widgets.path_display import PathDisplay

QuitConfirmKey = Literal["Ctrl+C", "Ctrl+D"]

QUIT_CONFIRM_DELAY = 1.0


class QuitManager:
    def __init__(self, app: App) -> None:
        self._confirm_time: float | None = None
        self._confirm_key: QuitConfirmKey | None = None
        self._confirm_timer: Timer | None = None
        self._app = app

    @property
    def confirm_key(self) -> QuitConfirmKey | None:
        return self._confirm_key

    def is_confirmed(self, key: QuitConfirmKey) -> bool:
        return (
            self._confirm_time is not None
            and self._confirm_key == key
            and (time.monotonic() - self._confirm_time) < QUIT_CONFIRM_DELAY
        )

    def request_confirmation(self, key: QuitConfirmKey) -> None:
        if self._confirm_timer is not None:
            self._confirm_timer.stop()
            self._confirm_timer = None
        self._confirm_time = time.monotonic()
        self._confirm_key = key
        try:
            path_display = self._app.query_one(PathDisplay)
            path_display.update(f"Press {key} again to quit")
        except Exception:
            pass
        self._confirm_timer = self._app.set_timer(
            QUIT_CONFIRM_DELAY, self.cancel_confirmation
        )

    def cancel_confirmation(self) -> None:
        if self._confirm_time is None:
            return
        self._confirm_time = None
        self._confirm_key = None
        if self._confirm_timer:
            self._confirm_timer.stop()
            self._confirm_timer = None
        try:
            path_display = self._app.query_one(PathDisplay)
            path_display.refresh_display()
        except Exception:
            pass
