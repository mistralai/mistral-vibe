from __future__ import annotations

from enum import StrEnum, auto
from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import Static

from vibe.cli.commands import ALT_KEY
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic


class _RewindAction(StrEnum):
    EDIT_AND_RESTORE = auto()
    EDIT_ONLY = auto()


class RewindApp(Container):
    """Bottom panel widget for rewind mode actions."""

    can_focus = True
    can_focus_children = False

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("1", "select_1", "Option 1", show=False),
        Binding("2", "select_2", "Option 2", show=False),
    ]

    class RewindWithRestore(Message):
        """User chose to edit the message and restore files."""

    class RewindWithoutRestore(Message):
        """User chose to edit the message without restoring files."""

    def __init__(self, message_preview: str, *, has_file_changes: bool) -> None:
        super().__init__(id="rewind-app")
        self._message_preview = message_preview
        self._has_file_changes = has_file_changes
        self.selected_option = 0
        self.option_widgets: list[Static] = []
        self._title_widget: NoMarkupStatic | None = None
        self._options = self._build_options()

    def _build_options(self) -> list[tuple[str, _RewindAction]]:
        options: list[tuple[str, _RewindAction]] = []
        if self._has_file_changes:
            options.append((
                "Edit & restore files to this point",
                _RewindAction.EDIT_AND_RESTORE,
            ))
        edit_only_label = (
            "Edit without restoring files"
            if self._has_file_changes
            else "Edit message from here"
        )
        options.append((edit_only_label, _RewindAction.EDIT_ONLY))
        return options

    @property
    def has_file_changes(self) -> bool:
        return self._has_file_changes

    def update_preview(self, message_preview: str) -> None:
        self._message_preview = message_preview
        if self._title_widget is not None:
            self._title_widget.update(f"Rewind to: {message_preview[:80]}")

    def compose(self) -> ComposeResult:
        with Vertical(id="rewind-content"):
            self._title_widget = NoMarkupStatic(
                f"Rewind to: {self._message_preview[:80]}", classes="rewind-title"
            )
            yield self._title_widget
            yield NoMarkupStatic("")
            for _ in range(len(self._options)):
                widget = NoMarkupStatic("", classes="rewind-option")
                self.option_widgets.append(widget)
                yield widget
            yield NoMarkupStatic("")
            yield NoMarkupStatic(
                f"{ALT_KEY}+↑↓ or Ctrl+P/N browse messages  ↑↓ pick option  Enter confirm  ESC cancel",
                classes="rewind-help",
            )

    async def on_mount(self) -> None:
        self._update_options()
        self.focus()

    def _update_options(self) -> None:
        for idx, ((text, _action), widget) in enumerate(
            zip(self._options, self.option_widgets, strict=True)
        ):
            is_selected = idx == self.selected_option
            cursor = "› " if is_selected else "  "
            option_text = f"{cursor}{idx + 1}. {text}"

            widget.update(option_text)

            widget.remove_class("rewind-cursor-selected")
            widget.remove_class("rewind-option-unselected")

            if is_selected:
                widget.add_class("rewind-cursor-selected")
            else:
                widget.add_class("rewind-option-unselected")

    def _option_count(self) -> int:
        return len(self._options)

    def action_move_up(self) -> None:
        self.selected_option = (self.selected_option - 1) % self._option_count()
        self._update_options()

    def action_move_down(self) -> None:
        self.selected_option = (self.selected_option + 1) % self._option_count()
        self._update_options()

    def action_select(self) -> None:
        self._handle_selection(self.selected_option)

    def action_select_1(self) -> None:
        if self._option_count() >= 1:
            self.selected_option = 0
            self._handle_selection(0)

    def action_select_2(self) -> None:
        if self._option_count() >= 2:  # noqa: PLR2004
            self.selected_option = 1
            self._handle_selection(1)

    def _handle_selection(self, option: int) -> None:
        _, action = self._options[option]
        match action:
            case _RewindAction.EDIT_AND_RESTORE:
                self.post_message(self.RewindWithRestore())
            case _RewindAction.EDIT_ONLY:
                self.post_message(self.RewindWithoutRestore())

    def on_blur(self, event: events.Blur) -> None:
        self.call_after_refresh(self.focus)
