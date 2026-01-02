from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, TypedDict

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
    id: str
    name: str


# HistoryApp needs the same focus behavior and bindings as the config UI


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
    ]

    class HistoryClosed(Message):
        def __init__(self, changes: dict[str, str]) -> None:
            super().__init__()
            self.changes = changes

    def __init__(self, config: VibeConfig, *, has_terminal_theme: bool = False) -> None:
        super().__init__(id="history-app")
        self.config = config
        self.selected_index = 0
        self.changes: dict[str, str] = {}

        themes = (
            _ALL_THEMES
            if has_terminal_theme
            else [t for t in _ALL_THEMES if t != TERMINAL_THEME_NAME]
        )

        self.history: list[HistoryDefinition] = [
            {
                "id": "2a23d8be",
                "name": "Enhancing Code Quality with Python 3.12+ Best Practices",
            },
            {
                "id": "4033b89d",
                "name": "Refactoring a Python Codebase for Modern Best Practices",
            },
            {
                "id": "9c12a5be",
                "name": "Refactoring a Python Codebase for Modern Best Practices",
            },
            {"id": "7ebb2e34", "name": "Title Generation for Conversations"},
            {
                "id": "d8e75be7",
                "name": "Enhancing a Codebase with Modern Python Best Practices",
            },
            {"id": "36abdbdd", "name": "Codebase Analysis and Task Management"},
            {"id": "d22b4daf", "name": "Title Generation for Conversations"},
            {"id": "7520556e", "name": "Title Generation for Conversations"},
            {"id": "c7b29265", "name": "Understanding JavaScript Basics"},
        ]

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

            value: str = history["name"]
            label: str = history["id"]
            text = f"{cursor}{label} : {value}"

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
                "↑↓ navigate  Space/Enter toggle  ESC exit", classes="settings-help"
            )
            yield self.help_widget
