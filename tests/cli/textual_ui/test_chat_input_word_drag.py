from __future__ import annotations

import pytest
from textual import events
from textual.widgets.text_area import Selection

from tests.conftest import build_test_vibe_app
from vibe.cli.commands import CommandRegistry
from vibe.cli.textual_ui.widgets.chat_input import ChatTextArea


def _make_text_area(text: str) -> ChatTextArea:
    return ChatTextArea(command_registry=CommandRegistry(), text=text)


def _mouse_move(ta: ChatTextArea, x: int, y: int = 0) -> events.MouseMove:
    origin = ta.region.offset
    return events.MouseMove(
        widget=ta,
        x=origin.x + x,
        y=origin.y + y,
        delta_x=x,
        delta_y=y,
        button=1,
        shift=False,
        meta=False,
        ctrl=False,
    )


def test_word_boundary_around_middle_of_word() -> None:
    ta = _make_text_area("hello world foo")
    assert ta._word_boundary_around((0, 2)) == ((0, 0), (0, 5))


def test_word_boundary_around_space_selects_trailing_word() -> None:
    ta = _make_text_area("hello world foo")
    assert ta._word_boundary_around((0, 5)) == ((0, 0), (0, 5))


def test_word_boundary_around_punctuation_returns_none() -> None:
    ta = _make_text_area(",,")
    assert ta._word_boundary_around((0, 1)) is None


def test_snap_word_drag_forward_extends_to_word_end() -> None:
    ta = _make_text_area("hello world foo")
    assert ta._snap_word_drag((0, 2), (0, 8)) == ((0, 0), (0, 11))


def test_snap_word_drag_backward_extends_to_word_start() -> None:
    ta = _make_text_area("hello world foo")
    assert ta._snap_word_drag((0, 8), (0, 2)) == ((0, 0), (0, 11))


def test_snap_paragraph_drag_forward_spans_whole_paragraphs() -> None:
    ta = _make_text_area("hello world\nbar baz")
    assert ta._snap_paragraph_drag((0, 2), (1, 2)) == ((0, 0), (1, 7))


def test_snap_paragraph_drag_backward_spans_whole_paragraphs() -> None:
    ta = _make_text_area("hello world\nbar baz")
    assert ta._snap_paragraph_drag((1, 2), (0, 2)) == ((0, 0), (1, 7))


@pytest.mark.asyncio
async def test_double_click_selects_word_at_mouse_down() -> None:
    app = build_test_vibe_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        ta = app.query_one(ChatTextArea)
        ta.focus()
        ta.load_text("hello world foo")
        await pilot.pause(0.2)

        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_up(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)

        assert ta.selection == Selection((0, 0), (0, 5))
        assert ta.selected_text == "hello"


@pytest.mark.asyncio
async def test_double_click_then_drag_forward_extends_by_word() -> None:
    app = build_test_vibe_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        ta = app.query_one(ChatTextArea)
        ta.focus()
        ta.load_text("hello world foo")
        await pilot.pause(0.2)

        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_up(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        assert ta.selection == Selection((0, 0), (0, 5))

        app.screen._forward_event(_mouse_move(ta, 8))
        await pilot.pause(0.05)
        assert ta.selection == Selection((0, 0), (0, 11))
        assert ta.selected_text == "hello world"

        await pilot.mouse_up(ta, offset=(8, 0))
        await pilot.pause(0.05)
        assert ta.selection == Selection((0, 0), (0, 11))


@pytest.mark.asyncio
async def test_double_click_then_drag_backward_extends_by_word() -> None:
    app = build_test_vibe_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        ta = app.query_one(ChatTextArea)
        ta.focus()
        ta.load_text("hello world foo")
        await pilot.pause(0.2)

        await pilot.mouse_down(ta, offset=(8, 0))
        await pilot.pause(0.05)
        await pilot.mouse_up(ta, offset=(8, 0))
        await pilot.pause(0.05)
        await pilot.mouse_down(ta, offset=(8, 0))
        await pilot.pause(0.05)
        assert ta.selection == Selection((0, 6), (0, 11))

        app.screen._forward_event(_mouse_move(ta, 2))
        await pilot.pause(0.05)
        assert ta.selection == Selection((0, 0), (0, 11))
        assert ta.selected_text == "hello world"

        await pilot.mouse_up(ta, offset=(2, 0))
        await pilot.pause(0.05)
        assert ta.selection == Selection((0, 0), (0, 11))


@pytest.mark.asyncio
async def test_triple_click_then_drag_extends_by_line() -> None:
    app = build_test_vibe_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        ta = app.query_one(ChatTextArea)
        ta.focus()
        ta.load_text("hello world\nbar baz")
        await pilot.pause(0.2)

        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_up(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_up(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        assert ta.selection == Selection((0, 0), (0, 11))

        app.screen._forward_event(_mouse_move(ta, 2, 1))
        await pilot.pause(0.05)
        assert ta.selection == Selection((0, 0), (1, 7))

        await pilot.mouse_up(ta, offset=(2, 1))
        await pilot.pause(0.05)
        assert ta.selection == Selection((0, 0), (1, 7))


@pytest.mark.asyncio
async def test_single_click_drag_uses_char_granularity() -> None:
    app = build_test_vibe_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        ta = app.query_one(ChatTextArea)
        ta.focus()
        ta.load_text("hello world foo")
        await pilot.pause(0.2)

        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        app.screen._forward_event(_mouse_move(ta, 8))
        await pilot.pause(0.05)

        assert ta.selection == Selection((0, 2), (0, 8))


@pytest.mark.asyncio
async def test_drag_end_resets_chain_so_next_click_is_char() -> None:
    app = build_test_vibe_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        ta = app.query_one(ChatTextArea)
        ta.focus()
        ta.load_text("hello world foo")
        await pilot.pause(0.2)

        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_up(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        app.screen._forward_event(_mouse_move(ta, 8))
        await pilot.pause(0.05)
        await pilot.mouse_up(ta, offset=(8, 0))
        await pilot.pause(0.05)
        assert ta._click_chain == 0

        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        assert ta._click_chain == 1
        assert ta.selection == Selection.cursor((0, 2))


@pytest.mark.asyncio
async def test_click_chain_loops_after_triple_click() -> None:
    app = build_test_vibe_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        ta = app.query_one(ChatTextArea)
        ta.focus()
        ta.load_text("hello world foo")
        await pilot.pause(0.2)

        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_up(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        assert ta._click_chain == 2
        await pilot.mouse_up(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        assert ta._click_chain == 3
        await pilot.mouse_up(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        assert ta._click_chain == 1
        assert ta.selection == Selection.cursor((0, 2))
        await pilot.mouse_up(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        assert ta._click_chain == 2
        await pilot.mouse_up(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        assert ta._click_chain == 3


@pytest.mark.asyncio
async def test_pause_after_double_click_resets_to_char() -> None:
    app = build_test_vibe_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        ta = app.query_one(ChatTextArea)
        ta.focus()
        ta.load_text("hello world foo")
        await pilot.pause(0.2)

        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_up(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        assert ta._click_chain == 2
        await pilot.mouse_up(ta, offset=(2, 0))
        await pilot.pause(0.05)

        ta._last_down_time = (ta._last_down_time or 0.0) - 1.0

        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        assert ta._click_chain == 1
        assert ta.selection == Selection.cursor((0, 2))


@pytest.mark.asyncio
async def test_jitter_within_word_does_not_break_click_chain() -> None:
    """A mouse move that stays within the same word must not set _dragged,
    so the click chain survives for the next triple-click. Without the fix,
    _expand_drag_selection set _dragged=True unconditionally on any move,
    which made on_mouse_up reset _click_chain to 0.
    """
    app = build_test_vibe_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        ta = app.query_one(ChatTextArea)
        ta.focus()
        ta.load_text("hello world foo")
        await pilot.pause(0.2)

        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_up(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        assert ta._click_chain == 2
        assert ta.selection == Selection((0, 0), (0, 5))

        # Jitter within "hello" (offset 2 -> offset 3, same word).
        app.screen._forward_event(_mouse_move(ta, 3))
        await pilot.pause(0.05)

        assert ta._dragged is False
        assert ta._click_chain == 2

        await pilot.mouse_up(ta, offset=(3, 0))
        await pilot.pause(0.05)

        # Chain survived — next click should be a triple-click (chain 3).
        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        assert ta._click_chain == 3


@pytest.mark.asyncio
async def test_drag_back_to_anchor_shrinks_selection() -> None:
    """After extending a word drag forward, moving back to the anchor must
    shrink the selection back to the anchor word. Without the fix, the
    target != anchor guard skipped _expand_drag_selection, leaving the
    extended selection stuck.
    """
    app = build_test_vibe_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        ta = app.query_one(ChatTextArea)
        ta.focus()
        ta.load_text("hello world foo")
        await pilot.pause(0.2)

        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_up(ta, offset=(2, 0))
        await pilot.pause(0.05)
        await pilot.mouse_down(ta, offset=(2, 0))
        await pilot.pause(0.05)
        assert ta.selection == Selection((0, 0), (0, 5))

        # Drag forward to extend selection.
        app.screen._forward_event(_mouse_move(ta, 8))
        await pilot.pause(0.05)
        assert ta.selection == Selection((0, 0), (0, 11))

        # Drag back to the anchor — selection should shrink back.
        app.screen._forward_event(_mouse_move(ta, 2))
        await pilot.pause(0.05)
        assert ta.selection == Selection((0, 0), (0, 5))


@pytest.mark.asyncio
async def test_right_click_does_not_advance_click_chain() -> None:
    """Right-click (button != 1) must not advance the click chain or move
    the cursor in ChatTextArea. Without the guard, a right-click would
    promote the next left-click into a word/paragraph select.
    """
    app = build_test_vibe_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        ta = app.query_one(ChatTextArea)
        ta.focus()
        ta.load_text("hello world foo")
        await pilot.pause(0.2)

        right_click = events.MouseDown(
            widget=ta,
            x=ta.region.offset.x + 2,
            y=ta.region.offset.y,
            delta_x=0,
            delta_y=0,
            button=2,
            shift=False,
            meta=False,
            ctrl=False,
        )
        await ta._on_mouse_down(right_click)
        await pilot.pause(0.05)

        assert ta._click_chain == 0
        assert ta._dragged is False
        assert ta._selecting is False
