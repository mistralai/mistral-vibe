from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from vibe.app_server.events import HistoryEntryAdded
from vibe.app_server.models import (
    PublicEntryGenerationStatus,
    PublicNoticeEntry,
    ScheduledLoopFiredNoticeDetail,
)
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.widgets.messages import UserCommandMessage


@pytest.mark.asyncio
async def test_scheduled_loop_notice_renders_as_user_command() -> None:
    mount = AsyncMock()
    handler = EventHandler(mount_callback=mount, get_tools_collapsed=lambda: False)
    entry = PublicNoticeEntry(
        id="scheduled-loop:turn-1",
        session_id="session-1",
        turn_id="turn-1",
        created_at=1,
        updated_at=1,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        level="info",
        message="Loop `2158d161` fired",
        detail=ScheduledLoopFiredNoticeDetail(loop_id="2158d161"),
    )

    await handler.handle_event(HistoryEntryAdded(entry))

    call = mount.await_args
    assert call is not None
    mounted = call.args[0]
    assert isinstance(mounted, UserCommandMessage)
    assert mounted._content == "Loop `2158d161` fired"
