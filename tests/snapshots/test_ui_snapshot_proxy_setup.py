from __future__ import annotations

import pytest
from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from vibe.core.proxy_setup import get_current_proxy_settings, set_proxy_var


class ProxySetupTestApp(BaseSnapshotTestApp):
    async def on_mount(self) -> None:
        await super().on_mount()
        await self._switch_to_proxy_setup_app()


class PrePopulatedProxySetupTestApp(BaseSnapshotTestApp):
    async def on_mount(self) -> None:
        set_proxy_var("HTTP_PROXY", "http://old-proxy:8080")
        set_proxy_var("HTTPS_PROXY", "https://old-proxy:8443")
        await super().on_mount()
        await self._switch_to_proxy_setup_app()


def test_snapshot_proxy_setup_initial_empty(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_proxy_setup.py:ProxySetupTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )


def test_snapshot_proxy_setup_initial_with_values(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_proxy_setup.py:PrePopulatedProxySetupTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )


def test_snapshot_proxy_setup_save_new_values(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)
        await pilot.press(*"http://proxy.example.com:8080")
        await pilot.press("down")
        await pilot.press(*"https://proxy.example.com:8443")
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_proxy_setup.py:ProxySetupTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )

    settings = get_current_proxy_settings()
    assert settings["HTTP_PROXY"] == "http://proxy.example.com:8080"
    assert settings["HTTPS_PROXY"] == "https://proxy.example.com:8443"


def test_snapshot_proxy_setup_edit_existing_values(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)
        await pilot.press("ctrl+u")
        await pilot.press(*"http://new-proxy:9090")
        await pilot.press("down")
        await pilot.press("ctrl+u")
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_proxy_setup.py:PrePopulatedProxySetupTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )

    settings = get_current_proxy_settings()
    assert settings["HTTP_PROXY"] == "http://new-proxy:9090"
    assert settings["HTTPS_PROXY"] is None


def test_snapshot_proxy_setup_cancel_discards_changes(
    snap_compare: SnapCompare,
) -> None:

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)
        await pilot.press(*"http://should-not-save:8080")
        await pilot.pause(0.1)
        await pilot.press("escape")
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_proxy_setup.py:ProxySetupTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )

    settings = get_current_proxy_settings()
    assert settings["HTTP_PROXY"] is None


def test_snapshot_proxy_setup_save_error(
    snap_compare: SnapCompare, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_error(*args, **kwargs):
        raise OSError("Permission denied")

    monkeypatch.setattr("vibe.core.proxy_setup.set_key", raise_error)

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)
        await pilot.press(*"http://proxy:8080")
        await pilot.press("enter")
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_proxy_setup.py:ProxySetupTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )
