from __future__ import annotations

from typing import cast

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widget import Widget

from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.app_server import CoreEventProjection
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.widgets.messages import BashOutputMessage
from vibe.core.tools.builtins.edit import Edit, EditArgs, EditResult
from vibe.core.types import ToolCallEvent, ToolResultEvent


class _SnapshotApp(App):
    CSS_PATH = "../../vibe/cli/textual_ui/app.tcss"

    def __init__(self) -> None:
        super().__init__()
        self._scroll: VerticalScroll | None = None
        self._handler: EventHandler | None = None

    def compose(self) -> ComposeResult:
        self._scroll = VerticalScroll(id="messages")
        yield self._scroll

    def on_mount(self) -> None:
        async def mount_callback(
            widget: Widget,
            *,
            after: Widget | None = None,
            before: Widget | None = None,
            container: Widget | None = None,
        ) -> None:
            if self._scroll is None:
                return
            if after is not None and after.parent is not None:
                await cast(Widget, after.parent).mount(widget, after=after)
            elif container is not None:
                await container.mount(widget)
            else:
                await self._scroll.mount(widget)

        self._handler = EventHandler(
            mount_callback=mount_callback, get_tools_collapsed=lambda: False
        )

    async def populate(self) -> None:
        raise NotImplementedError


class BashOverflowApp(_SnapshotApp):
    async def populate(self) -> None:
        if self._scroll is None:
            return
        # 24 lines: a 20-line preview above, with the 4-line overflow folded under
        # the classic "+4 lines" / "show less" toggle beneath it.
        output = "\n".join(f"output line {i}" for i in range(1, 25))
        await self._scroll.mount(
            BashOutputMessage("ls -la", "/repo", output=output, exit_code=0)
        )


class EditResultApp(_SnapshotApp):
    async def populate(self) -> None:
        if self._scroll is None or self._handler is None:
            return

        projection = CoreEventProjection()
        call_id = "edit_success"
        old_string = '    return f"hello {name}"'
        new_string = '    return f"Hello, {name}!"'
        await projection.dispatch(
            ToolCallEvent(
                tool_call_id=call_id,
                tool_name="edit",
                tool_class=Edit,
                args=EditArgs(
                    file_path="/repo/app.py",
                    old_string=old_string,
                    new_string=new_string,
                ),
            ),
            self._handler.handle_event,
        )
        result = EditResult(
            file="/repo/app.py",
            message="The file has been updated successfully.",
            old_string=old_string,
            new_string=new_string,
        )
        result._ui_occurrences = [
            (
                1,
                "\n".join([
                    "def greet(name: str) -> str:",
                    old_string,
                    "",
                    'print(greet("world"))',
                ]),
                "\n".join([
                    "def greet(name: str) -> str:",
                    new_string,
                    "",
                    'print(greet("world"))',
                ]),
            )
        ]
        await projection.dispatch(
            ToolResultEvent(
                tool_name="edit", tool_class=Edit, result=result, tool_call_id=call_id
            ),
            self._handler.handle_event,
        )


async def _populated(pilot: Pilot) -> None:
    app = cast(_SnapshotApp, pilot.app)
    await app.populate()
    await pilot.pause(0.3)


async def _collapsed_ansi(pilot: Pilot) -> None:
    pilot.app.theme = "ansi-dark"
    await _populated(pilot)


def test_snapshot_edit_result(snap_compare: SnapCompare) -> None:
    assert snap_compare(
        "test_ui_snapshot_overflow_sections.py:EditResultApp",
        terminal_size=(80, 16),
        run_before=_populated,
    )


def test_snapshot_bash_overflow_collapsed_ansi(snap_compare: SnapCompare) -> None:
    # Regression guard: the muted overflow arrow must stay dimmed under an ANSI
    # theme (it lost its `&:ansi { text-style: dim }` once already).
    assert snap_compare(
        "test_ui_snapshot_overflow_sections.py:BashOverflowApp",
        terminal_size=(80, 30),
        run_before=_collapsed_ansi,
    )
