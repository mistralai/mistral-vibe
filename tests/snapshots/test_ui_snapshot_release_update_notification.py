from __future__ import annotations

from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp, default_config
from tests.snapshots.snap_compare import SnapCompare
from tests.update_notifier.adapters.fake_update_cache_repository import (
    FakeUpdateCacheRepository,
)
from tests.update_notifier.adapters.fake_version_update_gateway import (
    FakeVersionUpdateGateway,
)
from vibe.cli.update_notifier import VersionUpdate


class SnapshotTestAppWithUpdate(BaseSnapshotTestApp):
    def __init__(self):
        config = default_config()
        config.enable_update_checks = True
        version_update_notifier = FakeVersionUpdateGateway(
            update=VersionUpdate(latest_version="1000.2.0")
        )
        update_cache_repository = FakeUpdateCacheRepository()
        super().__init__(
            config=config,
            version_update_notifier=version_update_notifier,
            update_cache_repository=update_cache_repository,
            current_version="1.0.4",
        )


def test_snapshot_shows_release_update_notification(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_release_update_notification.py:SnapshotTestAppWithUpdate",
        terminal_size=(120, 36),
        run_before=run_before,
    )
