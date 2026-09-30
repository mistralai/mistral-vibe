"""Test that the debug console outlives log reads the app server refuses."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import time

import pytest
from textual.app import App, ComposeResult
from textual.pilot import Pilot

from vibe.app_server.models import DebugLogEntry, DebugLogPage
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolError,
    ProtocolErrorCode,
)
from vibe.cli.textual_ui.widgets.debug_console import DebugConsole


class _LogSource:
    """Refuses reads the way the app server does while /branch holds the session."""

    def __init__(self) -> None:
        self.transition_active = True
        self.refused_reads = 0

    async def read_logs(self, *, limit: int = 100, offset: int = 0) -> DebugLogPage:
        if self.transition_active:
            self.refused_reads += 1
            raise AppServerResponseError(
                ProtocolError(
                    code=ProtocolErrorCode.CONFLICT,
                    message="Session lifecycle transition is active: fork:session-id",
                )
            )
        entry = DebugLogEntry(
            id="entry-1",
            timestamp=datetime(2026, 9, 29, tzinfo=UTC),
            ppid=1,
            pid=2,
            level="INFO",
            message="logged after the fork",
            raw_line="logged after the fork",
        )
        return DebugLogPage(entries=[entry], has_more=False)


class _DebugConsoleTestApp(App):
    def __init__(self, log_source: _LogSource) -> None:
        super().__init__()
        self._log_source = log_source

    def compose(self) -> ComposeResult:
        yield DebugConsole(log_source=self._log_source)


async def _wait_until(
    pilot: Pilot, predicate: Callable[[], bool], timeout: float = 2.0
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting for the debug console"
        await pilot.pause(0.02)


@pytest.mark.asyncio
async def test_refused_log_reads_do_not_crash_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.widgets.debug_console.LOG_POLL_INTERVAL", 0.05
    )
    source = _LogSource()
    app = _DebugConsoleTestApp(source)

    async with app.run_test() as pilot:
        console = app.query_one(DebugConsole)
        # The first page load and the polls after it are all refused.
        await _wait_until(pilot, lambda: source.refused_reads >= 3)
        assert app.return_code is None

        source.transition_active = False
        log_view = console._log_view
        assert log_view is not None
        await _wait_until(pilot, lambda: bool(log_view._lines))
        assert "logged after the fork" in log_view._lines[-1]
