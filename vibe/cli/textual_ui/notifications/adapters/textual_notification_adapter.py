from __future__ import annotations

from collections.abc import Callable
from enum import Enum, auto
import re
import time

from textual.app import App

from vibe.cli.textual_ui.notifications.ports.notification_port import (
    NotificationContext,
)

NOTIFICATION_THROTTLE_SECONDS: float = 1.0

_RUNNING_INDICATOR = ">>"
_WAITING_INDICATOR = "?"

NOTIFICATION_TITLE_SUFFIXES: dict[NotificationContext, str] = {
    NotificationContext.ACTION_REQUIRED: "Action Required",
    NotificationContext.COMPLETE: "Task Complete",
}

# Strip control chars: titles reach the terminal raw inside an OSC sequence.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class _TabState(Enum):
    IDLE = auto()
    RUNNING = auto()
    WAITING = auto()


class TextualNotificationAdapter:
    def __init__(
        self,
        app: App,
        *,
        get_enabled: Callable[[], bool],
        get_title_enabled: Callable[[], bool] | None = None,
        default_title: str = "App",
    ) -> None:
        self._app = app
        self._get_enabled = get_enabled
        self._get_title_enabled = get_title_enabled or (lambda: True)
        self._default_title = default_title
        self._has_focus: bool = True
        self._last_notification_time: float = 0.0
        self._state: _TabState = _TabState.IDLE
        # The context that triggered the current bell. When blurred, the
        # title appends the matching suffix so the user sees what happened
        # without focusing the tab.
        self._bell_context: NotificationContext | None = None

    def notify(self, context: NotificationContext) -> None:
        if context == NotificationContext.ACTION_REQUIRED:
            self._state = _TabState.WAITING
        else:
            # COMPLETE is terminal: clear any running/waiting state so the
            # title is self-consistent regardless of caller ordering.
            self._state = _TabState.IDLE

        if not self._get_enabled():
            self._render()
            return

        if not self._has_focus:
            self._bell_context = context
        self._fire_bell()
        self._render()

    def set_running(self, active: bool) -> None:
        if active:
            self._state = _TabState.RUNNING
        else:
            self._state = _TabState.IDLE
        self._bell_context = None
        self._render()

    def on_focus(self) -> None:
        self._has_focus = True
        # Focus acknowledges the current bell episode: consume the suffix
        # so it does not reappear on the next blur. The bell itself was
        # already consumed (fired or skipped) when the episode began.
        self._bell_context = None
        self._render()

    def on_blur(self) -> None:
        self._has_focus = False
        self._render()

    def clear_waiting(self) -> None:
        """Clear a WAITING state to IDLE. RUNNING and IDLE are unaffected.

        This prevents clear_waiting() during session bootstrap from
        clobbering a running indicator on a cold-start turn.
        """
        if self._state == _TabState.WAITING:
            self._state = _TabState.IDLE
            self._bell_context = None
        self._render()

    def set_default_title(self, title: str) -> None:
        normalized = title.strip() or "Vibe"
        if normalized == self._default_title:
            return
        self._default_title = normalized
        self._render()

    def _render(self) -> None:
        title = self._default_title
        tab_status = self._get_title_enabled()
        # Suffix takes priority when blurred (user cannot see the prompt).
        # Indicator shows when tab-status is on and no suffix applies.
        if not self._has_focus and self._bell_context is not None:
            title = self._suffixed_title()
        elif tab_status:
            title = self._indicator_title()
        self._set_title(title)

    def _indicator_title(self) -> str:
        match self._state:
            case _TabState.RUNNING:
                return f"{_RUNNING_INDICATOR} {self._default_title}"
            case _TabState.WAITING:
                return f"{_WAITING_INDICATOR} {self._default_title}"
            case _:
                return self._default_title

    def _suffixed_title(self) -> str:
        assert self._bell_context is not None  # caller guarantees non-None
        suffix = NOTIFICATION_TITLE_SUFFIXES[self._bell_context]
        return f"{self._default_title} - {suffix}"

    def _fire_bell(self) -> None:
        """Fire the bell when blurred, with throttle."""
        if self._has_focus:
            return
        current_time = time.monotonic()
        if current_time - self._last_notification_time < NOTIFICATION_THROTTLE_SECONDS:
            return
        self._last_notification_time = current_time
        self._app.bell()

    def _set_title(self, title: str) -> None:
        if self._app.is_headless or self._app._driver is None:
            return
        safe_title = _CONTROL_CHARS_RE.sub("", title)
        self._app._driver.write(f"\x1b]0;{safe_title}\x07")
