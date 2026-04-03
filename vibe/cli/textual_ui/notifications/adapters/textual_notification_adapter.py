from __future__ import annotations

from collections.abc import Callable
import time

from textual.app import App

from vibe.cli.textual_ui.notifications.ports.notification_port import (
    NotificationContext,
)

NOTIFICATION_TITLE_SUFFIXES: dict[NotificationContext, str] = {
    NotificationContext.ACTION_REQUIRED: "Action Required",
    NotificationContext.COMPLETE: "Task Complete",
}

NOTIFICATION_THROTTLE_SECONDS: float = 1.0


class TextualNotificationAdapter:
    def __init__(
        self, app: App, *, get_enabled: Callable[[], bool], default_title: str = "App"
    ) -> None:
        self._app = app
        self._get_enabled = get_enabled
        self._default_title = default_title
        self._has_focus: bool = True
        self._last_notification_time: float = 0.0

    def notify(self, context: NotificationContext) -> None:
        if not self._get_enabled() or self._has_focus:
            return

        current_time = time.monotonic()
        if current_time - self._last_notification_time < NOTIFICATION_THROTTLE_SECONDS:
            return

        self._last_notification_time = current_time
        self._app.bell()
        self._set_title(self._get_notification_title(context))

    def on_focus(self) -> None:
        self._has_focus = True
        self.restore()

    def on_blur(self) -> None:
        self._has_focus = False

    def restore(self) -> None:
        self._set_title(self._default_title)

    def _get_notification_title(self, context: NotificationContext) -> str:
        suffix = NOTIFICATION_TITLE_SUFFIXES.get(context)
        if suffix is None:
            return self._default_title
        return f"{self._default_title} - {suffix}"

    def _set_title(self, title: str) -> None:
        if not self._app.is_headless and self._app._driver is not None:
            self._app._driver.write(f"\x1b]0;{title}\x07")
