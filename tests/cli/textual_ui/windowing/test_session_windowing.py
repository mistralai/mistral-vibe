from __future__ import annotations

from vibe.cli.textual_ui.windowing.state import LOAD_MORE_BATCH_SIZE, SessionWindowing
from vibe.core.types import LLMMessage, Role


def _msg(
    role: Role = Role.user, *, content: str = "x", injected: bool = False
) -> LLMMessage:
    return LLMMessage(role=role, content=content, injected=injected)


def test_recompute_backfill_keeps_cursor_when_oldest_widgets_skip_injected_prefix() -> (
    None
):
    """min(visible_indices) can sit past injected-only tail slots; backfill must not overlap."""
    w = SessionWindowing(LOAD_MORE_BATCH_SIZE)
    w.set_backfill([_msg() for _ in range(80)])
    assert w.remaining == 80

    history = [
        *[_msg() for _ in range(80)],
        *[_msg(injected=True) for _ in range(5)],
        _msg(content="visible"),
    ]
    visible_indices = [85]
    has_backfill = w.recompute_backfill(
        history, visible_indices=visible_indices, visible_history_widgets_count=1
    )
    assert has_backfill
    assert w.remaining == 80


def test_recompute_backfill_advances_cursor_when_prefix_was_pruned_not_injected() -> (
    None
):
    """If DOM lost widgets for non-injected messages, align cursor with oldest remaining widget."""
    history = [_msg() for _ in range(100)]
    w = SessionWindowing(LOAD_MORE_BATCH_SIZE)
    w.set_backfill(history[:70])
    assert w.remaining == 70

    has_backfill = w.recompute_backfill(
        history, visible_indices=[80], visible_history_widgets_count=10
    )
    assert has_backfill
    assert w.remaining == 80
