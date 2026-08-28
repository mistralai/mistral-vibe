from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from vibe.app_server._legacy_session_runtime import LegacySessionRuntimeController
from vibe.app_server.models import SessionTitleUpdatedNoticeDetail
from vibe.core.types import SessionTitleUpdatedEvent


class _RecordingServices:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    async def notify(self, method: str, params: Any) -> None:
        self.calls.append((method, params))


def _controller(session_id: str, services: _RecordingServices) -> Any:
    controller: Any = object.__new__(LegacySessionRuntimeController)
    controller._services = services
    controller._root = SimpleNamespace(
        session=SimpleNamespace(agent_loop=SimpleNamespace(session_id=session_id))
    )
    return controller


@pytest.mark.asyncio
async def test_notify_title_patches_the_projection_and_emits_the_terminal_notice() -> (
    None
):
    # The background title is session metadata: one /title projection patch (so
    # every projection consumer, e.g. ACP, sees it live) plus the CLI-owned
    # terminal notice. Both from this single drain owner.
    services = _RecordingServices()
    controller = _controller("session-1", services)

    await controller._notify_title(
        SessionTitleUpdatedEvent(title="Generated title", session_id="session-1")
    )

    assert [method for method, _ in services.calls] == [
        "session/updated",
        "history/entryAdded",
    ]

    _, session_updated = services.calls[0]
    assert len(session_updated.patch) == 1
    patch = session_updated.patch[0]
    assert (patch.op, patch.path, patch.value) == (
        "replace",
        "/title",
        "Generated title",
    )

    _, entry_added = services.calls[1]
    detail = entry_added.entry.detail
    assert isinstance(detail, SessionTitleUpdatedNoticeDetail)
    assert detail.title == "Generated title"


@pytest.mark.asyncio
async def test_notify_title_drops_a_title_for_a_reset_session() -> None:
    # A reset (/new, /clear) swaps in a new session id while a title for the old
    # one is still queued; that stale title must not revive on the new session.
    services = _RecordingServices()
    controller = _controller("session-2", services)

    await controller._notify_title(
        SessionTitleUpdatedEvent(title="Old title", session_id="session-1")
    )

    assert services.calls == []


def _drain_controller(agent_loop: Any) -> Any:
    controller: Any = object.__new__(LegacySessionRuntimeController)
    controller._title_drain_task = None
    controller._title_drain_loop = None
    controller._root = SimpleNamespace(session=SimpleNamespace(agent_loop=agent_loop))

    async def _never() -> None:
        await asyncio.Event().wait()

    # Shadow the real drain so the task is inert (never touches a real queue).
    controller._run_title_drain = _never
    return controller


@pytest.mark.asyncio
async def test_restart_title_drain_reuses_the_drain_for_the_same_loop() -> None:
    loop = object()
    controller = _drain_controller(loop)

    controller._restart_title_drain()
    first = controller._title_drain_task
    assert first is not None

    # Same loop: no second consumer is spawned on the one out-of-band queue.
    controller._restart_title_drain()
    assert controller._title_drain_task is first

    first.cancel()


@pytest.mark.asyncio
async def test_restart_title_drain_rebinds_when_the_loop_changes() -> None:
    controller = _drain_controller(object())
    controller._restart_title_drain()
    first = controller._title_drain_task
    assert first is not None

    # A resume swaps in a new agent loop: the drain rebinds to the new queue.
    controller._root = SimpleNamespace(session=SimpleNamespace(agent_loop=object()))
    controller._restart_title_drain()
    second = controller._title_drain_task
    assert second is not first

    await asyncio.sleep(0)
    assert first.cancelled()
    second.cancel()
