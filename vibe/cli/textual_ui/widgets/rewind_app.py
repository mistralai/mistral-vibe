from __future__ import annotations

from enum import StrEnum, auto
from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.content import Content
from textual.message import Message
from textual.widgets import Static

from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.vim_navigation import VimNavigationMixin


class _RewindStep(StrEnum):
    ACTION = auto()
    PERSISTENCE = auto()


class _RewindAction(StrEnum):
    EDIT_AND_RESTORE = auto()
    EDIT_ONLY = auto()


class _RewindPersistence(StrEnum):
    IN_PLACE = auto()
    FORK = auto()


type _RewindChoice = _RewindAction | _RewindPersistence

_MAX_OPTIONS = 2


class RewindApp(VimNavigationMixin, Container):
    """Bottom panel widget for rewind mode actions.

    Two-step flow: first pick what to edit (restore files or not), then pick
    how to persist the rewind (keep the current session in place, or fork to a
    fresh session preserving the original as a parent).
    """

    can_focus = True
    can_focus_children = False

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("left", "edit_prev", "Edit previous", show=False),
        Binding("right", "edit_next", "Edit next", show=False),
        Binding("q", "quit_rewind", "Quit", show=False),
        Binding("enter", "select", "Select", show=False),
        Binding("1", "select_1", "Option 1", show=False),
        Binding("2", "select_2", "Option 2", show=False),
    ]

    class RewindConfirmed(Message):
        """User confirmed a rewind with a restore and persistence choice."""

        def __init__(self, *, restore_files: bool, inplace: bool) -> None:
            super().__init__()
            self.restore_files = restore_files
            self.inplace = inplace

    class EditPrev(Message):
        """User navigated to the previous editable message."""

    class EditNext(Message):
        """User navigated to the next editable message."""

    class Quit(Message):
        """User exited rewind mode."""

    def __init__(self, message_preview: str, *, has_file_changes: bool) -> None:
        super().__init__(id="rewind-app")
        self._message_preview = message_preview
        self._has_file_changes = has_file_changes
        self._step = _RewindStep.ACTION
        self._restore_files = False
        self.selected_option = 0
        self.option_widgets: list[Static] = []
        self._title_widget: NoMarkupStatic | None = None
        self._help_widget: NoMarkupStatic | None = None
        self._options = self._build_action_options()

    def _build_action_options(self) -> list[tuple[str, _RewindChoice]]:
        options: list[tuple[str, _RewindChoice]] = []
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

    def _build_persistence_options(self) -> list[tuple[str, _RewindChoice]]:
        return [
            ("Keep in current session", _RewindPersistence.IN_PLACE),
            ("Fork to a new session", _RewindPersistence.FORK),
        ]

    @property
    def has_file_changes(self) -> bool:
        return self._has_file_changes

    def update_preview(self, message_preview: str) -> None:
        self._message_preview = message_preview
        self._reset_to_action_step()
        if self._title_widget is not None:
            self._title_widget.update(self._title_text())

    def _title_text(self) -> str:
        return f"Rewind to: {self._message_preview[:80]}"

    def _help_text(self) -> Content:
        if self._step == _RewindStep.PERSISTENCE:
            return shortcut_hint(
                f"{shortcut('↑↓/jk')} pick option  "
                f"{shortcut('Enter')} confirm  {shortcut('Esc')} back  "
                f"{shortcut('q')} quit"
            )
        return shortcut_hint(
            f"{shortcut('←/Esc')} previous  {shortcut('→')} next  "
            f"{shortcut('↑↓/jk')} pick option  "
            f"{shortcut('Enter')} confirm  {shortcut('q')} quit"
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="rewind-content"):
            self._title_widget = NoMarkupStatic(
                self._title_text(), classes="rewind-title"
            )
            yield self._title_widget
            yield NoMarkupStatic("")
            for _ in range(_MAX_OPTIONS):
                widget = NoMarkupStatic("", classes="rewind-option")
                self.option_widgets.append(widget)
                yield widget
            yield NoMarkupStatic("")
            self._help_widget = NoMarkupStatic(self._help_text(), classes="rewind-help")
            yield self._help_widget

    async def on_mount(self) -> None:
        self._update_options()
        self.focus()

    def _update_options(self) -> None:
        for idx, widget in enumerate(self.option_widgets):
            if idx >= len(self._options):
                widget.update("")
                widget.display = False
                continue

            widget.display = True
            text, _choice = self._options[idx]
            is_selected = idx == self.selected_option
            cursor = "› " if is_selected else "  "
            widget.update(f"{cursor}{idx + 1}. {text}")

            widget.remove_class("rewind-cursor-selected")
            widget.remove_class("rewind-option-unselected")

            if is_selected:
                widget.add_class("rewind-cursor-selected")
            else:
                widget.add_class("rewind-option-unselected")

    def _render_step(self) -> None:
        self._update_options()
        if self._help_widget is not None:
            self._help_widget.update(self._help_text())

    def _reset_to_action_step(self) -> None:
        self._step = _RewindStep.ACTION
        self._restore_files = False
        self._options = self._build_action_options()
        self.selected_option = 0
        self._render_step()

    def _advance_to_persistence(self, *, restore_files: bool) -> None:
        self._restore_files = restore_files
        self._step = _RewindStep.PERSISTENCE
        self._options = self._build_persistence_options()
        self.selected_option = 0
        self._render_step()

    def go_back(self) -> bool:
        if self._step != _RewindStep.PERSISTENCE:
            return False
        self._reset_to_action_step()
        return True

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

    def action_edit_prev(self) -> None:
        self.post_message(self.EditPrev())

    def action_edit_next(self) -> None:
        self.post_message(self.EditNext())

    def action_quit_rewind(self) -> None:
        self.post_message(self.Quit())

    def _handle_selection(self, option: int) -> None:
        if option >= len(self._options):
            return
        _label, choice = self._options[option]
        match choice:
            case _RewindAction.EDIT_AND_RESTORE:
                self._advance_to_persistence(restore_files=True)
            case _RewindAction.EDIT_ONLY:
                self._advance_to_persistence(restore_files=False)
            case _RewindPersistence.IN_PLACE:
                self.post_message(
                    self.RewindConfirmed(
                        restore_files=self._restore_files, inplace=True
                    )
                )
            case _RewindPersistence.FORK:
                self.post_message(
                    self.RewindConfirmed(
                        restore_files=self._restore_files, inplace=False
                    )
                )

    def on_key(self, event: events.Key) -> None:
        self._handle_vim_navigation_key(event)

    def on_blur(self, event: events.Blur) -> None:
        self.call_after_refresh(self.focus)
