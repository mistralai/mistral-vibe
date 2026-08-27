from __future__ import annotations

from enum import StrEnum, auto
import re

from textual import errors, events
from textual._context import NoActiveAppError
from textual.geometry import Offset
from textual.screen import Screen
from textual.selection import Selection, SelectState
from textual.widget import Widget

from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.collapsible import ClickWithoutDragMixin

_WORD = re.compile(r"\w+")
_TRAILING_WORD = re.compile(r"\w+$")
_DOUBLE_CLICK = 2
_TRIPLE_CLICK = 3
_DEFAULT_CLICK_CHAIN_TIME_THRESHOLD = 0.5


class SelectGranularity(StrEnum):
    CHAR = auto()
    WORD = auto()
    PARAGRAPH = auto()


class _DragDirection(StrEnum):
    FORWARD = auto()
    BACKWARD = auto()

    @property
    def opposite(self) -> _DragDirection:
        return (
            _DragDirection.BACKWARD
            if self is _DragDirection.FORWARD
            else _DragDirection.FORWARD
        )


class WordSelectScreen(Screen[None]):
    """Default screen that owns multi-click text selection.

    Selection is driven entirely from ``MouseDown``: a double-click selects
    the word under the cursor, a triple-click selects the paragraph, and
    dragging after either extends the selection whole-word or
    whole-paragraph. ``on_click`` is intentionally not overridden for
    multi-click — Textual's own ``_chained_clicks`` (which feeds
    ``event.chain``) does not cycle, so relying on it produced spurious
    paragraph selections after a double+triple sequence. Chain tracking here
    wraps at triple-click and resets on offset/time break.
    """

    _click_chain: int = 0
    _chain_consumed: bool = False
    _last_down_time: float | None = None
    _last_down_offset: Offset | None = None
    _drag_anchor: Offset | None = None
    _drag_anchor_widget: Widget | None = None
    _drag_granularity: SelectGranularity = SelectGranularity.CHAR
    _expanding: bool = False
    _dragged: bool = False
    _pending_reapply: bool = False
    _last_drag_selection: dict[Widget, Selection] | None = None

    async def _on_paste(self, event: events.Paste) -> None:
        # Drag-and-drop sends AppBlur → Paste → AppFocus; the Paste arrives
        # with no focused widget, so Textual routes it to the screen. Forward
        # it to the chat input instead of losing it.
        container = self.app.query_one(ChatInputContainer)
        input_widget = container.input_widget
        if input_widget is not None and not container.disabled:
            input_widget.post_message(event)
            event.stop()

    def get_widget_and_offset_at(
        self, x: int, y: int
    ) -> tuple[Widget | None, Offset | None]:
        widget, offset = super().get_widget_and_offset_at(x, y)
        # HACK: drop DOM-detached widgets so Screen._forward_event doesn't
        # crash on widget.parent.region. Remove once Textualize/textual#6643
        # is fixed upstream.
        if (
            widget is not None
            and widget.parent is None
            and not isinstance(widget, Screen)
        ):
            return None, None
        return widget, offset

    def _selection_allowed(self, widget: Widget | None) -> bool:
        if widget is None:
            return False
        if not (self.allow_select and widget.allow_select):
            return False
        try:
            return self.app.ALLOW_SELECT
        except NoActiveAppError:
            return True

    @staticmethod
    def _interactive_ancestor(widget: Widget) -> Widget | None:
        """Nearest ancestor (incl. self) that defines its own ``on_click``.

        ``Widget`` only defines ``_on_click`` (never ``on_click``), so
        ``hasattr`` is True solely for widgets that explicitly declare (or
        inherit) an ``on_click`` handler -- i.e. the interactive transcript
        widgets whose toggling was being swallowed by their selectable leaf
        children.
        """
        for ancestor in widget.ancestors_with_self:
            if isinstance(ancestor, Widget) and hasattr(ancestor, "on_click"):
                return ancestor
        return None

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 1:
            return
        self._dragged = False
        self._pending_reapply = False
        self._last_drag_selection = None
        offset = event.screen_offset
        now = event.time
        threshold = self._click_chain_threshold()
        within_threshold = (
            self._last_down_offset == offset
            and self._last_down_time is not None
            and now - self._last_down_time <= threshold
        )
        if not within_threshold:
            self._click_chain = 1
            self._chain_consumed = False
        elif self._chain_consumed:
            # Wrap the chain back to a fresh single click so the cycle
            # (char -> word -> paragraph) loops instead of sticking on CHAR.
            self._click_chain = 1
            self._chain_consumed = False
        else:
            self._click_chain += 1
        if self._click_chain >= _TRIPLE_CLICK:
            self._chain_consumed = True
        self._last_down_time = now
        self._last_down_offset = offset
        if self._click_chain >= _TRIPLE_CLICK:
            self._drag_granularity = SelectGranularity.PARAGRAPH
        elif self._click_chain == _DOUBLE_CLICK:
            self._drag_granularity = SelectGranularity.WORD
        else:
            self._drag_granularity = SelectGranularity.CHAR
        if self._drag_granularity in {
            SelectGranularity.WORD,
            SelectGranularity.PARAGRAPH,
        }:
            select_state = self._select_state
            widget, anchor_offset = self.get_widget_and_offset_at(*event.screen_offset)
            if (
                select_state is not None
                and select_state.start.content_offset is not None
                and select_state.start.content_widget is widget
            ):
                widget = select_state.start.content_widget
                anchor_offset = select_state.start.content_offset
            if not self._selection_allowed(widget):
                self._drag_granularity = SelectGranularity.CHAR
                self._drag_anchor = None
                self._drag_anchor_widget = None
            else:
                self._drag_anchor = anchor_offset
                self._drag_anchor_widget = widget
                self._select_at_anchor()
                self._pending_reapply = True
                self._last_drag_selection = dict(self.selections)
        else:
            self._drag_anchor = None
            self._drag_anchor_widget = None

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._dragged:
            self._pending_reapply = False
            # A completed drag ends the click chain so a quick follow-up click
            # starts a fresh character selection instead of continuing to
            # triple-click / line-select.
            self._click_chain = 0
            self._chain_consumed = False
            self._last_down_time = None
            self._last_down_offset = None
        self._drag_granularity = SelectGranularity.CHAR
        self._drag_anchor = None
        self._drag_anchor_widget = None
        self._dragged = False

    def _apply_selection(
        self,
        granularity: SelectGranularity,
        widget: Widget | None,
        offset: Offset | None,
    ) -> None:
        if widget is None or offset is None:
            return
        if not self._selection_allowed(widget):
            return
        selection = self._selection_around(widget, offset, granularity)
        if selection is not None:
            self.selections = {widget: selection}

    def _forward_event(self, event: events.Event) -> None:
        # Suppress Textual's Widget._on_click which calls text_select_all()
        # on double-click for selectable widgets. ChatTextArea (ALLOW_SELECT=False)
        # handles its own multi-click in _on_mouse_down; interactive ancestors
        # (collapsibles, etc.) get the Click forwarded via _interactive_ancestor.
        if isinstance(event, events.Click) and event.chain >= _DOUBLE_CLICK:
            widget: Widget | None = None
            region = None
            try:
                widget, region = self.get_widget_at(event.x, event.y)
            except errors.NoWidget:
                pass
            if widget is not None and region is not None:
                interactive = self._interactive_ancestor(widget)
                if (
                    interactive is not None
                    and interactive is not self
                    and interactive is not widget
                ):
                    # The leaf under the cursor is selectable but an ancestor
                    # owns on_click (collapsible, tool result, ...). Forward
                    # the click to that ancestor so its on_click toggles, and
                    # skip super() so Widget._on_click does not run on the
                    # leaf and call text_select_all() (which would set a
                    # selection that blocks the next toggle).
                    # event.widget stays the leaf, so the ancestor's
                    # Widget._on_click skips text_select_all()
                    # (its `event.widget is self` guard is False).
                    # Reset _had_selection_at_press so ClickWithoutDragMixin's
                    # _click_is_passive doesn't swallow the toggle just
                    # because word/paragraph selection was set at MouseDown.
                    if isinstance(interactive, ClickWithoutDragMixin):
                        interactive.clear_selection_state_for_click()
                    interactive._forward_event(
                        event._apply_offset(-region.x, -region.y)
                    )
                    return
                if widget.allow_select:
                    # Pure-text selectable widget with no on_click ancestor:
                    # swallow the click so Widget._on_click's text_select_all
                    # does not clobber the word/paragraph selection set at
                    # MouseDown (restored via _pending_reapply on MouseUp).
                    return
        super()._forward_event(event)
        # Textual's _forward_event(MouseUp) calls clear_selection() synchronously
        # when down and up offsets match (a click, not a drag), wiping the
        # word/line selection we set at MouseDown. _pending_reapply is set at
        # MouseDown; we restore the selection here, synchronously, so there
        # is no render gap where the selection is empty. If a drag happened,
        # _dragged is True and we leave the extended selection alone.
        if (
            isinstance(event, events.MouseUp)
            and self._pending_reapply
            and not self._dragged
        ):
            self._pending_reapply = False
            widget, offset = self.get_widget_and_offset_at(event.x, event.y)
            granularity = (
                SelectGranularity.WORD
                if self._click_chain == _DOUBLE_CLICK
                else SelectGranularity.PARAGRAPH
            )
            self._apply_selection(granularity, widget, offset)
        if self._expanding:
            return
        if not isinstance(event, events.MouseMove):
            return
        if not self._selecting:
            return
        if self._drag_granularity not in {
            SelectGranularity.WORD,
            SelectGranularity.PARAGRAPH,
        }:
            return
        self._expanding = True
        try:
            self._expand_drag_selection()
        finally:
            self._expanding = False

    def _select_at_anchor(self) -> None:
        self._apply_selection(
            self._drag_granularity, self._drag_anchor_widget, self._drag_anchor
        )

    def _expand_drag_selection(self) -> None:
        anchor = self._drag_anchor
        anchor_widget = self._drag_anchor_widget
        if anchor is None or anchor_widget is None:
            return
        select_state = self._select_state
        if select_state is None or select_state.end is None:
            return
        end = select_state.end
        current_widget = end.content_widget
        current_offset = end.content_offset
        if current_widget is anchor_widget and current_offset is not None:
            new_selection = {
                anchor_widget: self._snap_drag(anchor_widget, anchor, current_offset)
            }
            # Always overwrite — super()._forward_event(MouseMove) may have
            # set a character-level selection that we need to snap back.
            self.selections = new_selection
            if self._last_drag_selection != new_selection:
                self._last_drag_selection = new_selection
                self._dragged = True
            return
        if not self.selections:
            return
        new_selections = dict(self.selections)
        direction = self._drag_direction(select_state)
        if anchor_widget in new_selections:
            sel = new_selections[anchor_widget]
            snapped = self._snap_endpoint(anchor_widget, anchor, direction=direction)
            new_selections[anchor_widget] = self._replace_endpoint(
                sel, snapped, direction=direction
            )
        if (
            current_widget is not None
            and current_offset is not None
            and current_widget is not anchor_widget
            and current_widget in new_selections
        ):
            sel = new_selections[current_widget]
            snapped = self._snap_endpoint(
                current_widget, current_offset, direction=direction.opposite
            )
            new_selections[current_widget] = self._replace_endpoint(
                sel, snapped, direction=direction.opposite
            )
        self.selections = new_selections
        if self._last_drag_selection != new_selections:
            self._last_drag_selection = new_selections
            self._dragged = True

    @staticmethod
    def _replace_endpoint(
        selection: Selection, offset: Offset, *, direction: _DragDirection
    ) -> Selection:
        if direction is _DragDirection.FORWARD:
            return Selection(offset, selection.end)
        return Selection(selection.start, offset)

    def _drag_direction(self, select_state: SelectState) -> _DragDirection:
        try:
            start_screen = select_state.start.pointer_start_offset
        except (AttributeError, errors.NoWidget):
            # Container may have been detached mid-drag (e.g. a re-render
            # during streaming); degrade to forward rather than crash.
            return _DragDirection.FORWARD
        current_screen = select_state.screen_offset
        if (start_screen.y, start_screen.x) <= (current_screen.y, current_screen.x):
            return _DragDirection.FORWARD
        return _DragDirection.BACKWARD

    def _snap_endpoint(
        self, widget: Widget, offset: Offset, *, direction: _DragDirection
    ) -> Offset:
        granularity = self._drag_granularity
        if direction is _DragDirection.FORWARD:
            return self._boundary_start(widget, offset, granularity)
        return self._boundary_end(widget, offset, granularity)

    def _snap_drag(self, widget: Widget, anchor: Offset, current: Offset) -> Selection:
        if (anchor.y, anchor.x) <= (current.y, current.x):
            start, end = anchor, current
        else:
            start, end = current, anchor
        granularity = self._drag_granularity
        return Selection.from_offsets(
            self._boundary_start(widget, start, granularity),
            self._boundary_end(widget, end, granularity),
        )

    def _click_chain_threshold(self) -> float:
        try:
            return self.app.CLICK_CHAIN_TIME_THRESHOLD
        except NoActiveAppError:
            return _DEFAULT_CLICK_CHAIN_TIME_THRESHOLD

    @staticmethod
    def _boundary_around(
        widget: Widget, offset: Offset, granularity: SelectGranularity
    ) -> tuple[Offset, Offset] | None:
        before = widget.get_selection(Selection(None, offset))
        after = widget.get_selection(Selection(offset, None))
        if before is None or after is None:
            return None
        left_line = before[0].rsplit("\n", 1)[-1]
        right_line = after[0].split("\n", 1)[0]
        if granularity is SelectGranularity.WORD:
            left = _TRAILING_WORD.search(left_line)
            right = _WORD.match(right_line)
            left_len = len(left.group()) if left else 0
            right_len = len(right.group()) if right else 0
            if not left_len and not right_len:
                return None
        else:  # PARAGRAPH
            left_len, right_len = len(left_line), len(right_line)
        return (
            Offset(offset.x - left_len, offset.y),
            Offset(offset.x + right_len, offset.y),
        )

    @staticmethod
    def _selection_around(
        widget: Widget, offset: Offset, granularity: SelectGranularity
    ) -> Selection | None:
        boundary = WordSelectScreen._boundary_around(widget, offset, granularity)
        return None if boundary is None else Selection.from_offsets(*boundary)

    @staticmethod
    def _boundary_start(
        widget: Widget, offset: Offset, granularity: SelectGranularity
    ) -> Offset:
        boundary = WordSelectScreen._boundary_around(widget, offset, granularity)
        return offset if boundary is None else boundary[0]

    @staticmethod
    def _boundary_end(
        widget: Widget, offset: Offset, granularity: SelectGranularity
    ) -> Offset:
        boundary = WordSelectScreen._boundary_around(widget, offset, granularity)
        return offset if boundary is None else boundary[1]
