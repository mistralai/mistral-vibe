from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import vibe.cli.textual_ui.handlers.event_handler as event_handler_module
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.core.hooks.models import (
    HookEndEvent,
    HookMessageSeverity,
    HookRunEndEvent,
    HookRunStartEvent,
)


class FakeHookRunContainer:
    def __init__(self) -> None:
        self.display = False
        self.remove = AsyncMock()

    async def add_message(self, _widget: object) -> None:
        self.display = True


@pytest.mark.asyncio
async def test_hook_run_end_removes_empty_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_containers: list[FakeHookRunContainer] = []

    def make_container() -> FakeHookRunContainer:
        container = FakeHookRunContainer()
        created_containers.append(container)
        return container

    monkeypatch.setattr(event_handler_module, "HookRunContainer", make_container)

    mount_callback = AsyncMock()
    handler = EventHandler(
        mount_callback=mount_callback, get_tools_collapsed=lambda: False
    )

    await handler.handle_event(HookRunStartEvent())
    await handler.handle_event(HookRunEndEvent())

    assert len(created_containers) == 1
    created_containers[0].remove.assert_awaited_once()
    assert handler._hook_run_container is None


@pytest.mark.asyncio
async def test_hook_run_end_keeps_container_with_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_containers: list[FakeHookRunContainer] = []

    def make_container() -> FakeHookRunContainer:
        container = FakeHookRunContainer()
        created_containers.append(container)
        return container

    monkeypatch.setattr(event_handler_module, "HookRunContainer", make_container)

    mount_callback = AsyncMock()
    handler = EventHandler(
        mount_callback=mount_callback, get_tools_collapsed=lambda: False
    )

    await handler.handle_event(HookRunStartEvent())
    await handler.handle_event(
        HookEndEvent(
            hook_name="post-turn", status=HookMessageSeverity.OK, content="Hook output"
        )
    )
    await handler.handle_event(HookRunEndEvent())

    assert len(created_containers) == 1
    created_containers[0].remove.assert_not_awaited()
    assert created_containers[0].display is True
    assert handler._hook_run_container is None
