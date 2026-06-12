from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.stubs.fake_tool import FakeTool, FakeToolArgs
import vibe.cli.textual_ui.handlers.event_handler as event_handler_module
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.widgets.messages import HookSystemMessageLine
from vibe.core.hooks.models import (
    HookEndEvent,
    HookMessageSeverity,
    HookRunEndEvent,
    HookRunStartEvent,
    HookStartEvent,
    HookType,
)
from vibe.core.types import ToolCallEvent, ToolResultEvent


class FakeHookRunContainer:
    def __init__(self) -> None:
        self.display = False
        self.remove = AsyncMock()
        self.messages: list[HookSystemMessageLine] = []
        self.classes: set[str] = set()

    def add_class(self, cls: str) -> None:
        self.classes.add(cls)

    async def add_message(self, widget: HookSystemMessageLine) -> None:
        self.display = True
        self.messages.append(widget)


@pytest.fixture
def hook_container_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> list[FakeHookRunContainer]:
    created: list[FakeHookRunContainer] = []

    def make_container() -> FakeHookRunContainer:
        container = FakeHookRunContainer()
        created.append(container)
        return container

    monkeypatch.setattr(event_handler_module, "HookRunContainer", make_container)
    return created


@pytest.mark.asyncio
async def test_hook_run_end_removes_empty_container(
    hook_container_factory: list[FakeHookRunContainer],
) -> None:
    mount_callback = AsyncMock()
    handler = EventHandler(
        mount_callback=mount_callback, get_tools_collapsed=lambda: False
    )

    await handler.handle_event(HookRunStartEvent())
    await handler.handle_event(HookRunEndEvent())

    assert len(hook_container_factory) == 1
    hook_container_factory[0].remove.assert_awaited_once()
    assert "agent_turn" not in handler._hook_containers


@pytest.mark.asyncio
async def test_hook_run_end_keeps_container_with_messages(
    hook_container_factory: list[FakeHookRunContainer],
) -> None:
    mount_callback = AsyncMock()
    handler = EventHandler(
        mount_callback=mount_callback, get_tools_collapsed=lambda: False
    )

    await handler.handle_event(HookRunStartEvent())
    await handler.handle_event(HookStartEvent(hook_name="post-turn"))
    await handler.handle_event(
        HookEndEvent(
            hook_name="post-turn", status=HookMessageSeverity.OK, content="Hook output"
        )
    )
    await handler.handle_event(HookRunEndEvent())

    assert len(hook_container_factory) == 1
    hook_container_factory[0].remove.assert_not_awaited()
    assert hook_container_factory[0].display is True
    assert "agent_turn" not in handler._hook_containers


def _tool_call_event(call_id: str) -> ToolCallEvent:
    return ToolCallEvent(
        tool_name="stub_tool",
        tool_class=FakeTool,
        args=FakeToolArgs(),
        tool_call_id=call_id,
    )


def _tool_result_event(call_id: str) -> ToolResultEvent:
    return ToolResultEvent(
        tool_name="stub_tool",
        tool_class=FakeTool,
        result=None,
        cancelled=False,
        tool_call_id=call_id,
    )


@pytest.mark.asyncio
async def test_before_tool_container_scoped_to_tool_call_id(
    hook_container_factory: list[FakeHookRunContainer],
) -> None:
    """Two concurrent tool calls each get their own before_tool container."""
    mount_callback = AsyncMock()
    handler = EventHandler(
        mount_callback=mount_callback, get_tools_collapsed=lambda: False
    )

    # Two tool calls arrive
    await handler.handle_event(_tool_call_event("call_A"))
    await handler.handle_event(_tool_call_event("call_B"))

    # before_tool starts for both, interleaved
    await handler.handle_event(
        HookRunStartEvent(
            scope=HookType.BEFORE_TOOL, tool_name="stub_tool", tool_call_id="call_A"
        )
    )
    await handler.handle_event(
        HookRunStartEvent(
            scope=HookType.BEFORE_TOOL, tool_name="stub_tool", tool_call_id="call_B"
        )
    )

    # Two distinct containers were created.
    assert len(hook_container_factory) == 2
    assert "before_tool:call_A" in handler._hook_containers
    assert "before_tool:call_B" in handler._hook_containers
    assert (
        handler._hook_containers["before_tool:call_A"]
        is not handler._hook_containers["before_tool:call_B"]
    )

    # End them — both are empty, both should be removed.
    await handler.handle_event(
        HookRunEndEvent(scope=HookType.BEFORE_TOOL, tool_call_id="call_A")
    )
    await handler.handle_event(
        HookRunEndEvent(scope=HookType.BEFORE_TOOL, tool_call_id="call_B")
    )
    assert "before_tool:call_A" not in handler._hook_containers
    assert "before_tool:call_B" not in handler._hook_containers


@pytest.mark.asyncio
async def test_after_tool_container_anchors_after_tool_result(
    hook_container_factory: list[FakeHookRunContainer],
) -> None:
    mount_callback = AsyncMock()
    handler = EventHandler(
        mount_callback=mount_callback, get_tools_collapsed=lambda: False
    )

    await handler.handle_event(_tool_call_event("call_X"))
    await handler.handle_event(_tool_result_event("call_X"))
    # After-tool start mounts after the tool result, which is the current
    # anchor for this tool_call_id.
    await handler.handle_event(
        HookRunStartEvent(
            scope=HookType.AFTER_TOOL, tool_name="stub_tool", tool_call_id="call_X"
        )
    )
    assert "after_tool:call_X" in handler._hook_containers

    # mount_callback was called with after=<tool_result_widget> for the after_tool
    # container. Inspect the last call's kwargs.
    last_call = mount_callback.call_args_list[-1]
    assert last_call.kwargs.get("after") is not None


@pytest.mark.asyncio
async def test_before_tool_container_for_unknown_tool_call_id(
    hook_container_factory: list[FakeHookRunContainer],
) -> None:
    mount_callback = AsyncMock()
    handler = EventHandler(
        mount_callback=mount_callback, get_tools_collapsed=lambda: False
    )

    await handler.handle_event(
        HookRunStartEvent(
            scope=HookType.BEFORE_TOOL, tool_name="bash", tool_call_id="unknown"
        )
    )
    last_call = mount_callback.call_args_list[-1]
    assert last_call.kwargs.get("after") is None
    assert last_call.kwargs.get("before") is None


@pytest.mark.asyncio
async def test_before_tool_container_mounts_before_tool_call_widget(
    hook_container_factory: list[FakeHookRunContainer],
) -> None:
    mount_callback = AsyncMock()
    handler = EventHandler(
        mount_callback=mount_callback, get_tools_collapsed=lambda: False
    )

    await handler.handle_event(_tool_call_event("call_Y"))
    tool_call_widget = handler._tool_call_anchors["call_Y"]

    await handler.handle_event(
        HookRunStartEvent(
            scope=HookType.BEFORE_TOOL, tool_name="stub_tool", tool_call_id="call_Y"
        )
    )

    last_call = mount_callback.call_args_list[-1]
    assert last_call.kwargs.get("before") is tool_call_widget
    assert last_call.kwargs.get("after") is None


@pytest.mark.asyncio
async def test_before_tool_run_end_keeps_tool_call_widget_as_anchor(
    hook_container_factory: list[FakeHookRunContainer],
) -> None:
    mount_callback = AsyncMock()
    handler = EventHandler(
        mount_callback=mount_callback, get_tools_collapsed=lambda: False
    )

    await handler.handle_event(_tool_call_event("call_Z"))
    tool_call_widget = handler._tool_call_anchors["call_Z"]

    await handler.handle_event(
        HookRunStartEvent(
            scope=HookType.BEFORE_TOOL, tool_name="stub_tool", tool_call_id="call_Z"
        )
    )
    await handler.handle_event(HookStartEvent(hook_name="before"))
    await handler.handle_event(
        HookEndEvent(hook_name="before", status=HookMessageSeverity.OK, content="ok")
    )
    await handler.handle_event(
        HookRunEndEvent(scope=HookType.BEFORE_TOOL, tool_call_id="call_Z")
    )

    # Even though the before_tool container has content and stays in the DOM,
    # the anchor for the next widget (the tool result) must remain the call
    # widget — the container lives *above* the call, not below it.
    assert handler._tool_call_anchors["call_Z"] is tool_call_widget


@pytest.mark.asyncio
async def test_hook_end_routes_to_matching_container_when_chains_interleave(
    hook_container_factory: list[FakeHookRunContainer],
) -> None:
    mount_callback = AsyncMock()
    handler = EventHandler(
        mount_callback=mount_callback, get_tools_collapsed=lambda: False
    )

    await handler.handle_event(_tool_call_event("call_A"))
    await handler.handle_event(_tool_call_event("call_B"))

    await handler.handle_event(
        HookRunStartEvent(
            scope=HookType.BEFORE_TOOL, tool_name="stub_tool", tool_call_id="call_A"
        )
    )
    assert len(hook_container_factory) == 1
    container_a = hook_container_factory[-1]

    await handler.handle_event(HookStartEvent(hook_name="hook_a"))
    await handler.handle_event(
        HookRunStartEvent(
            scope=HookType.BEFORE_TOOL, tool_name="stub_tool", tool_call_id="call_B"
        )
    )
    assert len(hook_container_factory) == 2
    container_b = hook_container_factory[-1]
    assert container_a is not container_b

    await handler.handle_event(
        HookEndEvent(
            hook_name="hook_a",
            status=HookMessageSeverity.OK,
            content="from A",
            scope=HookType.BEFORE_TOOL,
            tool_call_id="call_A",
        )
    )
    await handler.handle_event(HookStartEvent(hook_name="hook_b"))
    await handler.handle_event(
        HookEndEvent(
            hook_name="hook_b",
            status=HookMessageSeverity.OK,
            content="from B",
            scope=HookType.BEFORE_TOOL,
            tool_call_id="call_B",
        )
    )

    assert len(container_a.messages) == 1
    assert len(container_b.messages) == 1
    assert container_a.messages[0]._content == "from A"
    assert container_b.messages[0]._content == "from B"


@pytest.mark.asyncio
async def test_hook_end_after_other_chain_ended_still_routes_correctly(
    hook_container_factory: list[FakeHookRunContainer],
) -> None:
    mount_callback = AsyncMock()
    handler = EventHandler(
        mount_callback=mount_callback, get_tools_collapsed=lambda: False
    )

    await handler.handle_event(_tool_call_event("call_A"))
    await handler.handle_event(_tool_call_event("call_B"))
    await handler.handle_event(
        HookRunStartEvent(
            scope=HookType.BEFORE_TOOL, tool_name="stub_tool", tool_call_id="call_A"
        )
    )
    container_a = hook_container_factory[-1]
    await handler.handle_event(HookStartEvent(hook_name="hook_a"))

    await handler.handle_event(
        HookRunStartEvent(
            scope=HookType.BEFORE_TOOL, tool_name="stub_tool", tool_call_id="call_B"
        )
    )
    await handler.handle_event(HookStartEvent(hook_name="hook_b"))
    await handler.handle_event(
        HookEndEvent(
            hook_name="hook_b",
            status=HookMessageSeverity.OK,
            content="from B",
            scope=HookType.BEFORE_TOOL,
            tool_call_id="call_B",
        )
    )
    await handler.handle_event(
        HookRunEndEvent(scope=HookType.BEFORE_TOOL, tool_call_id="call_B")
    )

    await handler.handle_event(
        HookEndEvent(
            hook_name="hook_a",
            status=HookMessageSeverity.OK,
            content="from A",
            scope=HookType.BEFORE_TOOL,
            tool_call_id="call_A",
        )
    )

    assert len(container_a.messages) == 1
    assert container_a.messages[0]._content == "from A"
