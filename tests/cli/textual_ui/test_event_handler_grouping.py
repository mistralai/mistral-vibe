from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from tests.stubs.app_server import CoreEventProjection
from tests.stubs.fake_tool import FakeTool, FakeToolArgs
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.widgets.messages import ReasoningMessage
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage, ToolGroup
from vibe.core.tools.builtins.edit import Edit, EditArgs
from vibe.core.tools.builtins.write_file import WriteFile, WriteFileArgs
from vibe.core.types import AssistantEvent, ReasoningEvent, ToolCallEvent


def _call_event(call_id: str, tool_name: str = "stub_tool") -> ToolCallEvent:
    if tool_name == "edit":
        return ToolCallEvent(
            tool_name=tool_name,
            tool_class=Edit,
            args=EditArgs(file_path="app.py", old_string="old", new_string="new"),
            tool_call_id=call_id,
        )
    if tool_name == "write_file":
        return ToolCallEvent(
            tool_name=tool_name,
            tool_class=WriteFile,
            args=WriteFileArgs(file_path="app.py", content="content"),
            tool_call_id=call_id,
        )
    return ToolCallEvent(
        tool_name=tool_name,
        tool_class=FakeTool,
        args=FakeToolArgs(),
        tool_call_id=call_id,
    )


def _make_handler(
    *, show_thinking: bool = True
) -> tuple[EventHandler, AsyncMock, CoreEventProjection]:
    mount_callback = AsyncMock()
    handler = EventHandler(
        mount_callback=mount_callback,
        get_tools_collapsed=lambda: False,
        get_show_thinking=lambda: show_thinking,
    )
    return handler, mount_callback, CoreEventProjection()


def _mounted_groups(mount_callback: AsyncMock) -> list[ToolGroup]:
    return [
        call.args[0]
        for call in mount_callback.call_args_list
        if call.args and isinstance(call.args[0], ToolGroup)
    ]


def _mount_call_for(mount_callback: AsyncMock, widget: object):
    for call in mount_callback.call_args_list:
        if call.args and call.args[0] is widget:
            return call
    raise AssertionError("widget was never mounted")


@pytest.mark.asyncio
async def test_consecutive_tool_calls_share_one_group() -> None:
    handler, mount_callback, projection = _make_handler()

    await projection.dispatch(_call_event("a"), handler.handle_event)
    await projection.dispatch(_call_event("b"), handler.handle_event)

    groups = _mounted_groups(mount_callback)
    assert len(groups) == 1
    assert handler.current_tool_group is groups[0]


@pytest.mark.asyncio
async def test_reasoning_and_following_tool_call_share_group() -> None:
    handler, mount_callback, projection = _make_handler()

    await projection.dispatch(ReasoningEvent(content="thinking"), handler.handle_event)
    await projection.dispatch(_call_event("a"), handler.handle_event)

    assert len(_mounted_groups(mount_callback)) == 1


@pytest.mark.asyncio
async def test_assistant_text_breaks_group_into_two() -> None:
    handler, mount_callback, projection = _make_handler()

    await projection.dispatch(_call_event("a"), handler.handle_event)
    await projection.dispatch(AssistantEvent(content="done"), handler.handle_event)

    assert handler.current_tool_group is None

    await projection.dispatch(_call_event("b"), handler.handle_event)

    assert len(_mounted_groups(mount_callback)) == 2


@pytest.mark.asyncio
async def test_edit_mounts_standalone_and_breaks_open_group() -> None:
    handler, mount_callback, projection = _make_handler()

    await projection.dispatch(_call_event("a"), handler.handle_event)
    await projection.dispatch(_call_event("e", tool_name="edit"), handler.handle_event)

    assert handler.current_tool_group is None

    edit_widget = handler.tool_calls["e"]
    edit_call = _mount_call_for(mount_callback, edit_widget)
    assert "container" not in edit_call.kwargs
    assert "after" not in edit_call.kwargs


@pytest.mark.asyncio
async def test_edit_after_edit_does_not_open_a_group() -> None:
    handler, mount_callback, projection = _make_handler()

    await projection.dispatch(_call_event("e1", tool_name="edit"), handler.handle_event)
    await projection.dispatch(
        _call_event("e2", tool_name="write_file"), handler.handle_event
    )

    assert _mounted_groups(mount_callback) == []
    assert handler.current_tool_group is None


@pytest.mark.asyncio
async def test_stop_current_tool_call_finalizes_group() -> None:
    handler, _, projection = _make_handler()

    await projection.dispatch(_call_event("a"), handler.handle_event)
    assert handler.current_tool_group is not None

    handler.stop_current_tool_call()

    assert handler.current_tool_group is None


@pytest.mark.asyncio
async def test_hidden_thinking_node_is_not_displayed() -> None:
    handler, _, projection = _make_handler(show_thinking=False)

    await projection.dispatch(ReasoningEvent(content="secret"), handler.handle_event)

    msg = handler.current_streaming_reasoning
    assert msg is not None
    assert msg.display is False


@pytest.mark.asyncio
async def test_live_thinking_toggle_collapses_only_empty_groups() -> None:
    config = build_test_vibe_config(show_thinking_nodes=True)
    app = build_test_vibe_app(config=config)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        handler = app.event_handler
        assert handler is not None
        projection = CoreEventProjection()

        await projection.dispatch(
            ReasoningEvent(content="reasoning only"), handler.handle_event
        )
        await projection.dispatch(
            AssistantEvent(content="separator"), handler.handle_event
        )
        await projection.dispatch(
            ReasoningEvent(content="reasoning with tool"), handler.handle_event
        )
        await projection.dispatch(_call_event("a"), handler.handle_event)

        groups = list(app._messages_area.query(ToolGroup))
        reasoning_nodes = list(app._messages_area.query(ReasoningMessage))
        assert len(groups) == 2
        assert len(reasoning_nodes) == 2

        app.config.show_thinking_nodes = False
        app._apply_thinking_visibility()

        assert all(node.display is False for node in reasoning_nodes)
        assert groups[0].display is False
        assert groups[1].display is True
        assert handler.tool_calls["a"].display is True


@pytest.mark.asyncio
async def test_grouped_tool_call_mounts_into_the_group() -> None:
    handler, mount_callback, projection = _make_handler()

    await projection.dispatch(_call_event("a"), handler.handle_event)

    group = handler.current_tool_group
    assert group is not None
    call_widget = handler.tool_calls["a"]
    mount_call = _mount_call_for(mount_callback, call_widget)
    # First child of an empty group is mounted via the container kwarg.
    assert mount_call.kwargs.get("container") is group
    assert not isinstance(call_widget, ToolGroup)
    assert isinstance(call_widget, ToolCallMessage)
