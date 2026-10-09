from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from vibe.app_server._effect_models import FileReadEffectDetail
from vibe.app_server.events import HistoryEntryAdded
from vibe.app_server.models import (
    CompletedEffectState,
    EffectResultDisplay,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    PublicMessageEntry,
    PublicNoticeEntry,
    ScheduledLoopFiredNoticeDetail,
    TextContentBlock,
)
from vibe.cli.textual_ui.handlers.event_handler import EventHandler
from vibe.cli.textual_ui.widgets.fired_loop import FiredLoop
from vibe.cli.textual_ui.widgets.messages import UserCommandMessage, UserMessage
from vibe.cli.textual_ui.widgets.tools import ToolGroup
from vibe.user_content import UserDisplayContent
from vibe.utils.tool_presentation import EffectCallDisplay

FIRED_AT_MS = 1_787_593_260_000


def _prompt(
    entry_id: str, turn_id: str, text: str, *, marked: bool = False
) -> PublicMessageEntry:
    display = (
        UserDisplayContent(
            version="1",
            host="vibe",
            content=[
                {
                    "type": "vibe.scheduled_loop",
                    "loopId": "2158d161",
                    "firedAt": FIRED_AT_MS,
                }
            ],
        )
        if marked
        else None
    )
    return PublicMessageEntry(
        id=entry_id,
        session_id="session-1",
        turn_id=turn_id,
        created_at=1,
        updated_at=1,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        role="user",
        content=[TextContentBlock(text=text)],
        source="turn_start",
        user_display_content=display,
    )


def _fired(turn_id: str) -> PublicNoticeEntry:
    return PublicNoticeEntry(
        id=f"scheduled-loop:{turn_id}",
        session_id="session-1",
        turn_id=turn_id,
        created_at=FIRED_AT_MS,
        updated_at=FIRED_AT_MS,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        level="info",
        message="Loop `2158d161` fired",
        detail=ScheduledLoopFiredNoticeDetail(loop_id="2158d161"),
    )


def _skill_effect(turn_id: str) -> PublicEffectEntry:
    return PublicEffectEntry(
        id="skill-effect",
        session_id="session-1",
        turn_id=turn_id,
        created_at=1,
        updated_at=1,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        title="Loaded skill",
        detail=FileReadEffectDetail(
            tool_name="skill",
            display=EffectCallDisplay(summary="Loaded skill", status_text="Loading"),
        ),
        state=CompletedEffectState(
            duration_ms=1.0, display=EffectResultDisplay(success=True, message="ok")
        ),
    )


def _handler(mount: AsyncMock) -> EventHandler:
    return EventHandler(mount_callback=mount, get_tools_collapsed=lambda: False)


def _mounted(mount: AsyncMock) -> list[object]:
    return [call.args[0] for call in mount.await_args_list]


@pytest.mark.asyncio
async def test_marked_fired_prompt_shows_before_the_rest_of_its_turn() -> None:
    mount = AsyncMock()
    handler = _handler(mount)

    await handler.handle_event(
        HistoryEntryAdded(_prompt("u1", "t1", "/review", marked=True))
    )
    await handler.handle_event(HistoryEntryAdded(_skill_effect("t1")))
    await handler.handle_event(HistoryEntryAdded(_fired("t1")))

    mounted = _mounted(mount)
    assert isinstance(mounted[0], UserMessage)
    assert mounted[0].get_content() == "/review"
    assert mounted[0].fired_loop == FiredLoop("2158d161", FIRED_AT_MS)
    assert any(isinstance(widget, ToolGroup) for widget in mounted[1:])
    assert not any(
        isinstance(widget, UserMessage | UserCommandMessage) for widget in mounted[1:]
    )


@pytest.mark.asyncio
async def test_fired_notice_shows_an_unmarked_prompt_annotated_with_the_loop() -> None:
    mount = AsyncMock()
    handler = _handler(mount)

    await handler.handle_event(HistoryEntryAdded(_prompt("u1", "t1", "Run the linter")))
    await handler.handle_event(HistoryEntryAdded(_fired("t1")))

    mounted = _mounted(mount)
    assert not any(isinstance(widget, UserCommandMessage) for widget in mounted)
    prompts = [widget for widget in mounted if isinstance(widget, UserMessage)]
    assert [(prompt.get_content(), prompt.history_entry_id) for prompt in prompts] == [
        ("Run the linter", "u1")
    ]
    assert prompts[0].fired_loop == FiredLoop("2158d161", FIRED_AT_MS)


@pytest.mark.asyncio
async def test_fired_loop_without_a_same_turn_prompt_shows_nothing() -> None:
    mount = AsyncMock()
    handler = _handler(mount)

    await handler.handle_event(HistoryEntryAdded(_prompt("u0", "t0", "earlier")))
    await handler.handle_event(HistoryEntryAdded(_fired("t1")))

    assert not any(
        isinstance(widget, UserMessage | UserCommandMessage)
        for widget in _mounted(mount)
    )
