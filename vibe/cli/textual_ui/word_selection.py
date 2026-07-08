from __future__ import annotations

import re

from textual import events
from textual.geometry import Offset
from textual.screen import Screen
from textual.selection import Selection
from textual.widget import Widget

_WORD = re.compile(r"\w+")
_TRAILING_WORD = re.compile(r"\w+$")
_DOUBLE_CLICK = 2
_TRIPLE_CLICK = 3


class WordSelectScreen(Screen[None]):
    """Default screen that scopes multi-click text selection.

    Textual's built-in behavior selects the whole widget on a double-click and
    the surrounding container on a triple-click. Here a double-click selects
    only the word under the cursor, and a triple-click selects just the widget
    (the paragraph) rather than the wider container.
    """

    async def on_click(self, event: events.Click) -> None:
        if event.chain not in {_DOUBLE_CLICK, _TRIPLE_CLICK}:
            return
        if not (self.allow_select and self.app.ALLOW_SELECT):
            return
        widget, offset = self.get_widget_and_offset_at(*event.screen_offset)
        if widget is None or offset is None or not widget.allow_select:
            return
        if event.chain == _TRIPLE_CLICK:
            widget.text_select_all()
            return
        if (selection := self._word_selection_at(widget, offset)) is not None:
            self.selections = {widget: selection}

    @staticmethod
    def _word_selection_at(widget: Widget, offset: Offset) -> Selection | None:
        before = widget.get_selection(Selection(None, offset))
        after = widget.get_selection(Selection(offset, None))
        if before is None or after is None:
            return None
        left_line = before[0].rsplit("\n", 1)[-1]
        right_line = after[0].split("\n", 1)[0]
        left = _TRAILING_WORD.search(left_line)
        right = _WORD.match(right_line)
        left_len = len(left.group()) if left else 0
        right_len = len(right.group()) if right else 0
        if not left_len and not right_len:
            return None
        return Selection.from_offsets(
            Offset(offset.x - left_len, offset.y),
            Offset(offset.x + right_len, offset.y),
        )
