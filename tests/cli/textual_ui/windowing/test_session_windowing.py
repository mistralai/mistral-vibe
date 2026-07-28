from __future__ import annotations

from vibe.app_server.models import (
    PublicCheckpointEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    TextContentBlock,
)
from vibe.cli.textual_ui.windowing.state import LOAD_MORE_BATCH_SIZE, SessionWindowing


def _message(index: int, content: str = "x") -> PublicMessageEntry:
    return PublicMessageEntry(
        id=f"message-{index}",
        session_id="session-1",
        created_at=index,
        updated_at=index,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        role="user",
        content=[TextContentBlock(text=content)],
        source="harness",
    )


def _checkpoint(index: int) -> PublicCheckpointEntry:
    return PublicCheckpointEntry(
        id=f"checkpoint-{index}",
        session_id="session-1",
        created_at=index,
        updated_at=index,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        kind="internal",
    )


def test_recompute_backfill_keeps_cursor_when_oldest_widgets_skip_entry_prefix() -> (
    None
):
    w = SessionWindowing(LOAD_MORE_BATCH_SIZE)
    w.set_backfill([_message(index) for index in range(80)])
    assert w.remaining == 80

    history: list[PublicHistoryEntry] = [
        *[_message(index) for index in range(80)],
        *[_checkpoint(index) for index in range(80, 85)],
        _message(85, content="visible"),
    ]
    visible_indices = [85]
    has_backfill = w.recompute_backfill(
        history, visible_indices=visible_indices, visible_history_widgets_count=1
    )
    assert has_backfill
    assert w.remaining == 80


def test_recompute_backfill_advances_cursor_when_prefix_was_pruned() -> None:
    history: list[PublicHistoryEntry] = [_message(index) for index in range(100)]
    w = SessionWindowing(LOAD_MORE_BATCH_SIZE)
    w.set_backfill(history[:70])
    assert w.remaining == 70

    has_backfill = w.recompute_backfill(
        history, visible_indices=[80], visible_history_widgets_count=10
    )
    assert has_backfill
    assert w.remaining == 80
