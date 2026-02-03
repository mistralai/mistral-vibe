from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp, default_config
from tests.snapshots.snap_compare import SnapCompare
from tests.update_notifier.adapters.fake_update_cache_repository import (
    FakeUpdateCacheRepository,
)
from tests.update_notifier.adapters.fake_update_gateway import FakeUpdateGateway
from vibe.cli.update_notifier import Update


class SnapshotTestAppWithUpdate(BaseSnapshotTestApp):
    def __init__(self):
        config = default_config()
        config.enable_update_checks = True
        update_notifier = FakeUpdateGateway(update=Update(latest_version="1000.2.0"))
        update_cache_repository = FakeUpdateCacheRepository()
        super().__init__(
            config=config,
            update_notifier=update_notifier,
            update_cache_repository=update_cache_repository,
            current_version="1.0.4",
        )


def test_snapshot_shows_release_update_notification(
    snap_compare: SnapCompare, tmp_path: Path
) -> None:
    whats_new_file = tmp_path / "whats_new.md"
    whats_new_file.write_text("# What's New\n\n- Feature 1\n- Feature 2")

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        assert snap_compare(
            "test_ui_snapshot_release_update_notification.py:SnapshotTestAppWithUpdate",
            terminal_size=(120, 36),
            run_before=run_before,
        )
