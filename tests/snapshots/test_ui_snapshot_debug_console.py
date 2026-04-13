from __future__ import annotations

import atexit
from pathlib import Path
import shutil
import tempfile

from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from vibe.core.log_reader import LogReader

_SAMPLE_LOGS = "\n".join([
    "2026-02-21T10:28:51.100000+00:00 1234 5678 DEBUG Initializing model registry",
    "2026-02-21T10:28:51.200000+00:00 1234 5678 INFO Server started on port 8080",
    "2026-02-21T10:28:51.300000+00:00 1234 5678 INFO Loading configuration from /etc/vibe/config.yaml",
    "2026-02-21T10:28:51.400000+00:00 1234 5678 WARNING Cache miss for key user:42",
    "2026-02-21T10:28:51.500000+00:00 1234 5678 DEBUG Processing request GET /api/v1/models",
    "2026-02-21T10:28:51.600000+00:00 1234 5678 INFO Request completed in 45ms",
    "2026-02-21T10:28:51.700000+00:00 1234 5678 ERROR Connection refused to upstream service",
    "2026-02-21T10:28:51.800000+00:00 1234 5678 INFO Retrying connection attempt 1/3",
    "2026-02-21T10:28:51.900000+00:00 1234 5678 WARNING Rate limit approaching for client api-key-abc",
    "2026-02-21T10:28:52.000000+00:00 1234 5678 INFO Health check passed",
])

_APPENDED_LOG = (
    "2026-02-21T10:28:53.000000+00:00 1234 5678 CRITICAL Out of memory error detected"
)

# Module-level temp dir so it's available when import_app instantiates the class
_TMP_DIR = Path(tempfile.mkdtemp())
_LOG_FILE = _TMP_DIR / "test.log"
_LOG_FILE.write_text(_SAMPLE_LOGS + "\n")
atexit.register(shutil.rmtree, str(_TMP_DIR), True)


class DebugConsoleSnapshotApp(BaseSnapshotTestApp):
    def __init__(self) -> None:
        super().__init__()
        self._log_reader = LogReader(log_file=_LOG_FILE, poll_interval=0.1)


def test_snapshot_debug_console_open(snap_compare: SnapCompare) -> None:
    """Test that the debug console opens and shows log entries."""
    _LOG_FILE.write_text(_SAMPLE_LOGS + "\n")

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("ctrl+backslash")
        await pilot.pause(0.4)

    assert snap_compare(
        DebugConsoleSnapshotApp(), terminal_size=(120, 36), run_before=run_before
    )


def test_snapshot_debug_console_live_append(snap_compare: SnapCompare) -> None:
    """Test that appending a log line to the file shows the new entry."""
    _LOG_FILE.write_text(_SAMPLE_LOGS + "\n")

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("ctrl+backslash")
        await pilot.pause(0.4)
        with _LOG_FILE.open("a") as f:
            f.write(_APPENDED_LOG + "\n")
            f.flush()
        await pilot.pause(0.5)
        await pilot.pause()

    assert snap_compare(
        DebugConsoleSnapshotApp(), terminal_size=(120, 36), run_before=run_before
    )


def test_snapshot_debug_console_close(snap_compare: SnapCompare) -> None:
    """Test that closing the debug console restores the normal UI."""
    _LOG_FILE.write_text(_SAMPLE_LOGS + "\n")

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("ctrl+backslash")
        await pilot.pause(0.4)
        await pilot.press("ctrl+backslash")
        await pilot.pause(0.2)

    assert snap_compare(
        DebugConsoleSnapshotApp(), terminal_size=(120, 36), run_before=run_before
    )
