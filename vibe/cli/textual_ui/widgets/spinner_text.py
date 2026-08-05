from __future__ import annotations

from typing import Any

from textual.timer import Timer

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.spinner import Spinner, SpinnerType, create_spinner


class SpinnerText(NoMarkupStatic):
    """An inline text slot that spins until its value resolves.

    Drive it with :meth:`set_pending`: while pending it animates a spinner frame
    in place; once resolved it shows ``resolved`` and stops the timer. Handy for
    banner/status slots whose value arrives asynchronously (e.g. model routing).
    """

    _SPINNER_INTERVAL = 0.1

    def __init__(
        self, *, spinner_type: SpinnerType = SpinnerType.BRAILLE, **kwargs: Any
    ) -> None:
        super().__init__("", **kwargs)
        self._spinner: Spinner = create_spinner(spinner_type)
        self._timer: Timer | None = None

    def set_pending(self, pending: bool, *, resolved: str = "") -> None:
        if pending:
            self._start()
        else:
            self._stop()
            self.update(resolved)

    def _start(self) -> None:
        if self._timer is None:
            self.update(self._spinner.current_frame())
            self._timer = self.set_interval(self._SPINNER_INTERVAL, self._advance)

    def _advance(self) -> None:
        self.update(self._spinner.next_frame(), layout=False)

    def _stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def on_unmount(self) -> None:
        self._stop()
