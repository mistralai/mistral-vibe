from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.content import Content
from textual.widgets import Static

from tests.stubs.fake_tool import FakeTool, FakeToolArgs
from vibe.cli.textual_ui.widgets.collapsible import CollapsibleSection
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage, ToolResultMessage
from vibe.core.types import ToolCallEvent, ToolResultEvent


class _ToolApp(App[None]):
    def __init__(
        self, call_event: ToolCallEvent, result_event: ToolResultEvent
    ) -> None:
        super().__init__()
        self._call_event = call_event
        self._result_event = result_event
        self.call_widget: ToolCallMessage | None = None
        self.result_widget: ToolResultMessage | None = None

    def compose(self) -> ComposeResult:
        yield Vertical(id="root")

    async def on_mount(self) -> None:
        root = self.query_one("#root", Vertical)
        self.call_widget = ToolCallMessage(self._call_event)
        await root.mount(self.call_widget)
        self.result_widget = ToolResultMessage(self._result_event, self.call_widget)
        await root.mount(self.result_widget)


def _rendered(widget: Static) -> Content:
    content = widget.render()
    assert isinstance(content, Content)
    return content


def _call_event() -> ToolCallEvent:
    return ToolCallEvent(
        tool_name="stub_tool",
        tool_class=FakeTool,
        args=FakeToolArgs(),
        tool_call_id="a",
    )


def _error_result(error: str = "boom") -> ToolResultEvent:
    return ToolResultEvent(
        tool_name="stub_tool",
        tool_class=FakeTool,
        result=None,
        error=error,
        tool_call_id="a",
    )


def _skipped_result() -> ToolResultEvent:
    return ToolResultEvent(
        tool_name="stub_tool",
        tool_class=FakeTool,
        result=None,
        skipped=True,
        skip_reason="User declined",
        tool_call_id="a",
    )


@pytest.mark.asyncio
async def test_error_renders_muted_then_escalates_icon_and_styling() -> None:
    app = _ToolApp(_call_event(), _error_result())
    async with app.run_test() as pilot:
        await pilot.pause()
        call_widget = app.call_widget
        result_widget = app.result_widget
        assert call_widget is not None and result_widget is not None

        icon = call_widget._indicator_widget
        assert icon is not None
        # Default: held as a neutral grey square while the verdict is unknown.
        assert _rendered(icon).plain == "□"
        assert icon.has_class("muted")
        assert not icon.has_class("error")
        assert not result_widget.has_class("error-text")

        result_widget.escalate_error()
        await pilot.pause()

        # Escalated: red-cross icon, but the folded body keeps its muted style
        # (only the "Error" word colored, no whole-body error-text).
        assert _rendered(icon).plain == "✕"
        assert icon.has_class("error")
        assert not icon.has_class("muted")
        assert not result_widget.has_class("error-text")


@pytest.mark.asyncio
async def test_declined_call_renders_muted_square() -> None:
    app = _ToolApp(_call_event(), _skipped_result())
    async with app.run_test() as pilot:
        await pilot.pause()
        call_widget = app.call_widget
        assert call_widget is not None

        icon = call_widget._indicator_widget
        assert icon is not None
        assert _rendered(icon).plain == "□"
        assert icon.has_class("muted")
        assert not icon.has_class("error")


@pytest.mark.asyncio
async def test_error_with_square_brackets_does_not_raise_markup_error() -> None:
    error = (
        "Validation error in tool ask_user_question: 1 validation error for "
        "AskUserQuestionArgs\nquestions.0.header\n  Value error "
        "[type=value_error, input_value={'questions[0].header': 'x'}, "
        "input_type=dict]"
    )
    app = _ToolApp(_call_event(), _error_result(error))
    async with app.run_test() as pilot:
        await pilot.pause()
        result_widget = app.result_widget
        assert result_widget is not None

        detail = result_widget.query_one(CollapsibleSection).query_one(Static)
        content = _rendered(detail)
        assert content.plain == f"Error: {error}"


@pytest.mark.asyncio
async def test_folded_error_detail_colors_only_the_error_word() -> None:
    app = _ToolApp(_call_event(), _error_result())
    async with app.run_test() as pilot:
        await pilot.pause()
        result_widget = app.result_widget
        assert result_widget is not None

        section = result_widget.query_one(CollapsibleSection)
        detail = section.query_one(Static)
        # A markup-enabled Static is required so only "Error" can be colored.
        assert not isinstance(detail, NoMarkupStatic)
        content = _rendered(detail)
        assert content.plain == "Error: boom"
        assert any(
            span.start == 0 and span.end == len("Error") for span in content.spans
        )
