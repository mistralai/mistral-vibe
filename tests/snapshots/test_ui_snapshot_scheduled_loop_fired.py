from __future__ import annotations

from typing import Literal, cast
from weakref import WeakKeyDictionary

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.pilot import Pilot
from textual.widget import Widget

from tests.snapshots.snap_compare import SnapCompare
from vibe.app_server.events import HistoryEntryAdded
from vibe.app_server.models import (
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicNoticeEntry,
    ScheduledLoopFiredNoticeDetail,
    TextContentBlock,
)
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.widgets.messages import StreamingMessageBase, UserMessage
from vibe.cli.textual_ui.windowing.history import build_history_widgets
from vibe.user_content import UserDisplayContent

FIRED_AT_MS = 1_787_593_260_000


def _message(
    entry_id: str,
    turn_id: str,
    role: Literal["user", "assistant"],
    text: str,
    *,
    display: UserDisplayContent | None = None,
) -> PublicMessageEntry:
    return PublicMessageEntry(
        id=entry_id,
        session_id="session-1",
        turn_id=turn_id,
        created_at=FIRED_AT_MS,
        updated_at=FIRED_AT_MS,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        role=role,
        content=[TextContentBlock(text=text)],
        user_display_content=display,
    )


def _fired(turn_id: str) -> PublicNoticeEntry:
    return PublicNoticeEntry(
        id=f"scheduled-loop-{turn_id}",
        session_id="session-1",
        turn_id=turn_id,
        created_at=FIRED_AT_MS,
        updated_at=FIRED_AT_MS,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        level="info",
        message="Loop `1a2b3c4d` fired",
        detail=ScheduledLoopFiredNoticeDetail(loop_id="1a2b3c4d"),
    )


def _transcript() -> list[PublicHistoryEntry]:
    marker = UserDisplayContent(
        version="1",
        host="vibe",
        content=[
            {
                "type": "vibe.scheduled_loop",
                "loopId": "1a2b3c4d",
                "firedAt": FIRED_AT_MS,
            }
        ],
    )
    return [
        _message("u0", "t0", "user", "Check CI every minute"),
        _message("a0", "t0", "assistant", "Scheduled loop 1a2b3c4d."),
        _message("u1", "t1", "user", "Run the linter", display=marker),
        _fired("t1"),
        _message("a1", "t1", "assistant", "The linter passed."),
    ]


class _TranscriptApp(App):
    CSS_PATH = "../../vibe/cli/textual_ui/app.tcss"

    def compose(self) -> ComposeResult:
        yield VerticalScroll(id="messages")

    async def mount_message(self, widget: Widget, **_: object) -> None:
        await self.query_one("#messages", VerticalScroll).mount(widget)
        if isinstance(widget, StreamingMessageBase):
            await widget.write_initial_content()


class LiveScheduledLoopApp(_TranscriptApp):
    async def play(self) -> None:
        handler = EventHandler(
            mount_callback=self.mount_message, get_tools_collapsed=lambda: False
        )
        # The app mounts a typed prompt on submit; only a fired one comes from the server.
        await self.mount_message(
            UserMessage("Check CI every minute", history_entry_id="u0")
        )
        for entry in _transcript():
            await handler.handle_event(HistoryEntryAdded(entry))
        await handler.finalize_streaming()


class ResumedScheduledLoopApp(_TranscriptApp):
    async def on_mount(self) -> None:
        for widget in build_history_widgets(
            _transcript(),
            start_index=0,
            history_widget_indices=WeakKeyDictionary(),
            tools_collapsed=False,
        ):
            await self.mount_message(widget)


def test_snapshot_live_fired_loop_shows_its_prompt(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await cast(LiveScheduledLoopApp, pilot.app).play()
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_scheduled_loop_fired.py:LiveScheduledLoopApp",
        terminal_size=(80, 16),
        run_before=run_before,
    )


def test_snapshot_resumed_fired_loop_shows_its_prompt(
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.3)

    assert snap_compare(
        "test_ui_snapshot_scheduled_loop_fired.py:ResumedScheduledLoopApp",
        terminal_size=(80, 16),
        run_before=run_before,
    )
