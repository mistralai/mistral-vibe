from __future__ import annotations

from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import (
    BaseSnapshotTestApp,
    wait_for_agent_switch,
)
from tests.snapshots.snap_compare import SnapCompare
from vibe.core.agents.models import BuiltinAgentName


class DefaultModeSnapshotApp(BaseSnapshotTestApp):
    _current_agent_name = BuiltinAgentName.ACCEPT_EDITS


def test_snapshot_default_mode(snap_compare: SnapCompare) -> None:
    """Test that accept-edits mode is displayed at startup."""

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_modes.py:DefaultModeSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_cycle_to_auto_approve_mode(snap_compare: SnapCompare) -> None:
    """Test that shift+tab cycles from accept-edits to auto-approve mode."""

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("shift+tab")
        await wait_for_agent_switch(pilot, "auto-approve")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_modes.py:DefaultModeSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_cycle_to_ask_mode(snap_compare: SnapCompare) -> None:
    """Test that shift+tab wraps from auto-approve to ask mode."""

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("shift+tab")
        await pilot.press("shift+tab")
        await wait_for_agent_switch(pilot, "ask")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_modes.py:DefaultModeSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_cycle_to_plan_mode(snap_compare: SnapCompare) -> None:
    """Test that shift+tab cycles from ask to plan mode."""

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("shift+tab")
        await pilot.press("shift+tab")
        await pilot.press("shift+tab")
        await wait_for_agent_switch(pilot, "plan")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_modes.py:DefaultModeSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_cycle_wraps_to_accept_edits(snap_compare: SnapCompare) -> None:
    """Test that shift+tab cycles back to accept-edits mode."""

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("shift+tab")
        await pilot.press("shift+tab")
        await pilot.press("shift+tab")
        await pilot.press("shift+tab")
        await wait_for_agent_switch(pilot, "accept-edits")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_modes.py:DefaultModeSnapshotApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )
