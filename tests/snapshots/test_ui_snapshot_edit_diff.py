from __future__ import annotations

from pathlib import Path

from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from vibe.core.tools.builtins.edit import EditArgs

FILE_CONTENT = "\n".join([
    "def greet(name):",
    '    return f"hello {name}"',
    "",
    "MAX_USERS = 100",
    "TIMEOUT = 30",
])


class EditApprovalApp(BaseSnapshotTestApp):
    _diff_theme: str = "tokyo-night"

    async def on_ready(self) -> None:
        await super().on_ready()
        self.theme = self._diff_theme
        path = Path("src/example.py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(FILE_CONTENT)
        args = EditArgs(
            file_path="src/example.py",
            old_string="MAX_USERS = 100\nTIMEOUT = 30",
            new_string="MAX_USERS = 200\nTIMEOUT = 30",
        )
        await self._switch_to_approval_app("edit", args)


class EditApprovalAnsiApp(EditApprovalApp):
    _diff_theme = "ansi-dark"


REPLACE_ALL_CONTENT = "\n".join([
    "def total(items):",
    "    count = 0",
    "    for item in items:",
    "        count = count + 1",
    "    return count",
    "",
    "class Counter:",
    "    def reset(self):",
    "        count = 0  # start over",
    "        return count",
])


class EditReplaceAllApprovalApp(BaseSnapshotTestApp):
    _diff_theme: str = "tokyo-night"

    async def on_ready(self) -> None:
        await super().on_ready()
        self.theme = self._diff_theme
        path = Path("src/counter.py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(REPLACE_ALL_CONTENT)
        args = EditArgs(
            file_path="src/counter.py",
            old_string="count = 0",
            new_string="count = 1",
            replace_all=True,
        )
        await self._switch_to_approval_app("edit", args)


LONG_OLD = "    message = " + " + ".join(f'"word_{i}"' for i in range(40))
LONG_NEW = "    message = " + " + ".join(f'"token_{i}"' for i in range(40))
OVERFLOW_CONTENT = "\n".join(["def build_message():", LONG_OLD, "    return message"])


class EditOverflowApprovalApp(BaseSnapshotTestApp):
    _diff_theme: str = "tokyo-night"

    async def on_ready(self) -> None:
        await super().on_ready()
        self.theme = self._diff_theme
        path = Path("src/message.py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(OVERFLOW_CONTENT)
        args = EditArgs(
            file_path="src/message.py",
            old_string=f"{LONG_OLD}\n    return message",
            new_string=f"{LONG_NEW}\n    return message.upper()",
        )
        await self._switch_to_approval_app("edit", args)


def test_snapshot_edit_approval_diff(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_edit_diff.py:EditApprovalApp",
        terminal_size=(100, 30),
        run_before=run_before,
    )


def test_snapshot_edit_approval_diff_ansi(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_edit_diff.py:EditApprovalAnsiApp",
        terminal_size=(100, 30),
        run_before=run_before,
    )


def test_snapshot_edit_approval_diff_replace_all(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_edit_diff.py:EditReplaceAllApprovalApp",
        terminal_size=(100, 30),
        run_before=run_before,
    )


def test_snapshot_edit_approval_diff_horizontal_overflow(
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_edit_diff.py:EditOverflowApprovalApp",
        terminal_size=(100, 30),
        run_before=run_before,
    )
