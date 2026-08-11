from __future__ import annotations

from typing import Any

from textual.timer import Timer

from vibe.cli.textual_ui.widgets.no_markup_static import NonSelectableStatic

DEFAULT_NOTICE_TIMEOUT = 4.0


class InlineNotice(NonSelectableStatic):
    """A discrete, self-hiding inline status line (e.g. "Copied to clipboard").

    Unlike a toast, it renders in place and clears itself after a timeout.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.display = False
        self._hide_timer: Timer | None = None

    def show(self, message: str, *, timeout: float = DEFAULT_NOTICE_TIMEOUT) -> None:
        self.update(message)
        self.display = True
        if self._hide_timer is not None:
            self._hide_timer.stop()
        self._hide_timer = self.set_timer(timeout, self._hide)

    def _hide(self) -> None:
        self.display = False
