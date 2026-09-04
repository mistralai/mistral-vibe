from __future__ import annotations

from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from vibe.app_server.events import SessionUpdated
from vibe.app_server.models import IdleSessionStatus, PublicSession
from vibe.cli.textual_ui.handlers.event_handler import EventHandler


def _session(title: str | None) -> PublicSession:
    return PublicSession(
        id="session-1",
        status=IdleSessionStatus(),
        created_at=1,
        updated_at=1,
        title=title,
    )


def _handler(on_title: Callable[[str], None]) -> EventHandler:
    return EventHandler(
        mount_callback=AsyncMock(),
        get_tools_collapsed=lambda: False,
        on_session_title_changed=on_title,
    )


@pytest.mark.asyncio
async def test_session_updated_reports_new_title() -> None:
    titles: list[str] = []
    handler = _handler(titles.append)

    await handler.handle_event(
        SessionUpdated(
            previous=_session(None), session=_session("Fix the webhook"), patch=[]
        )
    )

    assert titles == ["Fix the webhook"]


@pytest.mark.asyncio
async def test_session_updated_ignores_unchanged_title() -> None:
    titles: list[str] = []
    handler = _handler(titles.append)

    await handler.handle_event(
        SessionUpdated(previous=_session("Same"), session=_session("Same"), patch=[])
    )

    assert titles == []


@pytest.mark.asyncio
async def test_session_updated_ignores_cleared_title() -> None:
    titles: list[str] = []
    handler = _handler(titles.append)

    await handler.handle_event(
        SessionUpdated(previous=_session("Old"), session=_session(None), patch=[])
    )

    assert titles == []
