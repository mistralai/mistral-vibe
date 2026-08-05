from __future__ import annotations

from typing import Any, ClassVar, cast

import pytest
from textual import events
from textual.geometry import Offset, Region
from textual.screen import Screen
from textual.selection import SELECT_ALL, SelectEnd, Selection, SelectStart, SelectState
from textual.widget import Widget

from vibe.cli.textual_ui.word_selection import (
    SelectGranularity,
    WordSelectScreen,
    _DragDirection,
)


def _make_select_state(
    screen: WordSelectScreen,
    widget: Widget,
    start_offset: Offset,
    end_offset: Offset,
    screen_offset: Offset | None = None,
) -> SelectState:
    """Helper to create a SelectState for testing."""
    if screen_offset is None:
        screen_offset = end_offset
    return SelectState(
        screen_offset,
        SelectStart(
            container=widget,
            container_pointer_delta=Offset(0, 0),
            container_initial_offset=Offset(0, 0),
            container_initial_scroll_offset=Offset(0, 0),
            content_widget=widget,
            content_offset=start_offset,
        ),
        SelectEnd(container=widget, content_widget=widget, content_offset=end_offset),
    )


class FakeSelectableWidget(Widget):
    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text
        self._lines = text.split("\n")

    def _index_of(self, offset: Offset | None) -> int | None:
        if offset is None:
            return None
        index = 0
        for line in self._lines[: offset.y]:
            index += len(line) + 1
        index += offset.x
        return index

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        start = self._index_of(selection.start)
        end = self._index_of(selection.end)
        if start is None:
            start = 0
        if end is None:
            end = len(self._text)
        if start > end:
            start, end = end, start
        return (self._text[start:end], self._text[end:])


def _make_screen() -> WordSelectScreen:
    return WordSelectScreen(id="_default")


def _make_mouse_down(x: int, y: int, button: int = 1) -> events.MouseDown:
    return events.MouseDown(
        widget=None,
        x=x,
        y=y,
        delta_x=0,
        delta_y=0,
        button=button,
        shift=False,
        meta=False,
        ctrl=False,
    )


def _make_mouse_up(x: int, y: int) -> events.MouseUp:
    return events.MouseUp(
        widget=None,
        x=x,
        y=y,
        delta_x=0,
        delta_y=0,
        button=1,
        shift=False,
        meta=False,
        ctrl=False,
    )


def _make_mouse_move(x: int, y: int) -> events.MouseMove:
    return events.MouseMove(
        widget=None,
        x=x,
        y=y,
        delta_x=0,
        delta_y=0,
        button=0,
        shift=False,
        meta=False,
        ctrl=False,
    )


# Screen.selections is an async reactive; assigning it without a running app
# leaves its watcher coroutine un-awaited (RuntimeWarning at GC). These tests
# assert on the stored dict, not the watcher side-effect, so the autouse fixture
# below stubs the watcher to a sync no-op to keep output clean.


@pytest.fixture(autouse=True)
def _stub_async_selections_watcher(monkeypatch: pytest.MonkeyPatch) -> None:
    def _sync_watch(
        self: Screen, old: dict[Widget, Selection], new: dict[Widget, Selection]
    ) -> None:
        return None

    monkeypatch.setattr(Screen, "_watch_selections", _sync_watch)


def test_word_selection_at_middle_of_word_spans_whole_word() -> None:
    widget = FakeSelectableWidget("hello world foo\nbar baz")
    selection = WordSelectScreen._selection_around(
        widget, Offset(2, 0), SelectGranularity.WORD
    )
    assert selection == Selection.from_offsets(Offset(0, 0), Offset(5, 0))


def test_word_selection_at_space_spans_adjacent_word() -> None:
    widget = FakeSelectableWidget("hello world foo\nbar baz")
    selection = WordSelectScreen._selection_around(
        widget, Offset(5, 0), SelectGranularity.WORD
    )
    assert selection == Selection.from_offsets(Offset(0, 0), Offset(5, 0))


def test_word_selection_at_word_start_spans_word() -> None:
    widget = FakeSelectableWidget("hello world foo\nbar baz")
    selection = WordSelectScreen._selection_around(
        widget, Offset(0, 0), SelectGranularity.WORD
    )
    assert selection == Selection.from_offsets(Offset(0, 0), Offset(5, 0))


def test_word_selection_at_word_end_spans_word() -> None:
    widget = FakeSelectableWidget("hello world foo\nbar baz")
    selection = WordSelectScreen._selection_around(
        widget, Offset(4, 0), SelectGranularity.WORD
    )
    assert selection == Selection.from_offsets(Offset(0, 0), Offset(5, 0))


def test_word_selection_at_punctuation_gap_returns_none() -> None:
    widget = FakeSelectableWidget(",,")
    assert (
        WordSelectScreen._selection_around(widget, Offset(1, 0), SelectGranularity.WORD)
        is None
    )


def test_paragraph_selection_at_offset_on_first_line_spans_paragraph() -> None:
    widget = FakeSelectableWidget("hello world foo\nbar baz")
    selection = WordSelectScreen._selection_around(
        widget, Offset(3, 0), SelectGranularity.PARAGRAPH
    )
    assert selection == Selection.from_offsets(Offset(0, 0), Offset(15, 0))


def test_paragraph_selection_at_offset_on_second_line_spans_paragraph() -> None:
    widget = FakeSelectableWidget("hello world foo\nbar baz")
    selection = WordSelectScreen._selection_around(
        widget, Offset(2, 1), SelectGranularity.PARAGRAPH
    )
    assert selection == Selection.from_offsets(Offset(0, 1), Offset(7, 1))


def test_paragraph_selection_at_empty_paragraph_spans_empty_paragraph() -> None:
    widget = FakeSelectableWidget("foo\n\nbar")
    selection = WordSelectScreen._selection_around(
        widget, Offset(0, 1), SelectGranularity.PARAGRAPH
    )
    assert selection == Selection.from_offsets(Offset(0, 1), Offset(0, 1))


def test_on_mouse_down_first_click_uses_char_granularity() -> None:
    screen = _make_screen()
    screen.on_mouse_down(_make_mouse_down(3, 0))
    assert screen._drag_granularity == SelectGranularity.CHAR
    assert screen._drag_anchor is None
    assert screen._click_chain == 1


def test_on_mouse_down_right_click_does_not_advance_click_chain() -> None:
    screen = _make_screen()
    screen.on_mouse_down(_make_mouse_down(2, 0, button=2))
    assert screen._click_chain == 0
    assert screen._drag_granularity == SelectGranularity.CHAR
    assert screen._drag_anchor is None


def test_on_mouse_down_second_click_at_same_offset_sets_word_granularity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world")
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    screen.on_mouse_down(_make_mouse_down(2, 0))
    assert screen._click_chain == 1
    assert screen._drag_granularity == SelectGranularity.CHAR
    screen.on_mouse_up(_make_mouse_up(2, 0))
    assert screen._click_chain == 1
    assert screen._drag_granularity == SelectGranularity.CHAR
    screen.on_mouse_down(_make_mouse_down(2, 0))
    assert screen._click_chain == 2
    assert screen._drag_granularity == SelectGranularity.WORD
    assert screen._drag_anchor == Offset(2, 0)
    assert screen._drag_anchor_widget is widget


def test_on_mouse_down_third_click_sets_line_granularity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world")
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    assert screen._click_chain == 3
    assert screen._drag_granularity == SelectGranularity.PARAGRAPH
    assert screen._drag_anchor == Offset(2, 0)
    assert screen._drag_anchor_widget is widget


def test_on_mouse_down_second_click_at_different_offset_resets_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world foo")
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(8, 0))
    assert screen._drag_granularity == SelectGranularity.CHAR
    assert screen._click_chain == 1


def test_on_mouse_up_resets_granularity_preserves_click_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world")
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    assert screen._drag_granularity == SelectGranularity.WORD
    assert screen._drag_anchor == Offset(2, 0)
    screen.on_mouse_up(_make_mouse_up(2, 0))
    assert screen._drag_granularity == SelectGranularity.CHAR
    assert screen._drag_anchor is None
    assert screen._drag_anchor_widget is None
    assert screen._click_chain == 2


def test_click_chain_loops_after_triple_click(monkeypatch: pytest.MonkeyPatch) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world foo")
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    assert screen._drag_granularity == SelectGranularity.WORD
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    assert screen._drag_granularity == SelectGranularity.PARAGRAPH
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    assert screen._drag_granularity == SelectGranularity.CHAR
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    assert screen._drag_granularity == SelectGranularity.WORD
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    assert screen._drag_granularity == SelectGranularity.PARAGRAPH


def test_pending_reapply_picks_word_then_paragraph_after_wrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world foo")
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    monkeypatch.setattr(Screen, "_forward_event", lambda self, event: None)
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen._forward_event(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen._forward_event(_make_mouse_up(2, 0))
    assert screen.selections[widget] == Selection.from_offsets(
        Offset(0, 0), Offset(5, 0)
    )
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen._forward_event(_make_mouse_up(2, 0))
    assert screen.selections[widget] == Selection.from_offsets(
        Offset(0, 0), Offset(15, 0)
    )
    # click 4: wrap to char — chain resets and consumed flag clears so the
    # cycle can advance again; reapply is skipped (CHAR sets no anchor).
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen._forward_event(_make_mouse_up(2, 0))
    assert screen._click_chain == 1
    assert screen._chain_consumed is False
    # click 5: word again — reapply restores WORD after the wrap.
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen._forward_event(_make_mouse_up(2, 0))
    assert screen.selections[widget] == Selection.from_offsets(
        Offset(0, 0), Offset(5, 0)
    )
    # click 6: paragraph again — reapply restores PARAGRAPH after the wrap.
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen._forward_event(_make_mouse_up(2, 0))
    assert screen.selections[widget] == Selection.from_offsets(
        Offset(0, 0), Offset(15, 0)
    )


def test_double_click_then_drag_snaps_to_word(monkeypatch: pytest.MonkeyPatch) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world foo")
    monkeypatch.setattr(Screen, "_forward_event", lambda self, event: None)
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    assert screen._drag_granularity == SelectGranularity.WORD
    screen._selecting = True
    screen._select_state = _make_select_state(
        screen, widget, Offset(2, 0), Offset(8, 0)
    )
    screen._forward_event(_make_mouse_move(8, 0))
    assert screen.selections[widget] == Selection.from_offsets(
        Offset(0, 0), Offset(11, 0)
    )


def test_double_click_then_drag_with_widget_at_non_origin_screen_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world foo")
    monkeypatch.setattr(Screen, "_forward_event", lambda self, event: None)
    # Widget origin sits at screen row 5, col 0: widget-local offset is screen
    # coords shifted by -5 rows. This is the configuration that masks the
    # coordinate-space bug when _drag_anchor is stored in screen coords.
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y - 5))
    )
    screen.on_mouse_down(_make_mouse_down(2, 5))
    screen.on_mouse_up(_make_mouse_up(2, 5))
    screen.on_mouse_down(_make_mouse_down(2, 5))
    assert screen._drag_granularity == SelectGranularity.WORD
    assert screen._drag_anchor == Offset(2, 0)
    screen._selecting = True
    screen._select_state = _make_select_state(
        screen, widget, Offset(2, 0), Offset(8, 0), screen_offset=Offset(8, 5)
    )
    screen._forward_event(_make_mouse_move(8, 5))
    assert screen.selections[widget] == Selection.from_offsets(
        Offset(0, 0), Offset(11, 0)
    )


def test_triple_click_then_drag_snaps_to_line(monkeypatch: pytest.MonkeyPatch) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world\nbar baz")
    monkeypatch.setattr(Screen, "_forward_event", lambda self, event: None)
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    assert screen._drag_granularity == SelectGranularity.PARAGRAPH
    screen._selecting = True
    screen._select_state = _make_select_state(
        screen, widget, Offset(2, 0), Offset(2, 1)
    )
    screen._forward_event(_make_mouse_move(2, 1))
    assert screen.selections[widget] == Selection.from_offsets(
        Offset(0, 0), Offset(7, 1)
    )


def test_expand_drag_selection_word_granularity_snaps_both_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world foo")
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    screen._drag_granularity = SelectGranularity.WORD
    screen._drag_anchor = Offset(2, 0)
    screen._drag_anchor_widget = widget
    screen._select_state = _make_select_state(
        screen, widget, Offset(2, 0), Offset(8, 0)
    )

    screen._expand_drag_selection()

    assert widget in screen.selections
    assert screen.selections[widget] == Selection.from_offsets(
        Offset(0, 0), Offset(11, 0)
    )


def test_expand_drag_selection_word_backward_drag_includes_anchor_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world foo")
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    screen._drag_granularity = SelectGranularity.WORD
    screen._drag_anchor = Offset(8, 0)
    screen._drag_anchor_widget = widget
    screen._select_state = _make_select_state(
        screen, widget, Offset(8, 0), Offset(2, 0)
    )

    screen._expand_drag_selection()

    assert screen.selections[widget] == Selection.from_offsets(
        Offset(0, 0), Offset(11, 0)
    )


def test_expand_drag_selection_line_granularity_spans_whole_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world\nbar baz")
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    screen._drag_granularity = SelectGranularity.PARAGRAPH
    screen._drag_anchor = Offset(2, 0)
    screen._drag_anchor_widget = widget
    screen._select_state = _make_select_state(
        screen, widget, Offset(2, 0), Offset(2, 1)
    )

    screen._expand_drag_selection()

    assert screen.selections[widget] == Selection.from_offsets(
        Offset(0, 0), Offset(7, 1)
    )


def test_expand_drag_selection_skips_when_anchor_widget_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world")
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    screen._drag_granularity = SelectGranularity.WORD
    screen._drag_anchor = None
    screen._drag_anchor_widget = widget

    screen._expand_drag_selection()

    assert screen.selections == {}


def test_expand_drag_selection_skips_when_current_widget_differs_from_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world")
    other = FakeSelectableWidget("other text")
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    screen._drag_granularity = SelectGranularity.WORD
    screen._drag_anchor = Offset(2, 0)
    screen._drag_anchor_widget = other

    screen._expand_drag_selection()

    assert screen.selections == {}


def test_expand_drag_selection_cross_widget_forward_snaps_both_widgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    anchor_widget = FakeSelectableWidget("hello world")
    current_widget = FakeSelectableWidget("foo bar baz")
    monkeypatch.setattr(screen, "_drag_direction", lambda _: _DragDirection.FORWARD)
    screen._drag_granularity = SelectGranularity.WORD
    screen._drag_anchor = Offset(2, 0)
    screen._drag_anchor_widget = anchor_widget
    screen._select_state = SelectState(
        Offset(8, 0),
        SelectStart(
            container=anchor_widget,
            container_pointer_delta=Offset(0, 0),
            container_initial_offset=Offset(0, 0),
            container_initial_scroll_offset=Offset(0, 0),
            content_widget=anchor_widget,
            content_offset=Offset(2, 0),
        ),
        SelectEnd(
            container=current_widget,
            content_widget=current_widget,
            content_offset=Offset(2, 0),
        ),
    )
    screen.selections = {anchor_widget: SELECT_ALL, current_widget: SELECT_ALL}

    screen._expand_drag_selection()

    # Anchor: start snapped to word start, end stays (None = end of widget)
    assert screen.selections[anchor_widget] == Selection(Offset(0, 0), None)
    # Current: end snapped to word end, start stays (None = start of widget)
    assert screen.selections[current_widget] == Selection(None, Offset(3, 0))


def test_expand_drag_selection_cross_widget_backward_snaps_both_widgets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    anchor_widget = FakeSelectableWidget("hello world")
    current_widget = FakeSelectableWidget("foo bar baz")
    monkeypatch.setattr(screen, "_drag_direction", lambda _: _DragDirection.BACKWARD)
    screen._drag_granularity = SelectGranularity.WORD
    screen._drag_anchor = Offset(2, 0)
    screen._drag_anchor_widget = anchor_widget
    screen._select_state = SelectState(
        Offset(0, 0),
        SelectStart(
            container=anchor_widget,
            container_pointer_delta=Offset(0, 0),
            container_initial_offset=Offset(0, 0),
            container_initial_scroll_offset=Offset(0, 0),
            content_widget=anchor_widget,
            content_offset=Offset(2, 0),
        ),
        SelectEnd(
            container=current_widget,
            content_widget=current_widget,
            content_offset=Offset(2, 0),
        ),
    )
    screen.selections = {anchor_widget: SELECT_ALL, current_widget: SELECT_ALL}

    screen._expand_drag_selection()

    # Anchor: end snapped to word end, start stays (None = start of widget)
    assert screen.selections[anchor_widget] == Selection(None, Offset(5, 0))
    # Current: start snapped to word start, end stays (None = end of widget)
    assert screen.selections[current_widget] == Selection(Offset(0, 0), None)


def test_forward_event_post_processes_mouse_move_with_word_granularity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world foo")
    monkeypatch.setattr(Screen, "_forward_event", lambda self, event: None)
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    screen._selecting = True
    screen._drag_granularity = SelectGranularity.WORD
    screen._drag_anchor = Offset(2, 0)
    screen._drag_anchor_widget = widget
    screen._select_state = _make_select_state(
        screen, widget, Offset(2, 0), Offset(8, 0)
    )

    screen._forward_event(_make_mouse_move(8, 0))

    assert screen.selections[widget] == Selection.from_offsets(
        Offset(0, 0), Offset(11, 0)
    )


def test_forward_event_skips_when_not_selecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world foo")
    monkeypatch.setattr(Screen, "_forward_event", lambda self, event: None)
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    screen._selecting = False
    screen._drag_granularity = SelectGranularity.WORD
    screen._drag_anchor = Offset(2, 0)
    screen._drag_anchor_widget = widget

    screen._forward_event(_make_mouse_move(8, 0))

    assert screen.selections == {}


def test_forward_event_skips_non_mouse_move_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world foo")
    monkeypatch.setattr(Screen, "_forward_event", lambda self, event: None)
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    screen._selecting = True
    screen._drag_granularity = SelectGranularity.WORD
    screen._drag_anchor = Offset(2, 0)
    screen._drag_anchor_widget = widget

    screen._forward_event(_make_mouse_down(8, 0))

    assert screen.selections == {}


@pytest.mark.asyncio
async def test_real_double_click_then_drag_snaps_to_word(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.conftest import build_test_vibe_app

    app = build_test_vibe_app()

    async with app.run_test() as pilot:
        screen = cast(WordSelectScreen, app.screen)
        widget = FakeSelectableWidget("hello world foo")
        await screen.mount(widget)
        await pilot.pause()

        widget_origin = widget.region.offset

        await pilot.mouse_down(widget, offset=Offset(2, 0))
        await pilot.pause()
        await pilot.mouse_up(widget, offset=Offset(2, 0))
        await pilot.pause()
        await pilot.mouse_down(widget, offset=Offset(2, 0))
        await pilot.pause()

        assert screen._drag_granularity == SelectGranularity.WORD
        assert screen._drag_anchor == Offset(2, 0)
        assert screen._drag_anchor_widget is widget

        move = events.MouseMove(
            widget=widget,
            x=widget_origin.x + 8,
            y=widget_origin.y,
            delta_x=6,
            delta_y=0,
            button=1,
            shift=False,
            meta=False,
            ctrl=False,
        )
        screen._forward_event(move)
        await pilot.pause()

        assert widget in screen.selections
        assert screen.selections[widget] == Selection.from_offsets(
            Offset(0, 0), Offset(11, 0)
        )


def _make_click(x: int, y: int, chain: int = 1) -> events.Click:
    return events.Click(
        widget=None,
        x=x,
        y=y,
        delta_x=0,
        delta_y=0,
        button=1,
        shift=False,
        meta=False,
        ctrl=False,
        chain=chain,
    )


def test_forward_event_suppresses_click_for_selectable_widget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world")
    forwarded: list[events.Event] = []
    monkeypatch.setattr(
        Screen, "_forward_event", lambda self, event: forwarded.append(event)
    )
    monkeypatch.setattr(
        screen, "get_widget_at", lambda x, y: (widget, Region(0, 0, 10, 10))
    )

    screen._forward_event(_make_click(2, 0, chain=1))
    screen._forward_event(_make_click(2, 0, chain=2))
    screen._forward_event(_make_click(2, 0, chain=3))

    assert len(forwarded) == 1


def test_forward_event_forwards_multi_click_for_non_selectable_widget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world")
    monkeypatch.setattr(type(widget), "ALLOW_SELECT", False)
    forwarded: list[events.Event] = []
    monkeypatch.setattr(
        Screen, "_forward_event", lambda self, event: forwarded.append(event)
    )
    monkeypatch.setattr(
        screen, "get_widget_at", lambda x, y: (widget, Region(0, 0, 10, 10))
    )

    screen._forward_event(_make_click(2, 0, chain=2))
    screen._forward_event(_make_click(2, 0, chain=3))

    assert len(forwarded) == 2


def test_forward_event_forwards_single_click(monkeypatch: pytest.MonkeyPatch) -> None:
    screen = _make_screen()
    forwarded: list[events.Event] = []
    monkeypatch.setattr(
        Screen, "_forward_event", lambda self, event: forwarded.append(event)
    )

    screen._forward_event(_make_click(2, 0, chain=1))

    assert len(forwarded) == 1


def test_pending_reapply_restores_word_selection_after_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world foo")
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    monkeypatch.setattr(Screen, "_forward_event", lambda self, event: None)
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen._forward_event(_make_mouse_up(2, 0))

    assert widget in screen.selections
    assert screen.selections[widget] == Selection.from_offsets(
        Offset(0, 0), Offset(5, 0)
    )


def test_pending_reapply_restores_line_selection_after_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world\nbar baz")
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    monkeypatch.setattr(Screen, "_forward_event", lambda self, event: None)
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen._forward_event(_make_mouse_up(2, 0))

    assert widget in screen.selections
    assert screen.selections[widget] == Selection.from_offsets(
        Offset(0, 0), Offset(11, 0)
    )


def test_dragged_flag_prevents_reapply_from_overwriting_extended_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world foo")
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    monkeypatch.setattr(Screen, "_forward_event", lambda self, event: None)
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen._selecting = True
    screen._select_state = _make_select_state(
        screen, widget, Offset(2, 0), Offset(8, 0)
    )
    screen._forward_event(_make_mouse_move(8, 0))
    assert screen._dragged is True
    screen._forward_event(_make_mouse_up(8, 0))

    assert screen.selections[widget] == Selection.from_offsets(
        Offset(0, 0), Offset(11, 0)
    )


def test_drag_end_resets_click_chain_so_next_click_is_char(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world foo")
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    monkeypatch.setattr(Screen, "_forward_event", lambda self, event: None)
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))
    assert screen._drag_granularity == SelectGranularity.WORD
    screen._selecting = True
    screen._select_state = _make_select_state(
        screen, widget, Offset(2, 0), Offset(8, 0)
    )
    screen._forward_event(_make_mouse_move(8, 0))
    assert screen._dragged is True
    screen.on_mouse_up(_make_mouse_up(8, 0))
    assert screen._click_chain == 0
    assert screen._last_down_offset is None

    screen.on_mouse_down(_make_mouse_down(2, 0))
    assert screen._click_chain == 1
    assert screen._drag_granularity == SelectGranularity.CHAR


def test_apply_selection_skips_when_widget_disallows_select(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world")
    monkeypatch.setattr(type(widget), "ALLOW_SELECT", False)
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))

    assert screen._drag_granularity == SelectGranularity.CHAR
    assert screen._drag_anchor is None
    assert screen._drag_anchor_widget is None
    assert screen.selections == {}


def test_apply_selection_skips_when_screen_disallows_select(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    widget = FakeSelectableWidget("hello world")
    monkeypatch.setattr(
        screen, "get_widget_and_offset_at", lambda x, y: (widget, Offset(x, y))
    )
    monkeypatch.setattr(WordSelectScreen, "ALLOW_SELECT", False)
    screen.on_mouse_down(_make_mouse_down(2, 0))
    screen.on_mouse_up(_make_mouse_up(2, 0))
    screen.on_mouse_down(_make_mouse_down(2, 0))

    assert screen._drag_granularity == SelectGranularity.CHAR
    assert screen._drag_anchor is None
    assert screen._drag_anchor_widget is None
    assert screen.selections == {}


class _FakeInteractive(Widget):
    """A widget that defines its own on_click (interactive transcript widget)."""

    async def on_click(self, event: events.Click) -> None:
        pass


def test_interactive_ancestor_walks_to_on_click_parent() -> None:
    leaf = FakeSelectableWidget("hello")
    parent = _FakeInteractive()
    # Bypass mount but link the parent so ancestors_with_self reaches it.
    cast("Any", leaf)._parent = parent

    assert WordSelectScreen._interactive_ancestor(leaf) is parent


def test_interactive_ancestor_returns_none_for_pure_text_leaf() -> None:
    leaf = FakeSelectableWidget("hello")

    assert WordSelectScreen._interactive_ancestor(leaf) is None


def test_forward_event_dispatches_multi_click_to_interactive_ancestor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    leaf = FakeSelectableWidget("hello")
    parent = _FakeInteractive()
    cast("Any", leaf)._parent = parent
    dispatched: list[events.Event] = []
    monkeypatch.setattr(parent, "_forward_event", dispatched.append)
    super_forwarded: list[events.Event] = []
    monkeypatch.setattr(
        Screen, "_forward_event", lambda self, event: super_forwarded.append(event)
    )
    monkeypatch.setattr(screen, "get_widget_at", lambda x, y: (leaf, Offset(0, 0)))

    screen._forward_event(_make_click(2, 0, chain=2))
    screen._forward_event(_make_click(2, 0, chain=3))

    assert len(dispatched) == 2
    assert super_forwarded == []


def test_forward_event_still_suppresses_click_for_pure_text_selectable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screen = _make_screen()
    leaf = FakeSelectableWidget("hello")
    super_forwarded: list[events.Event] = []
    monkeypatch.setattr(
        Screen, "_forward_event", lambda self, event: super_forwarded.append(event)
    )
    monkeypatch.setattr(screen, "get_widget_at", lambda x, y: (leaf, Offset(0, 0)))

    screen._forward_event(_make_click(2, 0, chain=1))
    screen._forward_event(_make_click(2, 0, chain=2))
    screen._forward_event(_make_click(2, 0, chain=3))

    # chain==1 falls through to super; chain>=2 is suppressed so the leaf's
    # Widget._on_click never calls text_select_all() and clobbers the
    # word/paragraph selection set at MouseDown.
    assert len(super_forwarded) == 1


@pytest.mark.asyncio
async def test_rapid_multi_clicks_toggle_on_every_click_through_selectable_leaf() -> (
    None
):
    """Regression: rapid clicks on a ClickWithoutDragMixin widget whose leaf
    child is selectable must toggle on every click, not just the first.

    ``pilot.click`` always produces ``chain == 1`` per call, so this forwards
    synthetic ``Click`` events with ``chain=2``/``chain=3`` directly (the way
    Textual's driver does after rapid mouse-up at the same offset).
    """
    from textual.app import App, ComposeResult
    from textual.widgets import Static

    from vibe.cli.textual_ui.widgets.collapsible import ClickWithoutDragMixin

    class _Leaf(Static):
        pass

    class _Toggle(ClickWithoutDragMixin, Static):
        def __init__(self) -> None:
            super().__init__("header")
            self.clicks = 0
            self.passive: list[bool] = []

        async def on_click(self, event: events.Click) -> None:
            self.passive.append(self._click_is_passive(event))
            self.clicks += 1

    class _App(App[None]):
        def get_default_screen(self) -> Screen:
            return WordSelectScreen(id="_default")

        def compose(self) -> ComposeResult:
            yield _Toggle()

        def on_mount(self) -> None:
            self.toggle = self.query_one(_Toggle)
            self.leaf = _Leaf("body")
            self.toggle.mount(self.leaf)

    app = _App()
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        screen = cast(WordSelectScreen, app.screen)
        toggle = app.toggle
        leaf = app.leaf
        origin = leaf.region.offset
        x, y = origin.x + 1, origin.y

        for chain in (2, 3, 2, 3):
            screen._forward_event(
                events.MouseDown(
                    widget=leaf,
                    x=x,
                    y=y,
                    delta_x=0,
                    delta_y=0,
                    button=1,
                    shift=False,
                    meta=False,
                    ctrl=False,
                )
            )
            await pilot.pause()
            screen._forward_event(
                events.Click(
                    widget=leaf,
                    x=x,
                    y=y,
                    delta_x=0,
                    delta_y=0,
                    button=1,
                    shift=False,
                    meta=False,
                    ctrl=False,
                    chain=chain,
                )
            )
            await pilot.pause()
            screen._forward_event(
                events.MouseUp(
                    widget=leaf,
                    x=x,
                    y=y,
                    delta_x=0,
                    delta_y=0,
                    button=1,
                    shift=False,
                    meta=False,
                    ctrl=False,
                )
            )
            await pilot.pause()

        assert toggle.clicks == 4
        assert toggle.passive == [False, False, False, False]


@pytest.mark.asyncio
async def test_pure_text_static_keeps_word_selection_on_double_click() -> None:
    """Regression guard: pure-text selectable widgets (no on_click) still set
    word/paragraph selection on multi-click, and the chain>=2 Click is
    suppressed so Widget._on_click's text_select_all does not clobber it.
    """
    from textual.app import App, ComposeResult
    from textual.widgets import Static

    class _App(App[None]):
        def get_default_screen(self) -> Screen:
            return WordSelectScreen(id="_default")

        def compose(self) -> ComposeResult:
            yield Static("hello world foo", id="text")

    app = _App()
    async with app.run_test(size=(40, 5)) as pilot:
        await pilot.pause()
        text = app.query_one("#text", Static)
        screen = cast(WordSelectScreen, app.screen)
        origin = text.region.offset
        x, y = origin.x + 2, origin.y

        # First click (down/up) sets chain=1.
        screen._forward_event(
            events.MouseDown(
                widget=text,
                x=x,
                y=y,
                delta_x=0,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
            )
        )
        await pilot.pause()
        screen._forward_event(
            events.MouseUp(
                widget=text,
                x=x,
                y=y,
                delta_x=0,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
            )
        )
        await pilot.pause()
        # Second mouse-down advances chain to 2 -> WORD selection set at MouseDown.
        screen._forward_event(
            events.MouseDown(
                widget=text,
                x=x,
                y=y,
                delta_x=0,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
            )
        )
        await pilot.pause()
        assert screen._drag_granularity == SelectGranularity.WORD
        assert text in screen.selections
        # MouseUp clears then reapply restores it; the chain>=2 Click that
        # follows is suppressed so Widget._on_click's text_select_all cannot
        # clobber the word selection.
        screen._forward_event(
            events.MouseUp(
                widget=text,
                x=x,
                y=y,
                delta_x=0,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
            )
        )
        await pilot.pause()
        screen._forward_event(
            events.Click(
                widget=text,
                x=x,
                y=y,
                delta_x=0,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
                chain=2,
            )
        )
        await pilot.pause()

        assert text in screen.selections
        selection = screen.selections[text]
        assert (selection.start, selection.end) == (Offset(0, 0), Offset(5, 0))


@pytest.mark.asyncio
async def test_pre_existing_selection_does_not_block_toggle_on_multi_click() -> None:
    """When the user already has a selection and double-clicks an interactive
    widget, the toggle must still fire (chain>=2 = rapid toggle intent, not
    text-selection intent). The _had_selection_at_press reset ensures
    ClickWithoutDragMixin._click_is_passive doesn't swallow the toggle.
    """
    from textual.app import App, ComposeResult
    from textual.widgets import Static

    from vibe.cli.textual_ui.widgets.collapsible import ClickWithoutDragMixin

    class _Leaf(Static):
        pass

    class _Toggle(ClickWithoutDragMixin, Static):
        def __init__(self) -> None:
            super().__init__("header")
            self.clicks = 0

        async def on_click(self, event: events.Click) -> None:
            if self._click_is_passive(event):
                return
            self.clicks += 1

    class _App(App[None]):
        def get_default_screen(self) -> Screen:
            return WordSelectScreen(id="_default")

        def compose(self) -> ComposeResult:
            yield _Toggle()

        def on_mount(self) -> None:
            self.toggle = self.query_one(_Toggle)
            self.leaf = _Leaf("body")
            self.toggle.mount(self.leaf)

    app = _App()
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        screen = cast(WordSelectScreen, app.screen)
        toggle = app.toggle
        leaf = app.leaf
        origin = leaf.region.offset
        x, y = origin.x + 1, origin.y

        # Set a pre-existing selection on the leaf (simulating the user
        # having selected text, then rapidly double-clicking to toggle).
        from textual.selection import Selection as TextualSelection

        screen.selections = {leaf: TextualSelection(Offset(0, 0), Offset(3, 0))}
        await pilot.pause()

        screen._forward_event(
            events.MouseDown(
                widget=leaf,
                x=x,
                y=y,
                delta_x=0,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
            )
        )
        await pilot.pause()
        screen._forward_event(
            events.Click(
                widget=leaf,
                x=x,
                y=y,
                delta_x=0,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
                chain=2,
            )
        )
        await pilot.pause()
        screen._forward_event(
            events.MouseUp(
                widget=leaf,
                x=x,
                y=y,
                delta_x=0,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
            )
        )
        await pilot.pause()

        # Toggle fires despite the pre-existing selection.
        assert toggle.clicks == 1


@pytest.mark.asyncio
async def test_interactive_is_widget_falls_through_to_suppression() -> None:
    """When the leaf IS the interactive widget (e.g. ChatTextArea with
    ALLOW_SELECT=False), the `interactive is not widget` guard skips the
    forwarding branch and falls through to the existing allow_select
    suppression. This preserves prior behavior for same-widget on_click.
    """
    from textual.app import App, ComposeResult
    from textual.widgets import Static

    class _SelfInteractive(Static):
        """Widget that defines on_click on itself (leaf == interactive)."""

        ALLOW_SELECT: ClassVar[bool] = False
        clicks = 0

        async def on_click(self, event: events.Click) -> None:
            self.clicks += 1

    class _App(App[None]):
        def get_default_screen(self) -> Screen:
            return WordSelectScreen(id="_default")

        def compose(self) -> ComposeResult:
            yield _SelfInteractive("click me")

    app = _App()
    async with app.run_test(size=(40, 5)) as pilot:
        await pilot.pause()
        screen = cast(WordSelectScreen, app.screen)
        widget = app.query_one(_SelfInteractive)
        origin = widget.region.offset
        x, y = origin.x + 2, origin.y

        screen._forward_event(
            events.Click(
                widget=widget,
                x=x,
                y=y,
                delta_x=0,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
                chain=2,
            )
        )
        await pilot.pause()

        # on_click should fire via super()._forward_event (not the forwarding
        # branch), and no text_select_all should run (ALLOW_SELECT=False).
        assert widget.clicks == 1
        assert widget not in screen.selections


@pytest.mark.asyncio
async def test_jitter_within_word_does_not_break_click_chain_transcript() -> None:
    """A mouse move that stays within the same word must not set _dragged
    in the transcript path, so the click chain survives for the next
    triple-click. Without the fix, _expand_drag_selection set
    _dragged=True unconditionally on any move.
    """
    from textual.app import App, ComposeResult
    from textual.widgets import Static

    class _App(App[None]):
        def get_default_screen(self) -> Screen:
            return WordSelectScreen(id="_default")

        def compose(self) -> ComposeResult:
            yield Static("hello world foo", id="text")

    app = _App()
    async with app.run_test(size=(40, 5)) as pilot:
        await pilot.pause()
        screen = cast(WordSelectScreen, app.screen)
        text = app.query_one("#text", Static)
        origin = text.region.offset
        x, y = origin.x + 2, origin.y

        screen._forward_event(
            events.MouseDown(
                widget=text,
                x=x,
                y=y,
                delta_x=0,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
            )
        )
        await pilot.pause()
        screen._forward_event(
            events.MouseUp(
                widget=text,
                x=x,
                y=y,
                delta_x=0,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
            )
        )
        await pilot.pause()
        screen._forward_event(
            events.MouseDown(
                widget=text,
                x=x,
                y=y,
                delta_x=0,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
            )
        )
        await pilot.pause()
        assert screen._click_chain == 2
        assert screen._drag_granularity == SelectGranularity.WORD
        assert text in screen.selections

        # Jitter within "hello" (offset 2 -> offset 3, same word).
        screen._forward_event(
            events.MouseMove(
                widget=text,
                x=origin.x + 3,
                y=y,
                delta_x=1,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
            )
        )
        await pilot.pause()

        assert screen._dragged is False
        assert screen._click_chain == 2

        screen._forward_event(
            events.MouseUp(
                widget=text,
                x=origin.x + 3,
                y=y,
                delta_x=0,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
            )
        )
        await pilot.pause()

        # Chain survived — next click should be a triple-click (chain 3).
        screen._forward_event(
            events.MouseDown(
                widget=text,
                x=x,
                y=y,
                delta_x=0,
                delta_y=0,
                button=1,
                shift=False,
                meta=False,
                ctrl=False,
            )
        )
        await pilot.pause()
        assert screen._click_chain == 3
