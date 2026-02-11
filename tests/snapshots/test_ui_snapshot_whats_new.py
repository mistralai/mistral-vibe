from __future__ import annotations

import os
from pathlib import Path
import time
from unittest.mock import patch

from textual.pilot import Pilot

from tests.cli.plan_offer.adapters.fake_whoami_gateway import FakeWhoAmIGateway
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp, default_config
from tests.snapshots.snap_compare import SnapCompare
from tests.update_notifier.adapters.fake_update_cache_repository import (
    FakeUpdateCacheRepository,
)
from tests.update_notifier.adapters.fake_update_gateway import FakeUpdateGateway
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIResponse
from vibe.cli.update_notifier import UpdateCache


class SnapshotTestAppWithWhatsNew(BaseSnapshotTestApp):
    def __init__(self, gateway: FakeWhoAmIGateway | None = None):
        self._previous_api_key = os.environ.get("MISTRAL_API_KEY")
        os.environ["MISTRAL_API_KEY"] = "snapshot-api-key"

        config = default_config()
        config.enable_update_checks = False
        update_notifier = FakeUpdateGateway(update=None)
        cache = UpdateCache(
            latest_version="1.0.0",
            stored_at_timestamp=int(time.time()),
            seen_whats_new_version=None,
        )
        update_cache_repository = FakeUpdateCacheRepository(update_cache=cache)
        super().__init__(
            config=config,
            update_notifier=update_notifier,
            update_cache_repository=update_cache_repository,
            current_version="1.0.0",
            plan_offer_gateway=gateway,
        )

    def on_unmount(self) -> None:
        if self._previous_api_key is None:
            os.environ.pop("MISTRAL_API_KEY", None)
        else:
            os.environ["MISTRAL_API_KEY"] = self._previous_api_key
        return None


class SnapshotTestAppWithPlanUpgradeCTA(SnapshotTestAppWithWhatsNew):
    def __init__(self):
        plan_offer_gateway = FakeWhoAmIGateway(
            WhoAmIResponse(
                is_pro_plan=False,
                advertise_pro_plan=True,
                prompt_switching_to_pro_plan=False,
            )
        )

        super().__init__(gateway=plan_offer_gateway)


class SnapshotTestAppWithSwitchKeyCTA(SnapshotTestAppWithWhatsNew):
    def __init__(self):
        plan_offer_gateway = FakeWhoAmIGateway(
            WhoAmIResponse(
                is_pro_plan=False,
                advertise_pro_plan=False,
                prompt_switching_to_pro_plan=True,
            )
        )

        super().__init__(gateway=plan_offer_gateway)


class SnapshotTestAppWithWhatsNewNoPlanCTA(SnapshotTestAppWithWhatsNew):
    def __init__(self):
        plan_offer_gateway = FakeWhoAmIGateway(
            WhoAmIResponse(
                is_pro_plan=True,
                advertise_pro_plan=False,
                prompt_switching_to_pro_plan=False,
            )
        )

        super().__init__(gateway=plan_offer_gateway)


def test_snapshot_shows_whats_new_message(
    snap_compare: SnapCompare, tmp_path: Path
) -> None:
    # Create whats_new.md file before the app starts
    whats_new_file = tmp_path / "whats_new.md"
    whats_new_file.write_text("# What's New\n\n- Feature 1\n- Feature 2\n- Feature 3")

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.5)

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        assert snap_compare(
            "test_ui_snapshot_whats_new.py:SnapshotTestAppWithWhatsNew",
            terminal_size=(120, 36),
            run_before=run_before,
        )


def test_snapshot_shows_upgrade_message(
    snap_compare: SnapCompare, tmp_path: Path
) -> None:
    whats_new_file = tmp_path / "whats_new.md"
    whats_new_file.write_text("# What's New\n\n- Feature 1\n- Feature 2\n- Feature 3")

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.5)

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        assert snap_compare(
            "test_ui_snapshot_whats_new.py:SnapshotTestAppWithPlanUpgradeCTA",
            terminal_size=(120, 36),
            run_before=run_before,
        )


def test_snapshot_shows_switch_message(
    snap_compare: SnapCompare, tmp_path: Path
) -> None:
    whats_new_file = tmp_path / "whats_new.md"
    whats_new_file.write_text("# What's New\n\n- Feature 1\n- Feature 2\n- Feature 3")

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.5)

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        assert snap_compare(
            "test_ui_snapshot_whats_new.py:SnapshotTestAppWithSwitchKeyCTA",
            terminal_size=(120, 36),
            run_before=run_before,
        )


def test_snapshot_shows_no_plan_message(
    snap_compare: SnapCompare, tmp_path: Path
) -> None:
    whats_new_file = tmp_path / "whats_new.md"
    whats_new_file.write_text("# What's New\n\n- Feature 1\n- Feature 2\n- Feature 3")

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.5)

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        assert snap_compare(
            "test_ui_snapshot_whats_new.py:SnapshotTestAppWithWhatsNewNoPlanCTA",
            terminal_size=(120, 36),
            run_before=run_before,
        )
