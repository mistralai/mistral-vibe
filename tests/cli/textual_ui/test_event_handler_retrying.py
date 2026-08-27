from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from vibe.app_server.events import SessionSnapshot, TurnRetrying
from vibe.app_server.models import (
    IdleSessionStatus,
    PublicRetryCategory,
    PublicRetryState,
    PublicSession,
    PublicSessionState,
)
from vibe.app_server.protocol import TurnRetryingParams
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.widgets.loading import (
    DEFAULT_LOADING_STATUS,
    RETRYING_LOADING_STATUS,
    LoadingWidget,
)


def _snapshot(event_id: int, *, retrying: bool) -> SessionSnapshot:
    retry_state = None
    if retrying:
        retry_state = PublicRetryState(
            turn_id="turn-1",
            category=PublicRetryCategory.RATE_LIMITED,
            detail="HTTP 429",
        )
    return SessionSnapshot(
        PublicSessionState(
            event_id=event_id,
            session=PublicSession(
                id="session-1", status=IdleSessionStatus(), created_at=1, updated_at=1
            ),
            retrying=retry_state,
        )
    )


def _handler() -> EventHandler:
    return EventHandler(mount_callback=AsyncMock(), get_tools_collapsed=lambda: False)


@pytest.mark.asyncio
async def test_session_snapshots_control_retrying_status() -> None:
    handler = _handler()
    loading = LoadingWidget()

    await handler.handle_event(_snapshot(1, retrying=True), loading)
    assert loading._base_status == RETRYING_LOADING_STATUS

    await handler.handle_event(_snapshot(2, retrying=True), loading)
    assert loading._base_status == RETRYING_LOADING_STATUS

    await handler.handle_event(_snapshot(3, retrying=False), loading)
    assert loading._base_status == DEFAULT_LOADING_STATUS


@pytest.mark.asyncio
async def test_non_retrying_snapshot_preserves_specific_status() -> None:
    handler = _handler()
    loading = LoadingWidget()
    loading.set_status("Running hook")

    await handler.handle_event(_snapshot(1, retrying=False), loading)

    assert loading._base_status == "Running hook"


@pytest.mark.asyncio
async def test_legacy_retry_notification_does_not_control_status() -> None:
    handler = _handler()
    loading = LoadingWidget()
    loading.set_status("Running hook")

    await handler.handle_event(
        TurnRetrying(
            TurnRetryingParams(
                session_id="session-1",
                category=PublicRetryCategory.RATE_LIMITED,
                detail="HTTP 429",
            )
        ),
        loading,
    )

    assert loading._base_status == "Running hook"
