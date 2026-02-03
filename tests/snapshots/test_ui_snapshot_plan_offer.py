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
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIResponse
from vibe.cli.update_notifier import UpdateCache


class PlanOfferSnapshotApp(BaseSnapshotTestApp):
    def __init__(self, gateway: FakeWhoAmIGateway):
        self._previous_api_key = os.environ.get("MISTRAL_API_KEY")
        os.environ["MISTRAL_API_KEY"] = "snapshot-api-key"
        super().__init__(
            config=default_config(),
            plan_offer_gateway=gateway,
            update_cache_repository=FakeUpdateCacheRepository(),
        )

    def on_unmount(self) -> None:
        if self._previous_api_key is None:
            os.environ.pop("MISTRAL_API_KEY", None)
        else:
            os.environ["MISTRAL_API_KEY"] = self._previous_api_key
        return None


class SnapshotAppPlanOfferUpgrade(PlanOfferSnapshotApp):
    def __init__(self) -> None:
        gateway = FakeWhoAmIGateway(
            WhoAmIResponse(
                is_pro_plan=False,
                advertise_pro_plan=True,
                prompt_switching_to_pro_plan=False,
            )
        )
        super().__init__(gateway=gateway)


class SnapshotAppPlanOfferSwitchKey(PlanOfferSnapshotApp):
    def __init__(self) -> None:
        gateway = FakeWhoAmIGateway(
            WhoAmIResponse(
                is_pro_plan=False,
                advertise_pro_plan=False,
                prompt_switching_to_pro_plan=True,
            )
        )
        super().__init__(gateway=gateway)


class SnapshotAppPlanOfferNone(PlanOfferSnapshotApp):
    def __init__(self) -> None:
        gateway = FakeWhoAmIGateway(
            WhoAmIResponse(
                is_pro_plan=True,
                advertise_pro_plan=False,
                prompt_switching_to_pro_plan=False,
            )
        )
        super().__init__(gateway=gateway)


class SnapshotAppWhatsNewAndPlanOffer(PlanOfferSnapshotApp):
    def __init__(self) -> None:
        gateway = FakeWhoAmIGateway(
            WhoAmIResponse(
                is_pro_plan=False,
                advertise_pro_plan=True,
                prompt_switching_to_pro_plan=False,
            )
        )
        super().__init__(gateway=gateway)
        cache = UpdateCache(
            latest_version="1.0.0",
            stored_at_timestamp=int(time.time()),
            seen_whats_new_version=None,
        )
        self._update_cache_repository = FakeUpdateCacheRepository(update_cache=cache)
        self._current_version = "1.0.0"


async def _pause_for_plan_offer_task(pilot: Pilot) -> None:
    await pilot.pause(0.1)


def test_snapshot_shows_upgrade_plan_offer(snap_compare: SnapCompare) -> None:
    assert snap_compare(
        "test_ui_snapshot_plan_offer.py:SnapshotAppPlanOfferUpgrade",
        terminal_size=(120, 36),
        run_before=_pause_for_plan_offer_task,
    )


def test_snapshot_shows_switch_key_plan_offer(snap_compare: SnapCompare) -> None:
    assert snap_compare(
        "test_ui_snapshot_plan_offer.py:SnapshotAppPlanOfferSwitchKey",
        terminal_size=(120, 36),
        run_before=_pause_for_plan_offer_task,
    )


def test_snapshot_shows_no_plan_offer(snap_compare: SnapCompare) -> None:
    assert snap_compare(
        "test_ui_snapshot_plan_offer.py:SnapshotAppPlanOfferNone",
        terminal_size=(120, 36),
        run_before=_pause_for_plan_offer_task,
    )


def test_snapshot_shows_whats_new_and_plan_offer(
    snap_compare: SnapCompare, tmp_path: Path
) -> None:
    whats_new_file = tmp_path / "whats_new.md"
    whats_new_file.write_text("# What's New\n\n- Feature 1\n- Feature 2")

    with patch("vibe.cli.update_notifier.whats_new.VIBE_ROOT", tmp_path):
        assert snap_compare(
            "test_ui_snapshot_plan_offer.py:SnapshotAppWhatsNewAndPlanOffer",
            terminal_size=(120, 36),
            run_before=_pause_for_plan_offer_task,
        )
