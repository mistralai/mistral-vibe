from __future__ import annotations

from textual.pilot import Pilot

from tests.snapshots.snap_compare import SnapCompare


def test_snapshot_default_mode(snap_compare: SnapCompare) -> None:
    """Test that default mode is displayed correctly at startup."""

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)

    assert snap_compare(
        "base_snapshot_test_app.py:BaseSnapshotTestApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_cycle_to_plan_mode(snap_compare: SnapCompare) -> None:
    """Test that shift+tab cycles from default to plan mode."""

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("shift+tab")  # default -> plan
        await pilot.pause(0.1)

    assert snap_compare(
        "base_snapshot_test_app.py:BaseSnapshotTestApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_cycle_to_accept_edits_mode(snap_compare: SnapCompare) -> None:
    """Test that shift+tab cycles from plan to accept edits mode."""

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("shift+tab")  # default -> plan
        await pilot.press("shift+tab")  # plan -> accept edits
        await pilot.pause(0.1)

    assert snap_compare(
        "base_snapshot_test_app.py:BaseSnapshotTestApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_cycle_to_auto_approve_mode(snap_compare: SnapCompare) -> None:
    """Test that shift+tab cycles to auto approve mode."""

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("shift+tab")  # default -> plan
        await pilot.press("shift+tab")  # plan -> accept edits
        await pilot.press("shift+tab")  # accept edits -> auto approve
        await pilot.pause(0.1)

    assert snap_compare(
        "base_snapshot_test_app.py:BaseSnapshotTestApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_cycle_wraps_to_default(snap_compare: SnapCompare) -> None:
    """Test that shift+tab cycles back to default mode after auto approve."""

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("shift+tab")  # default -> plan
        await pilot.press("shift+tab")  # plan -> accept edits
        await pilot.press("shift+tab")  # accept edits -> auto approve
        await pilot.press("shift+tab")  # auto approve -> default (wrap)
        await pilot.pause(0.1)

    assert snap_compare(
        "base_snapshot_test_app.py:BaseSnapshotTestApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )
