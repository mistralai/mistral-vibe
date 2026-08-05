from __future__ import annotations

from collections.abc import Callable
import re
from typing import TYPE_CHECKING, cast

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget

if TYPE_CHECKING:
    from vibe.cli.textual_ui.app import ChatScroll

from vibe.cli.textual_ui.widgets.no_markup_static import (
    NoMarkupStatic,
    NonSelectableStatic,
)

# Control chars (incl. ESC) that must never reach the terminal via a header.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Same, but keep tab (\x09) and newline (\x0a) for the expanded multi-line view.
_CONTROL_CHARS_KEEP_NEWLINES = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _single_line(text: str) -> str:
    """Collapse header text to a single line and strip control/escape bytes.

    A header row is single-line by construction, so a multi-line value (e.g. a
    heredoc command) would otherwise wrap and break the layout -- hence the
    whitespace flattening. Control/escape bytes are dropped because Textual
    renders a stray ESC straight to the terminal (it strips BEL but not ESC),
    which would corrupt the display.
    """
    return _CONTROL_CHARS.sub("", " ".join(text.split()))


def _multi_line(text: str) -> str:
    """Strip control/escape bytes but preserve newlines (and tabs).

    Used for the expanded header, where a multi-line value (e.g. a heredoc
    command) should render across lines instead of being flattened. CR/CRLF are
    normalised to LF so a stray carriage return can't redraw over the line.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL_CHARS_KEEP_NEWLINES.sub("", text)


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

    def clear_selection_state_for_click(self) -> None:
        self._had_selection_at_press = False


class CollapsibleSection(ClickWithoutDragMixin, Vertical):
    """Shared fold/click machinery for a disclosable body.

    Concrete layouts (`HeaderCollapsibleSection`, `OverflowCollapsibleSection`)
    build their own toggle row, decide where it sits relative to the body, and
    react to a toggle via `_on_toggled`.

    The body may be passed either as a ready `Widget` (eager -- kept mounted and
    just shown/hidden) or as a factory `Callable[[], Widget]` (lazy -- built on
    first expand and torn down on collapse). Lazy is the default for the many
    collapsed tool results that would otherwise keep heavy, never-seen content
    widgets (Markdown/Syntax/diffs) alive in the DOM.
    """

    _toggle_row: Horizontal
    # Whether a lazily-built body mounts before the toggle row (overflow layout)
    # or after it (header layout). Concrete subclasses set this.
    _body_before_toggle: bool = False

    class Toggled(Message):
        def __init__(self, section: CollapsibleSection, is_collapsed: bool) -> None:
            super().__init__()
            self.section = section
            self.is_collapsed = is_collapsed

    def __init__(self, body: Widget | Callable[[], Widget]) -> None:
        super().__init__()
        self.add_class("collapsible-section")
        if callable(body):
            self._body_factory: Callable[[], Widget] | None = body
            self._body: Widget | None = None
        else:
            self._body_factory = None
            self._body = body
            body.display = False
        self._is_collapsed = True
        self._triangle = NonSelectableStatic("⏵", classes="collapsible-triangle")

    @property
    def is_collapsed(self) -> bool:
        return self._is_collapsed

    def _show_body(self) -> None:
        if self._body is None and self._body_factory is not None:
            self._body = self._body_factory()
            self._body.display = True
            if self._body_before_toggle:
                self.mount(self._body, before=self._toggle_row)
            else:
                self.mount(self._body)
        elif self._body is not None:
            self._body.display = True

    def _hide_body(self) -> None:
        if self._body is None:
            return
        if self._body_factory is not None:
            # Lazy: drop the subtree entirely so a collapsed section costs nothing
            # beyond its header. It is rebuilt from the factory on the next expand.
            self._body.remove()
            self._body = None
        else:
            self._body.display = False

    def toggle(self) -> None:
        if self._is_collapsed:
            chat = next(
                (ancestor for ancestor in self.ancestors if ancestor.id == "chat"), None
            )
            if chat is not None:
                cast("ChatScroll", chat).preserve_scroll_position()

        self._is_collapsed = not self._is_collapsed
        if self._is_collapsed:
            self._hide_body()
        else:
            self._show_body()
        self._triangle.update("⏵" if self._is_collapsed else "⏷")
        self._on_toggled(self._is_collapsed)
        self.post_message(self.Toggled(self, self._is_collapsed))
        # Toggling reflows the row and resizes the body. Repaint after the reflow
        # settles, otherwise cells vacated by it ghost until the next scroll.
        self.app.call_after_refresh(self.screen.refresh)

    def set_collapsed(self, collapsed: bool) -> None:
        if self._is_collapsed != collapsed:
            self.toggle()

    def _on_toggled(self, collapsed: bool) -> None:
        raise NotImplementedError

    def _is_click_on_toggle(self, event: events.Click) -> bool:
        return self._is_click_within(event, self._toggle_row)

    async def on_click(self, event: events.Click) -> None:
        if self._click_is_passive(event):
            return
        event.stop()
        self.toggle()


class HeaderCollapsibleSection(CollapsibleSection):
    """A clickable summary header on top, with the detail body folded beneath.

    The header carries an optional bold verb and a single-line summary that
    wraps when expanded. The triangle colour reflects the outcome
    (success/error), or stays muted while a verdict is pending -- see
    `mark_error` for the escalation to a hard error.
    """

    _body_before_toggle = False

    def __init__(
        self,
        body: Widget | Callable[[], Widget],
        *,
        header_text: str,
        header_verb: str = "",
        header_suffix: str = "",
        header_success: bool = True,
        header_muted: bool = False,
    ) -> None:
        super().__init__(body)
        # Collapsed shows a flattened one-liner; expanded restores newlines so a
        # multi-line command (heredoc, for-loop) reads as it was written.
        self._collapsed_text = _single_line(header_text)
        self._expanded_text = _multi_line(header_text)
        self._header_text = self._collapsed_text
        if not header_muted:
            self._triangle.add_class("success" if header_success else "error")

        verb_widget = (
            NoMarkupStatic(header_verb, classes="collapsible-header-verb")
            if header_verb
            else None
        )
        self._text_widget = NoMarkupStatic(
            self._header_text, classes="status-indicator-text"
        )
        children: list[Widget] = [self._triangle]
        if verb_widget:
            children.append(verb_widget)
        children.append(self._text_widget)
        if header_suffix:
            children.append(
                NoMarkupStatic(header_suffix, classes="status-indicator-suffix")
            )
        toggle_classes = "collapsible-toggle header"
        if header_suffix:
            toggle_classes += " has-suffix"
        self._toggle_row = Horizontal(*children, classes=toggle_classes)

    def compose(self) -> ComposeResult:
        yield self._toggle_row
        # Lazy bodies (factory) are mounted on first expand; eager bodies compose
        # here (hidden) so live callers can update them while collapsed.
        if self._body is not None:
            yield self._body

    def _on_toggled(self, collapsed: bool) -> None:
        # Swap between the flattened one-liner and the newline-preserving text
        # (wrapped when expanded, via the `expanded` class).
        self._text_widget.update(
            self._collapsed_text if collapsed else self._expanded_text
        )
        self._toggle_row.set_class(not collapsed, "expanded")

    def mark_error(self) -> None:
        """Recolour a muted header's triangle to the hard-error colour."""
        self._triangle.remove_class("success")
        self._triangle.add_class("error")


class OverflowCollapsibleSection(CollapsibleSection):
    """Content with a small disclosure toggle ("+N lines" / "show less") beneath.

    Used where a preview or body is always visible and only the overflow folds
    away. The collapsed label can be updated live (e.g. as command output
    streams) via `set_collapsed_label`.
    """

    _body_before_toggle = True

    def __init__(
        self,
        body: Widget | Callable[[], Widget],
        *,
        collapsed_label: str,
        expanded_label: str = "show less",
    ) -> None:
        super().__init__(body)
        self._collapsed_label = collapsed_label
        self._expanded_label = expanded_label
        self._label = NoMarkupStatic(
            collapsed_label, classes="collapsible-toggle-label"
        )
        self._toggle_row = Horizontal(
            self._triangle, self._label, classes="collapsible-toggle"
        )

    def compose(self) -> ComposeResult:
        # Lazy bodies (factory) are mounted before the toggle row on first expand;
        # eager bodies compose here (hidden) for live callers (e.g. bash output).
        if self._body is not None:
            yield self._body
        yield self._toggle_row

    def _on_toggled(self, collapsed: bool) -> None:
        self._label.update(self._collapsed_label if collapsed else self._expanded_label)
        if collapsed:
            self._toggle_row.scroll_visible()

    def set_collapsed_label(self, label: str) -> None:
        self._collapsed_label = label
        if self._is_collapsed:
            self._label.update(label)
