from __future__ import annotations

from typing import Any

from textual.app import App

from vibe.cli.textual_ui.replay_harness._drain import drain_pumps
from vibe.cli.textual_ui.replay_harness._protocol import (
    HOLD_KEY,
    MARKER,
    RELEASE_KEY,
    replaying,
)


class ReplayDialogIdleMarker:
    """Idle marker for a dialog app shown before the session opens."""

    def __init__(self, app: App[Any]) -> None:
        self._app = app
        self._enabled = replaying()
        self._held = False
        self._emitted = False
        self._pending = False
        self._stopped = False

    def consume_batch_key(self, key: str) -> bool:
        """Hold or release marker emission for a batched input step."""
        if not self._enabled or key not in {HOLD_KEY, RELEASE_KEY}:
            return False
        self._held = key == HOLD_KEY
        if not self._held:
            self.maybe_emit()
        return True

    def rearm(self) -> None:
        if self._enabled:
            self._emitted = False

    def stop(self) -> None:
        """Hand the next marker to whatever the dialog's answer starts."""
        self._stopped = True

    def maybe_emit(self) -> None:
        if not self._enabled or self._stopped or self._held or self._emitted:
            return
        if self._pending:
            return

        def paint() -> None:
            self._app.call_after_refresh(self._write)

        # Drain first: the decision a key just posted still has to exit the dialog.
        self._pending = True
        drain_pumps(self._app, paint)

    def _write(self) -> None:
        self._pending = False
        if self._stopped or self._held or self._emitted:
            return
        driver = self._app._driver  # pyright: ignore[reportPrivateUsage]
        if driver is None:
            return
        self._emitted = True
        driver.write(MARKER)
        driver.flush()
