from __future__ import annotations

from typing import cast

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget

from vibe.cli.textual_ui.widgets.no_markup_static import (
    NoMarkupStatic,
    NonSelectableStatic,
)


def lines_label(count: int, *, prefix: str = "") -> str:
    word = "line" if count == 1 else "lines"
    return f"{prefix}{count} {word}"


class ClickWithoutDragMixin:
    _click_press_pos: tuple[int, int] | None = None
    _had_selection_at_press: bool = False

    def on_mouse_down(self, event: events.MouseDown) -> None:
        self._click_press_pos = (event.screen_x, event.screen_y)
        self._had_selection_at_press = bool(cast(Widget, self).screen.selections)

    def _is_click_within(self, event: events.Click, container: Widget | None) -> bool:
        widget = event.widget
        return (
            container is not None
            and widget is not None
            and container in widget.ancestors_with_self
        )

    def _is_click_on_toggle(self, event: events.Click) -> bool:
        return False

    def _click_is_passive(self, event: events.Click) -> bool:
        press = self._click_press_pos
        self._click_press_pos = None
        had_selection = self._had_selection_at_press
        self._had_selection_at_press = False
        if had_selection and not self._is_click_on_toggle(event):
            return True
        return press is not None and press != (event.screen_x, event.screen_y)


class CollapsibleSection(ClickWithoutDragMixin, Vertical):
    class Toggled(Message):
        def __init__(self, section: CollapsibleSection, is_collapsed: bool) -> None:
            super().__init__()
            self.section = section
            self.is_collapsed = is_collapsed

    def __init__(
        self,
        overflow_widget: Widget,
        collapsed_label: str,
        *,
        expanded_label: str = "show less",
    ) -> None:
        super().__init__()
        self.add_class("collapsible-section")
        self._overflow_widget = overflow_widget
        self._overflow_widget.display = False
        self._collapsed_label = collapsed_label
        self._expanded_label = expanded_label
        self._is_collapsed = True
        self._triangle = NonSelectableStatic("▶", classes="collapsible-triangle")
        self._label = NoMarkupStatic(
            collapsed_label, classes="collapsible-toggle-label"
        )
        self._toggle_row = Horizontal(
            self._triangle, self._label, classes="collapsible-toggle"
        )

    def compose(self) -> ComposeResult:
        yield self._overflow_widget
        yield self._toggle_row

    @property
    def is_collapsed(self) -> bool:
        return self._is_collapsed

    def set_collapsed_label(self, label: str) -> None:
        self._collapsed_label = label
        if self._is_collapsed:
            self._label.update(label)

    def toggle(self) -> None:
        self._is_collapsed = not self._is_collapsed
        self._overflow_widget.display = not self._is_collapsed
        self._triangle.update("▼" if not self._is_collapsed else "▶")
        self._label.update(
            self._collapsed_label if self._is_collapsed else self._expanded_label
        )
        if self._is_collapsed:
            self._toggle_row.scroll_visible()
        self.post_message(self.Toggled(self, self._is_collapsed))

    def set_collapsed(self, collapsed: bool) -> None:
        if self._is_collapsed != collapsed:
            self.toggle()

    def _is_click_on_toggle(self, event: events.Click) -> bool:
        return self._is_click_within(event, self._toggle_row)

    async def on_click(self, event: events.Click) -> None:
        if self._click_is_passive(event):
            return
        event.stop()
        self.toggle()
