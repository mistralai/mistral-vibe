from __future__ import annotations

from textual.pilot import Pilot

from tests.conftest import build_test_agent_loop
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp, default_config
from tests.snapshots.snap_compare import SnapCompare
from vibe.core.config import build_default_orchestrator
from vibe.core.config.layers.admin import AdminConfigLayer
from vibe.core.utils.concurrency import run_sync


class ConfigScreenTestApp(BaseSnapshotTestApp):
    async def on_mount(self) -> None:
        await super().on_mount()
        await self._show_config()


def _enforced_orchestrator():
    async def build():
        orchestrator = await build_default_orchestrator()
        admin = next(
            layer
            for layer in orchestrator.layers
            if isinstance(layer, AdminConfigLayer)
        )
        admin.load_managed_toml('theme = "textual-dark"\n')
        await orchestrator.reload()
        return orchestrator

    return run_sync(build())


class EnforcedConfigScreenTestApp(BaseSnapshotTestApp):
    def __init__(self, **kwargs) -> None:
        agent_loop = build_test_agent_loop(config=default_config())
        agent_loop._config_orchestrator = _enforced_orchestrator()
        super().__init__(agent_loop=agent_loop, **kwargs)

    async def on_mount(self) -> None:
        await super().on_mount()
        await self._show_config()


def test_snapshot_config_screen_initial(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_config_screen.py:ConfigScreenTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )


def test_snapshot_config_screen_edit_modal(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)
        for char in "theme":
            await pilot.press(char)
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_config_screen.py:ConfigScreenTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )


def test_snapshot_config_screen_search(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)
        for char in "enable":
            await pilot.press(char)
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_config_screen.py:ConfigScreenTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )


def test_snapshot_config_screen_enforced(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)
        for char in "theme":
            await pilot.press(char)
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_config_screen.py:EnforcedConfigScreenTestApp",
        terminal_size=(100, 36),
        run_before=run_before,
    )
