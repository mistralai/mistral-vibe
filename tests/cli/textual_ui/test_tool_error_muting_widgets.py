from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.content import Content
from textual.widgets import Static

from tests.stubs.app_server import CoreEventProjection
from tests.stubs.fake_tool import FakeTool, FakeToolArgs
from vibe.app_server.models import PublicEffectEntry
from vibe.cli.textual_ui.widgets.collapsible import CollapsibleSection
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage, ToolResultMessage
from vibe.core.tools.builtins.edit import Edit, EditArgs
from vibe.core.tools.builtins.read_file import ReadFile, ReadFileArgs
from vibe.core.types import ToolCallEvent, ToolResultEvent


class _ToolApp(App[None]):
    def __init__(
        self, result_event: ToolResultEvent, call_event: ToolCallEvent | None = None
    ) -> None:
        super().__init__()
        projection = CoreEventProjection()
        projection.project(call_event or _call_event())
        call_entry = projection.history[-1]
        projection.project(result_event)
        result_entry = projection.history[-1]
        assert isinstance(call_entry, PublicEffectEntry)
        assert isinstance(result_entry, PublicEffectEntry)
        self._call_entry = call_entry
        self._result_entry = result_entry
        self.call_widget: ToolCallMessage | None = None
        self.result_widget: ToolResultMessage | None = None

    def compose(self) -> ComposeResult:
        yield Vertical(id="root")

    async def on_mount(self) -> None:
        root = self.query_one("#root", Vertical)
        self.call_widget = ToolCallMessage(self._call_entry)
        await root.mount(self.call_widget)
        self.result_widget = ToolResultMessage(self._result_entry, self.call_widget)
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
async def test_error_folds_into_muted_arrow_then_escalates() -> None:
    app = _ToolApp(_error_result())
    async with app.run_test() as pilot:
        await pilot.pause()
        call_widget = app.call_widget
        result_widget = app.result_widget
        assert call_widget is not None and result_widget is not None

        # No square: the call widget is folded away and the section's arrow
        # carries the state -- muted (default colour) while the verdict is
        # unknown.
        assert call_widget.display is False
        section = result_widget.query_one(CollapsibleSection)
        triangle = section._triangle
        assert not triangle.has_class("success")
        assert not triangle.has_class("error")
        assert not result_widget.has_class("error-text")

        result_widget.escalate_error()
        await pilot.pause()

        # Escalated: the arrow turns red.
        assert triangle.has_class("error")


@pytest.mark.asyncio
async def test_read_error_header_uses_settled_call_display() -> None:
    file_path = "/workspace/nonexistent-file.txt"
    call = ToolCallEvent(
        tool_name="read_file",
        tool_class=ReadFile,
        args=ReadFileArgs(file_path=file_path),
        tool_call_id="a",
    )
    result = ToolResultEvent(
        tool_name="read_file",
        tool_class=ReadFile,
        error=f"read_file failed: File not found at: {file_path}",
        tool_call_id="a",
    )
    app = _ToolApp(result, call)
    async with app.run_test() as pilot:
        await pilot.pause()
        result_widget = app.result_widget
        assert result_widget is not None

        verb = result_widget.query_one(".collapsible-header-verb", NoMarkupStatic)
        message = result_widget.query_one(".status-indicator-text", NoMarkupStatic)
        assert _rendered(verb).plain == "Read"
        assert _rendered(message).plain == file_path


@pytest.mark.asyncio
async def test_generic_error_header_uses_ran_fallback() -> None:
    app = _ToolApp(_error_result())
    async with app.run_test() as pilot:
        await pilot.pause()
        result_widget = app.result_widget
        assert result_widget is not None

        verb = result_widget.query_one(".collapsible-header-verb", NoMarkupStatic)
        message = result_widget.query_one(".status-indicator-text", NoMarkupStatic)
        assert _rendered(verb).plain == "Ran"
        assert _rendered(message).plain == "stub_tool(text='')"


@pytest.mark.asyncio
async def test_non_collapsible_error_updates_visible_call_display() -> None:
    file_path = "/workspace/example.py"
    call = ToolCallEvent(
        tool_name="edit",
        tool_class=Edit,
        args=EditArgs(file_path=file_path, old_string="old", new_string="new"),
        tool_call_id="a",
    )
    result = ToolResultEvent(
        tool_name="edit",
        tool_class=Edit,
        error="edit failed: old_string was not found",
        tool_call_id="a",
    )
    app = _ToolApp(result, call)
    async with app.run_test() as pilot:
        await pilot.pause()
        call_widget = app.call_widget
        assert call_widget is not None

        assert call_widget.display
        assert call_widget._verb_widget is not None
        assert call_widget._text_widget is not None
        assert _rendered(call_widget._verb_widget).plain == "Edited"
        assert _rendered(call_widget._text_widget).plain == file_path


@pytest.mark.asyncio
async def test_declined_call_folds_into_muted_arrow() -> None:
    app = _ToolApp(_skipped_result())
    async with app.run_test() as pilot:
        await pilot.pause()
        call_widget = app.call_widget
        result_widget = app.result_widget
        assert call_widget is not None and result_widget is not None

        assert call_widget.display is False
        triangle = result_widget.query_one(CollapsibleSection)._triangle
        assert not triangle.has_class("success")
        assert not triangle.has_class("error")


@pytest.mark.asyncio
async def test_error_with_square_brackets_does_not_raise_markup_error() -> None:
    error = (
        "Validation error in tool ask_user_question: 1 validation error for "
        "AskUserQuestionArgs\nquestions.0.header\n  Value error "
        "[type=value_error, input_value={'questions[0].header': 'x'}, "
        "input_type=dict]"
    )
    app = _ToolApp(_error_result(error))
    async with app.run_test() as pilot:
        await pilot.pause()
        result_widget = app.result_widget
        assert result_widget is not None

        # The detail body is built lazily on expand.
        result_widget.query_one(CollapsibleSection).set_collapsed(False)
        await pilot.pause()

        detail = result_widget.query_one(".tool-result-content").children[0]
        assert isinstance(detail, Static)
        content = _rendered(detail)
        assert content.plain == f"Error: {error}"


@pytest.mark.asyncio
async def test_folded_error_detail_colors_only_the_error_word() -> None:
    app = _ToolApp(_error_result())
    async with app.run_test() as pilot:
        await pilot.pause()
        result_widget = app.result_widget
        assert result_widget is not None

        # The detail body is built lazily on expand.
        result_widget.query_one(CollapsibleSection).set_collapsed(False)
        await pilot.pause()

        detail = result_widget.query_one(".tool-result-content").children[0]
        # A markup-enabled Static is required so only "Error" can be colored.
        assert isinstance(detail, Static)
        assert not isinstance(detail, NoMarkupStatic)
        content = _rendered(detail)
        assert content.plain == "Error: boom"
        assert any(
            span.start == 0 and span.end == len("Error") for span in content.spans
        )


@pytest.mark.asyncio
async def test_error_strips_escape_sequences_from_failed_command_output() -> None:
    raw_output = (
        "Command failed: 'bad-cmd'\n"
        "Return code: 1\n"
        "Stdout: \x1b[?25l\x1b[H\x1b[2J\x1b[6n\x1b[?9001h\x1b]0;title\x07"
        "\x1b[31merror text\x1b[0m\rredraw"
    )
    app = _ToolApp(_error_result(raw_output))
    async with app.run_test() as pilot:
        await pilot.pause()
        result_widget = app.result_widget
        assert result_widget is not None

        result_widget.query_one(CollapsibleSection).set_collapsed(False)
        await pilot.pause()

        detail = result_widget.query_one(".tool-result-content").children[0]
        assert isinstance(detail, Static)
        content = _rendered(detail)
        assert "\x1b" not in content.plain
        assert "\r" not in content.plain
        assert "redraw" in content.plain
        assert "Command failed" in content.plain
