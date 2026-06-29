from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from tests.stubs.fake_tool import FakeTool, FakeToolArgs
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.widgets.tools import ToolResultMessage
from vibe.core.types import ToolCallEvent, ToolResultEvent


def _call_event(call_id: str) -> ToolCallEvent:
    return ToolCallEvent(
        tool_name="stub_tool",
        tool_class=FakeTool,
        args=FakeToolArgs(),
        tool_call_id=call_id,
    )


def _error_result(call_id: str) -> ToolResultEvent:
    return ToolResultEvent(
        tool_name="stub_tool",
        tool_class=FakeTool,
        result=None,
        error="boom",
        tool_call_id=call_id,
    )


def _ok_result(call_id: str) -> ToolResultEvent:
    return ToolResultEvent(
        tool_name="stub_tool", tool_class=FakeTool, result=None, tool_call_id=call_id
    )


def _make_handler() -> tuple[EventHandler, AsyncMock]:
    mount_callback = AsyncMock()
    handler = EventHandler(
        mount_callback=mount_callback, get_tools_collapsed=lambda: False
    )
    return handler, mount_callback


def _last_result_widget(mount_callback: AsyncMock) -> ToolResultMessage:
    for call in reversed(mount_callback.call_args_list):
        widget = call.args[0]
        if isinstance(widget, ToolResultMessage):
            return widget
    raise AssertionError("no ToolResultMessage was mounted")


@pytest.mark.asyncio
async def test_error_result_is_registered_as_pending() -> None:
    handler, mount_callback = _make_handler()

    await handler.handle_event(_call_event("a"))
    await handler.handle_event(_error_result("a"))

    assert len(handler._pending_error_results) == 1


@pytest.mark.asyncio
async def test_followup_tool_call_keeps_error_muted() -> None:
    handler, mount_callback = _make_handler()

    await handler.handle_event(_call_event("a"))
    await handler.handle_event(_error_result("a"))
    result_widget = _last_result_widget(mount_callback)
    result_widget.escalate_error = Mock()

    await handler.handle_event(_call_event("b"))

    result_widget.escalate_error.assert_not_called()
    assert handler._pending_error_results == []


@pytest.mark.asyncio
async def test_turn_end_escalates_error() -> None:
    handler, mount_callback = _make_handler()

    await handler.handle_event(_call_event("a"))
    await handler.handle_event(_error_result("a"))
    result_widget = _last_result_widget(mount_callback)
    result_widget.escalate_error = Mock()

    handler.escalate_unresolved_errors()

    result_widget.escalate_error.assert_called_once()
    assert handler._pending_error_results == []


@pytest.mark.asyncio
async def test_successful_result_is_not_pending() -> None:
    handler, mount_callback = _make_handler()

    await handler.handle_event(_call_event("a"))
    await handler.handle_event(_ok_result("a"))

    assert handler._pending_error_results == []


@pytest.mark.asyncio
async def test_streaming_arg_update_before_result_does_not_register_error() -> None:
    handler, _ = _make_handler()

    # Same tool_call_id re-emitted as a streaming arg update, before any result.
    await handler.handle_event(_call_event("a"))
    await handler.handle_event(_call_event("a"))

    assert handler._pending_error_results == []


@pytest.mark.asyncio
async def test_parallel_errors_escalated_together_at_turn_end() -> None:
    handler, mount_callback = _make_handler()

    await handler.handle_event(_call_event("a"))
    await handler.handle_event(_call_event("b"))
    await handler.handle_event(_error_result("a"))
    await handler.handle_event(_error_result("b"))
    mocks: list[Mock] = []
    for widget in handler._pending_error_results:
        mock = Mock()
        widget.escalate_error = mock
        mocks.append(mock)
    assert len(mocks) == 2

    handler.escalate_unresolved_errors()

    for mock in mocks:
        mock.assert_called_once()
    assert handler._pending_error_results == []


@pytest.mark.asyncio
async def test_cancel_holds_muted_square_for_in_flight_call() -> None:
    handler, _ = _make_handler()

    await handler.handle_event(_call_event("a"))
    tool_call = handler.tool_calls["a"]
    show_muted = Mock()
    stop_spinning = Mock()
    tool_call.show_muted = show_muted
    tool_call.stop_spinning = stop_spinning

    handler.stop_current_tool_call(cancelled=True)

    show_muted.assert_called_once()
    stop_spinning.assert_not_called()


@pytest.mark.asyncio
async def test_turn_error_shows_red_cross_for_in_flight_call() -> None:
    handler, _ = _make_handler()

    await handler.handle_event(_call_event("a"))
    tool_call = handler.tool_calls["a"]
    show_muted = Mock()
    stop_spinning = Mock()
    tool_call.show_muted = show_muted
    tool_call.stop_spinning = stop_spinning

    handler.stop_current_tool_call(success=False)

    stop_spinning.assert_called_once()
    show_muted.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_does_not_escalate_pending_errors() -> None:
    handler, mount_callback = _make_handler()

    await handler.handle_event(_call_event("a"))
    await handler.handle_event(_error_result("a"))
    result_widget = _last_result_widget(mount_callback)
    result_widget.escalate_error = Mock()

    handler.stop_current_tool_call(cancelled=True)

    result_widget.escalate_error.assert_not_called()
    assert handler._pending_error_results == []
