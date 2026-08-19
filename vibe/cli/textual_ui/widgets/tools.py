from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.widget import Widget
from textual.widgets import Static

from vibe.app_server.models import (
    CancelledEffectState,
    CompletedEffectState,
    EffectCallDisplay,
    EffectResultDisplay,
    EffectState,
    FailedEffectState,
    PublicEffectEntry,
    SkippedEffectState,
)
from vibe.cli.textual_ui.widgets.collapsible import (
    ClickWithoutDragMixin,
    CollapsibleSection,
    HeaderCollapsibleSection,
    OverflowCollapsibleSection,
    lines_label,
)
from vibe.cli.textual_ui.widgets.links import LinkStatic, linkify_urls_in_text
from vibe.cli.textual_ui.widgets.messages import ExpandingBorder
from vibe.cli.textual_ui.widgets.no_markup_static import (
    NoMarkupStatic,
    NonSelectableStatic,
)
from vibe.cli.textual_ui.widgets.status_message import IndicatorState, StatusMessage
from vibe.cli.textual_ui.widgets.tool_widgets import (
    ToolResultWidget,
    clean_output,
    effect_result_is_collapsible,
    get_result_widget,
    linkify_effect_result,
)


def _failed_header_display(
    entry: PublicEffectEntry, state: FailedEffectState
) -> EffectResultDisplay:
    call_display = entry.detail.display
    if call_display.settled_message is None:
        return state.display
    return EffectResultDisplay(
        success=False,
        verb=call_display.settled_verb,
        message=call_display.settled_message,
        suffix=call_display.suffix,
    )


class ToolGroup(Vertical):
    """Visual container grouping consecutive tool calls, results, and thinking.

    Purely a grouping wrapper: no header, no collapse. Its children are packed
    together with no vertical gaps; the group as a whole is spaced from the
    surrounding conversation.
    """

    def __init__(self) -> None:
        super().__init__(classes="tool-group")

    @property
    def content_container(self) -> ToolGroup:
        return self

    def sync_visibility(self) -> None:
        """Collapse the group when all its children are hidden.

        A group holding only a hidden thinking node would otherwise still
        reserve its top margin and leave a stray blank line.
        """
        self.display = any(child.display for child in self.children)


class ToolCallMessage(StatusMessage):
    def __init__(self, entry: PublicEffectEntry) -> None:
        self._entry = entry
        self._tool_name = entry.detail.tool_name
        self._stream_widget: NoMarkupStatic | None = None
        self._suffix_widget: NoMarkupStatic | None = None
        self._verb_widget: NoMarkupStatic | None = None
        self._header_row: Horizontal | None = None

        super().__init__()
        self.add_class("tool-call")

        if isinstance(entry.state, CompletedEffectState):
            self._is_spinning = False
            self._state = (
                IndicatorState.SUCCESS
                if entry.state.display.success
                else IndicatorState.ERROR
            )
        elif isinstance(entry.state, FailedEffectState):
            self._is_spinning = False
            self._state = IndicatorState.ERROR
        elif isinstance(entry.state, SkippedEffectState | CancelledEffectState):
            self._is_spinning = False
            self._state = IndicatorState.MUTED

    def compose(self) -> ComposeResult:
        with Vertical(classes="tool-call-container"):
            self._header_row = Horizontal(classes="tool-call-header")
            with self._header_row:
                self._indicator_widget = NonSelectableStatic(
                    self._spinner.current_frame(), classes="status-indicator-icon"
                )
                yield self._indicator_widget
                self._verb_widget = NoMarkupStatic(
                    "", classes="collapsible-header-verb"
                )
                self._verb_widget.display = False
                yield self._verb_widget
                self._text_widget = LinkStatic("", classes="status-indicator-text")
                yield self._text_widget
                self._suffix_widget = NoMarkupStatic(
                    "", classes="status-indicator-suffix"
                )
                self._suffix_widget.display = False
                yield self._suffix_widget
            self._stream_widget = NoMarkupStatic("", classes="tool-stream-message")
            self._stream_widget.display = False
            yield self._stream_widget

    def on_mount(self) -> None:
        super().on_mount()
        self.recompute_gap()

    def recompute_gap(self) -> None:
        # Outside a group (history / standalone), collapse the gap when the
        # previous visible widget is another tool widget. Inside a group the CSS
        # packs children directly, so there is nothing to toggle.
        # "no-gap" is a ToolResultMessage signal, intentionally not a CSS hook.
        self.set_class(self._follows_visible_tool_widget(), "no-gap")

    def _follows_visible_tool_widget(self) -> bool:
        if self.parent is None or self.parent.has_class("tool-group"):
            return False
        siblings = list(self.parent.children)
        idx = siblings.index(self) if self in siblings else -1
        if idx <= 0:
            return False
        prev_idx = idx - 1
        while prev_idx > 0 and not siblings[prev_idx].display:
            prev_idx -= 1
        prev = siblings[prev_idx]
        return prev.display and isinstance(prev, (ToolCallMessage, ToolResultMessage))

    @property
    def tool_call_id(self) -> str:
        return self._entry.id

    def get_content(self) -> str:
        return self._header_parts()[1]

    def get_content_suffix(self) -> str:
        return self._header_parts()[2]

    def _header_parts(self) -> tuple[str, str, str]:
        if display := self._settled_display():
            return display.verb, display.message, display.suffix
        display = self._call_display()
        message = display.message if display.message is not None else display.summary
        return display.verb, message, display.suffix

    def _settled_display(self) -> EffectResultDisplay | None:
        state = self._entry.state
        if isinstance(state, FailedEffectState):
            return _failed_header_display(self._entry, state)
        if isinstance(state, CompletedEffectState | SkippedEffectState):
            return state.display
        if isinstance(state, CancelledEffectState):
            return state.display
        return None

    def _call_display(self) -> EffectCallDisplay:
        return self._entry.detail.display

    def update_entry(self, entry: PublicEffectEntry) -> None:
        self._entry = entry
        self._tool_name = entry.detail.tool_name
        verb, message, suffix = self._header_parts()
        self._set_text(message, suffix, verb=verb)

    def set_stream_message(self, message: str) -> None:
        """Update the stream message displayed below the tool call indicator."""
        if self._stream_widget:
            self._stream_widget.update(f"→ {message}")
            self._stream_widget.display = True

    def settle(self, state: IndicatorState) -> None:
        super().settle(state)
        if self._stream_widget is None:
            return
        self._stream_widget.update("")
        self._stream_widget.display = False

    def set_result_text(
        self, text: str, suffix: str = "", *, verb: str = "", linkify: bool = False
    ) -> None:
        self._set_text(text, suffix, verb=verb, linkify=linkify)

    def _set_text(
        self, text: str, suffix: str, *, verb: str = "", linkify: bool = False
    ) -> None:
        if self._verb_widget:
            self._verb_widget.update(verb)
            self._verb_widget.display = bool(verb)
        if self._text_widget:
            content = linkify_urls_in_text(text) if linkify else text
            self._text_widget.update(content)
        self._update_suffix(suffix)

    def _update_suffix(self, suffix: str) -> None:
        if self._suffix_widget:
            self._suffix_widget.update(suffix)
            self._suffix_widget.display = bool(suffix)
        if self._header_row is not None:
            # With a suffix present the title drops to auto width so the suffix
            # sits right after it; otherwise the title takes 1fr and wraps.
            self._header_row.set_class(bool(suffix), "has-suffix")

    def update_display(self) -> None:
        super().update_display()
        verb, _, suffix = self._header_parts()
        if self._header_row is not None:
            self._header_row.set_class(self._is_spinning, "running")
            self._header_row.set_class(
                effect_result_is_collapsible(self._entry.detail), "collapsible-result"
            )
        if self._verb_widget:
            self._verb_widget.update(verb)
            self._verb_widget.display = bool(verb)
        self._update_suffix(suffix)

    def show_muted(self) -> None:
        # Neutral grey square with the call summary -- used for an error whose
        # verdict is still unknown, a user-declined call, and a cancelled call.
        self.settle(IndicatorState.MUTED)

    def escalate_error(self) -> None:
        # No recovery followed: promote the held square to a hard red cross.
        self.settle(IndicatorState.ERROR)


class ToolResultMessage(ClickWithoutDragMixin, Static):
    def __init__(
        self, entry: PublicEffectEntry, call_widget: ToolCallMessage | None = None
    ) -> None:
        self._entry = entry
        self._call_widget = call_widget
        self._tool_name = entry.detail.tool_name
        self._content_container: Vertical | None = None
        self._result_widget: ToolResultWidget | None = None
        self._is_collapsible = self._determine_collapsible()
        self._is_error = False
        # The collapsed error/skip section whose triangle carries the muted
        # state (and gets recoloured red on escalation).
        self._muted_section: HeaderCollapsibleSection | None = None

        super().__init__()
        self.add_class("tool-result")

    @property
    def tool_name(self) -> str:
        return self._tool_name

    def _determine_collapsible(self) -> bool:
        return effect_result_is_collapsible(self._entry.detail)

    def compose(self) -> ComposeResult:
        if self._is_collapsible:
            # Collapsible results mount their section directly onto this widget
            # (see `_render_result_collapsible`); no intermediate container.
            return
        with Horizontal(classes="tool-result-container"):
            self._border = ExpandingBorder(classes="tool-result-border")
            yield self._border
            self._content_container = Vertical(classes="tool-result-content")
            yield self._content_container

    async def on_mount(self) -> None:
        if self._call_widget:
            if isinstance(self._state, FailedEffectState):
                # Start muted; the verdict (recoverable vs terminal) lands later.
                self._call_widget.show_muted()
                verb, message, suffix = self._get_result_parts()
                self._call_widget.set_result_text(message, suffix, verb=verb)
            elif isinstance(self._state, SkippedEffectState | CancelledEffectState):
                # A declined/denied call is the user's choice, not a failure.
                self._call_widget.show_muted()
                verb, message, suffix = self._get_result_parts()
                self._call_widget.set_result_text(message, suffix, verb=verb)
            else:
                success = self._determine_success()
                self._call_widget.stop_spinning(success=success)
                # Collapsible results fold into a header and hide the call
                # widget, so its inline text is only set for expanded results.
                if not self._is_collapsible:
                    verb, message, suffix = self._get_result_parts()
                    linkify = linkify_effect_result(self._entry.detail)
                    self._call_widget.set_result_text(
                        message, suffix, verb=verb, linkify=linkify
                    )
        self.recompute_gap()
        await self._render_result()

    def recompute_gap(self) -> None:
        self.set_class(self._needs_standalone_gap(), "has-gap")

    def _needs_standalone_gap(self) -> bool:
        # Grouped results are packed with no gaps (CSS); a standalone collapsible
        # result keeps a gap unless its call widget was itself gap-collapsed.
        if self.parent is not None and self.parent.has_class("tool-group"):
            return False
        if self._call_widget is None or not self._is_collapsible:
            return False
        return "no-gap" not in self._call_widget.classes

    def _muted_header_text(self) -> str:
        # The call widget reflects the settled display when one is available and
        # otherwise falls back to the summary of what was attempted.
        if self._call_widget is not None:
            summary = self._call_widget.get_content()
            if summary:
                return summary
        return self._get_result_text()

    @staticmethod
    def _bordered(body: Widget) -> Horizontal:
        """Wrap a result body in the expanding-border container used by all
        collapsible result bodies.
        """
        return Horizontal(
            ExpandingBorder(classes="tool-result-border"),
            Vertical(body, classes="tool-result-content"),
            classes="tool-result-container",
        )

    def escalate_error(self) -> None:
        # Turn ended without a follow-up tool call: promote the held muted state
        # to a hard error. For collapsible results the arrow turns red; for
        # expanded (call-widget) results the call icon turns into a red cross.
        if not self._is_error:
            return
        if self._muted_section is not None:
            self._muted_section.mark_error()
        elif self._call_widget is not None:
            self._call_widget.escalate_error()
            verb, message, suffix = self._get_result_parts()
            self._call_widget.set_result_text(message, suffix, verb=verb)

    def _determine_success(self) -> bool:
        display = self._result_display()
        return display.success if display is not None else False

    def _get_result_parts(self) -> tuple[str, str, str]:
        if isinstance(self._state, FailedEffectState):
            display = _failed_header_display(self._entry, self._state)
            return display.verb, display.message, display.suffix
        if isinstance(self._state, SkippedEffectState | CancelledEffectState):
            return "", f"{self._tool_name}: skipped", ""
        if display := self._result_display():
            return display.verb, display.message, display.suffix
        return "", f"{self._tool_name} completed", ""

    def _get_result_text(self) -> str:
        verb, message, _ = self._get_result_parts()
        return f"{verb} {message}".strip() if verb else message

    async def _render_result(self) -> None:
        if self._is_collapsible:
            await self._render_result_collapsible()
        else:
            await self._render_result_expanded()

    async def _render_result_collapsible(self) -> None:
        # Bodies are built lazily (factory closures): a collapsed result keeps
        # only its header, and the heavy content widget (Markdown/Syntax/diff) is
        # created on first expand and torn down on collapse. See
        # `CollapsibleSection`.
        await self.remove_children()

        if isinstance(self._state, FailedEffectState):
            self._is_error = True
            # Fold the whole result into a single muted-arrow header; the error
            # detail is the collapsed, bordered body. No separate square icon or
            # "N lines" row. Escalation recolours red.
            error = clean_output(self._state.error.message)
            verb, message, suffix = self._get_result_parts()

            def build_error_body() -> Widget:
                return self._bordered(
                    Static(Content.from_markup("[$error]Error[/]: ") + Content(error))
                )

            self._muted_section = HeaderCollapsibleSection(
                build_error_body,
                header_text=message,
                header_verb=verb,
                header_suffix=suffix,
                header_muted=True,
            )
            await self.mount(self._muted_section)
            if self._call_widget:
                self._call_widget.display = False
            self.display = True
            return

        if isinstance(self._state, SkippedEffectState | CancelledEffectState):
            reason = self._state.reason
            self._muted_section = HeaderCollapsibleSection(
                lambda: self._bordered(NoMarkupStatic(f"Skipped: {reason}")),
                header_text=self._muted_header_text(),
                header_muted=True,
            )
            await self.mount(self._muted_section)
            if self._call_widget:
                self._call_widget.display = False
            self.display = True
            return

        self.remove_class("error-text")
        self.remove_class("warning-text")

        display = self._result_display()
        if display is None:
            self.display = False
            return

        def build_result_body() -> Widget:
            widget = get_result_widget(
                self._entry.detail,
                self._state.output
                if isinstance(self._state, CompletedEffectState)
                else None,
                success=display.success,
                message=display.message,
                warnings=display.warnings,
            )
            self._result_widget = widget
            return Horizontal(
                ExpandingBorder(classes="tool-result-border"),
                Vertical(widget, classes="tool-result-content"),
                classes="tool-result-container",
            )

        section = HeaderCollapsibleSection(
            build_result_body,
            header_text=display.message,
            header_verb=display.verb,
            header_suffix=display.suffix,
            header_success=display.success,
        )
        await self.mount(section)
        if self._call_widget:
            self._call_widget.display = False
        self.display = True

    async def _render_result_expanded(self) -> None:
        if self._content_container is None:
            return

        await self._content_container.remove_children()

        if isinstance(self._state, FailedEffectState):
            self._is_error = True
            # Only the inline "Error" span is ever colored; escalation changes the
            # call icon to a red cross but leaves this folded body untouched.
            message = clean_output(self._state.error.message)
            line_count = len(message.strip("\n").split("\n"))
            await self._content_container.mount(
                OverflowCollapsibleSection(
                    lambda: Static(
                        Content.from_markup("[$error]Error[/]: ") + Content(message)
                    ),
                    collapsed_label=lines_label(line_count),
                )
            )
            self.display = True
            return

        if isinstance(self._state, SkippedEffectState | CancelledEffectState):
            self.add_class("warning-text")
            reason = self._state.reason
            await self._content_container.mount(NoMarkupStatic(f"Skipped: {reason}"))
            self.display = True
            return

        self.remove_class("error-text")
        self.remove_class("warning-text")

        display = self._result_display()
        if display is None:
            self.display = False
            return

        widget = get_result_widget(
            self._entry.detail,
            self._state.output
            if isinstance(self._state, CompletedEffectState)
            else None,
            success=display.success,
            message=display.message,
            warnings=display.warnings,
        )
        await self._content_container.mount(widget)
        self._result_widget = widget
        self._apply_border_colors()
        self.display = bool(widget.children)

    @property
    def _state(self) -> EffectState:
        return self._entry.state

    def _result_display(self) -> EffectResultDisplay | None:
        match self._state:
            case (
                CompletedEffectState()
                | FailedEffectState()
                | SkippedEffectState() as state
            ):
                return state.display
            case CancelledEffectState() as state:
                return state.display
            case _:
                return None

    def _apply_border_colors(self) -> None:
        if self._result_widget is None:
            return
        self._border.set_row_colors(self._result_widget.border_row_colors)

    def on_tool_result_widget_border_colors_changed(
        self, message: ToolResultWidget.BorderColorsChanged
    ) -> None:
        if message.control is self._result_widget:
            self._apply_border_colors()

    async def on_click(self, event: events.Click) -> None:
        if self._click_is_passive(event):
            return
        sections = list(self.query(CollapsibleSection))
        if sections:
            sections[0].toggle()
