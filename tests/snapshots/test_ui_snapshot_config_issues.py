from __future__ import annotations

from pathlib import Path

from textual.pilot import Pilot

from tests.conftest import build_test_vibe_config
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from vibe.core.hooks.models import HookConfigIssue
from vibe.core.skills.models import SkillConfigIssue


class SnapshotTestAppWithConfigIssues(BaseSnapshotTestApp):
    def __init__(self) -> None:
        super().__init__()
        self.agent_loop.skill_manager._config_issues = [
            SkillConfigIssue(
                file=Path("/test/skills/broken-skill/SKILL.md"),
                message="Failed to load: missing required field 'description'",
            )
        ]


class SnapshotTestAppWithHookConfigIssue(BaseSnapshotTestApp):
    def __init__(self) -> None:
        super().__init__()
        self.agent_loop.hook_config_issues = [
            HookConfigIssue(
                file=Path("/test/hooks/broken-hook.toml"),
                message="Failed to parse: invalid TOML syntax",
            )
        ]


class SnapshotTestAppWithActiveModelWarning(BaseSnapshotTestApp):
    def __init__(self) -> None:
        super().__init__(
            config=build_test_vibe_config(
                disable_welcome_banner_animation=True,
                displayed_workdir="/test/workdir",
                active_model="does-not-exist",
            )
        )


def test_snapshot_shows_config_issue_notification(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_config_issues.py:SnapshotTestAppWithConfigIssues",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_shows_hook_config_issue_notification(
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_config_issues.py:SnapshotTestAppWithHookConfigIssue",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_shows_active_model_warning_notification(
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_config_issues.py:SnapshotTestAppWithActiveModelWarning",
        terminal_size=(120, 36),
        run_before=run_before,
    )
