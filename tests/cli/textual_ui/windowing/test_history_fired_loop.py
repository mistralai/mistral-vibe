from __future__ import annotations

from weakref import WeakKeyDictionary

from vibe.app_server.models import (
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicNoticeEntry,
    ScheduledLoopFiredNoticeDetail,
    TextContentBlock,
)
from vibe.cli.textual_ui.widgets.fired_loop import FiredLoop
from vibe.cli.textual_ui.widgets.messages import UserMessage
from vibe.cli.textual_ui.windowing.history import build_history_widgets
from vibe.user_content import UserDisplayContent


def _prompt(
    entry_id: str,
    turn_id: str | None,
    text: str,
    *,
    display: UserDisplayContent | None = None,
) -> PublicMessageEntry:
    return PublicMessageEntry(
        id=entry_id,
        session_id="s1",
        turn_id=turn_id,
        created_at=1,
        updated_at=1,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        role="user",
        content=[TextContentBlock(text=text)],
        user_display_content=display,
    )


def _fired(turn_id: str, created_at: int) -> PublicNoticeEntry:
    return PublicNoticeEntry(
        id=f"scheduled-loop-{turn_id}",
        session_id="s1",
        turn_id=turn_id,
        created_at=created_at,
        updated_at=created_at,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        level="info",
        message="Loop `abc` fired",
        detail=ScheduledLoopFiredNoticeDetail(loop_id="abc"),
    )


def _marker(loop_id: str, fired_at: int) -> UserDisplayContent:
    return UserDisplayContent(
        version="1",
        host="vibe",
        content=[
            {"type": "vibe.scheduled_loop", "loopId": loop_id, "firedAt": fired_at}
        ],
    )


def _prompts(batch: list[PublicHistoryEntry]) -> list[tuple[str, FiredLoop | None]]:
    widgets = build_history_widgets(
        batch,
        start_index=0,
        history_widget_indices=WeakKeyDictionary(),
        tools_collapsed=False,
    )
    return [
        (widget.get_content(), widget.fired_loop)
        for widget in widgets
        if isinstance(widget, UserMessage)
    ]


def test_resumed_fired_prompt_keeps_its_loop_from_the_display_marker() -> None:
    prompt = _prompt("imported-1", None, "Run the linter", display=_marker("abc", 42))

    assert _prompts([prompt]) == [("Run the linter", FiredLoop("abc", 42))]


def test_resumed_fired_prompt_takes_its_loop_from_the_same_turn_notice() -> None:
    batch: list[PublicHistoryEntry] = [
        _prompt("u0", "t0", "earlier"),
        _prompt("u1", "t1", "Run the linter"),
        _fired("t1", 1_787_593_260_000),
    ]

    assert _prompts(batch) == [
        ("earlier", None),
        ("Run the linter", FiredLoop("abc", 1_787_593_260_000)),
    ]


def test_resumed_marker_wins_over_the_same_turn_notice_as_it_does_live() -> None:
    batch: list[PublicHistoryEntry] = [
        _prompt("u1", "t1", "Run the linter", display=_marker("abc", 42)),
        _fired("t1", 1_787_593_260_000),
    ]

    assert _prompts(batch) == [("Run the linter", FiredLoop("abc", 42))]


def test_fired_notice_from_another_turn_annotates_no_prompt() -> None:
    batch: list[PublicHistoryEntry] = [
        _prompt("u0", "t0", "earlier"),
        _fired("t1", 1_787_593_260_000),
    ]

    assert _prompts(batch) == [("earlier", None)]
