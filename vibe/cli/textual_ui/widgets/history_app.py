from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, TypedDict

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import Static

from vibe.cli.textual_ui.terminal_theme import TERMINAL_THEME_NAME
from vibe.cli.textual_ui.widgets.config_app import _ALL_THEMES

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig


class HistoryDefinition(TypedDict):
    session_id: str
    session_title: str


class HistoryApp(Container):
    history: list[HistoryDefinition] = []
    themes: list[str] = []
    can_focus = True
    can_focus_children = False

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("space", "toggle_setting", "Toggle", show=False),
        Binding("enter", "cycle", "Next", show=False),
        Binding("escape", "close", "Esc", show=False),
    ]

    class HistoryClosed(Message):
        def __init__(self, changes: dict[str, str]) -> None:
            super().__init__()
            self.changes = changes

    class SessionSelected(Message):
        def __init__(self, session: HistoryDefinition) -> None:
            super().__init__()
            self.session = session

    def __init__(
        self,
        config: VibeConfig,
        history: list[HistoryDefinition] | None = None,
        *,
        has_terminal_theme: bool = False,
    ) -> None:
        super().__init__(id="history-app")
        self.config = config
        self.selected_index = 0
        self.changes: dict[str, str] = {}
        self.history = history or []

        themes = (
            _ALL_THEMES
            if has_terminal_theme
            else [t for t in _ALL_THEMES if t != TERMINAL_THEME_NAME]
        )

        self.title_widget: Static | None = None
        self.history_widgets: list[Static] = []
        self.help_widget: Static | None = None

    def on_mount(self) -> None:
        self._update_display()
        self.focus()

    def _update_display(self) -> None:
        for i, (history, widget) in enumerate(
            zip(self.history, self.history_widgets, strict=True)
        ):
            is_selected = i == self.selected_index
            cursor = "› " if is_selected else "  "

            title: str = history["session_title"]
            session_id: str = history["session_id"]
            text = f"{cursor}{session_id} : {title}"

            widget.update(text)

            widget.remove_class("settings-cursor-selected")
            widget.remove_class("settings-value-cycle-selected")
            widget.remove_class("settings-value-cycle-unselected")

            if is_selected:
                widget.add_class("settings-value-cycle-selected")
            else:
                widget.add_class("settings-value-cycle-unselected")

    def action_move_up(self) -> None:
        self.selected_index = (self.selected_index - 1) % len(self.history)
        self._update_display()

    def action_move_down(self) -> None:
        self.selected_index = (self.selected_index + 1) % len(self.history)
        self._update_display()

    def _emit_selection(self) -> None:
        if not self.history:
            return

        try:
            session = self.history[self.selected_index]
            self.post_message(self.SessionSelected(session))
        except Exception:
            pass

    def action_toggle_setting(self) -> None:
        self._emit_selection()

    def action_cycle(self) -> None:
        self._emit_selection()

    def action_close(self) -> None:
        self.post_message(self.HistoryClosed(changes=self.changes.copy()))

    def on_blur(self, event: events.Blur) -> None:
        self.call_after_refresh(self.focus)

    def compose(self) -> ComposeResult:
        with Vertical(id="config-content"):
            self.title_widget = Static("Sessions", classes="settings-title")
            yield self.title_widget

            yield Static("")

            for _ in self.history:
                widget = Static("", classes="settings-option")
                self.history_widgets.append(widget)
                yield widget

            yield Static("")

            self.help_widget = Static(
                "↑↓ navigate  Enter/Open  ESC exit", classes="settings-help"
            )
            yield self.help_widget
