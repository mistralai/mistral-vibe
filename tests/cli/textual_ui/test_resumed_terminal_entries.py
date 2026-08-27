from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.content import Content
from textual.widget import Widget
from textual.widgets import Static

from vibe.app_server.events import HistoryEntryAdded
from vibe.app_server.models import (
    CancelledEffectState,
    CompletedEffectState,
    EffectCallDisplay,
    EffectResultDisplay,
    FailedEffectState,
    GenericEffectDetail,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicError,
    PublicHistoryEntry,
    PublicReasoningEntry,
)
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.widgets.messages import ReasoningMessage
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage


class _ResumedHistoryApp(App[None]):
    def __init__(self, entry: PublicHistoryEntry) -> None:
        super().__init__()
        self._entry = entry

    def compose(self) -> ComposeResult:
        yield Vertical(id="history")

    async def on_mount(self) -> None:
        history = self.query_one("#history", Vertical)

        async def mount(
            widget: Widget,
            *,
            after: Widget | None = None,
            before: Widget | None = None,
            container: Widget | None = None,
        ) -> None:
            if container is not None:
                await container.mount(widget)
                return
            if before is not None and before.parent is history:
                await history.mount(widget, before=before)
                return
            if after is not None and after.parent is history:
                await history.mount(widget, after=after)
                return
            await history.mount(widget)

        handler = EventHandler(mount_callback=mount, get_tools_collapsed=lambda: True)
        await handler.handle_event(HistoryEntryAdded(self._entry))


def _reasoning() -> PublicReasoningEntry:
    return PublicReasoningEntry(
        id="reasoning-1",
        session_id="session-1",
        turn_id="turn-1",
        created_at=1,
        updated_at=2,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        text="The completed reasoning from the resumed session.",
    )


def _effect(
    state: CompletedEffectState | FailedEffectState | CancelledEffectState,
) -> PublicEffectEntry:
    return PublicEffectEntry(
        id="effect-1",
        session_id="session-1",
        turn_id="turn-1",
        created_at=1,
        updated_at=2,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        title="example",
        detail=GenericEffectDetail(
            tool_name="example",
            input={"path": "README.md"},
            display=EffectCallDisplay(
                summary="Reading README.md",
                suffix="(truncated)",
                settled_verb="Read",
                settled_message="README.md",
                status_text="Running example",
            ),
        ),
        state=state,
    )


def _failed_effect() -> PublicEffectEntry:
    return _effect(
        FailedEffectState(
            error=PublicError(message="Read failed"),
            display=EffectResultDisplay(
                success=False, message="File not found at: README.md"
            ),
        )
    )


def _text(widget: Static | None) -> str:
    assert widget is not None
    rendered = widget.render()
    assert isinstance(rendered, Content)
    return rendered.plain


@pytest.mark.asyncio
async def test_resumed_completed_reasoning_starts_settled() -> None:
    app = _ResumedHistoryApp(_reasoning())

    async with app.run_test() as pilot:
        await pilot.pause()
        message = app.query_one(ReasoningMessage)

        assert not message._is_spinning
        assert message._spinner_timer is None
        assert _text(message._indicator_widget) == "⏵"
        assert _text(message._status_text_widget) == "Thought"
        assert message._stream is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "glyph"),
    [
        (
            CompletedEffectState(
                output={"value": "done"},
                output_text="transient output",
                display=EffectResultDisplay(success=True, message="Done"),
            ),
            "✓",
        ),
        (
            CancelledEffectState(
                reason="Turn interrupted",
                output_text="transient output",
                display=EffectResultDisplay(success=False, message="Cancelled"),
            ),
            "□",
        ),
    ],
)
async def test_resumed_terminal_effect_starts_settled(
    state: CompletedEffectState | CancelledEffectState, glyph: str
) -> None:
    app = _ResumedHistoryApp(_effect(state))

    async with app.run_test() as pilot:
        await pilot.pause()
        call = app.query_one(ToolCallMessage)
        stream = call._stream_widget

        assert not call._is_spinning
        assert call._spinner_timer is None
        assert _text(call._indicator_widget) == glyph
        assert stream is not None
        assert not stream.display
        assert _text(stream) == ""


@pytest.mark.asyncio
async def test_resumed_failed_effect_uses_settled_display() -> None:
    app = _ResumedHistoryApp(_failed_effect())

    async with app.run_test() as pilot:
        await pilot.pause()
        call = app.query_one(ToolCallMessage)

        assert _text(call._verb_widget) == "Read"
        assert _text(call._text_widget) == "README.md"
        assert _text(call._suffix_widget) == "(truncated)"
