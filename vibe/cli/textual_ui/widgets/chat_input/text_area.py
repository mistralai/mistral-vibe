from __future__ import annotations

from typing import Any, ClassVar, Literal

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea

from vibe.cli.autocompletion.base import CompletionResult
from vibe.cli.textual_ui.external_editor import ExternalEditor
from vibe.cli.textual_ui.widgets.chat_input.completion_manager import (
    MultiCompletionManager,
)

InputMode = Literal["!", "/", ">", "&"]


class ChatTextArea(TextArea):
    BINDINGS: ClassVar[list[Binding]] = [
        Binding(
            "shift+enter,ctrl+j",
            "insert_newline",
            "New Line",
            show=False,
            priority=True,
        ),
        Binding("ctrl+g", "open_external_editor", "External Editor", show=False),
    ]

    DEFAULT_MODE: ClassVar[Literal[">"]] = ">"

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    class HistoryPrevious(Message):
        def __init__(self, prefix: str) -> None:
            self.prefix = prefix
            super().__init__()

    class HistoryNext(Message):
        def __init__(self, prefix: str) -> None:
            self.prefix = prefix
            super().__init__()

    class HistoryReset(Message):
        """Message sent when history navigation should be reset."""

    class ModeChanged(Message):
        """Message sent when the input mode changes (>, !, /, &)."""

        def __init__(self, mode: InputMode) -> None:
            self.mode = mode
            super().__init__()

    def __init__(self, nuage_enabled: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._nuage_enabled = nuage_enabled
        self._input_mode: InputMode = self.DEFAULT_MODE
        self._history_prefix: str | None = None
        self._last_text = ""
        self._navigating_history = False
        self._last_cursor_col: int = 0
        self._last_used_prefix: str | None = None
        self._original_text: str = ""
        self._cursor_pos_after_load: tuple[int, int] | None = None
        self._cursor_moved_since_load: bool = False
        self._completion_manager: MultiCompletionManager | None = None
        self._app_has_focus: bool = True

    def on_blur(self, event: events.Blur) -> None:
        if self._app_has_focus:
            self.call_after_refresh(self.focus)

    def set_app_focus(self, has_focus: bool) -> None:
        self._app_has_focus = has_focus
        self.cursor_blink = has_focus
        if has_focus and not self.has_focus:
            self.call_after_refresh(self.focus)

    def on_click(self, event: events.Click) -> None:
        self._mark_cursor_moved_if_needed()

    def action_insert_newline(self) -> None:
        self.insert("\n")

    def action_open_external_editor(self) -> None:
        editor = ExternalEditor()
        current_text = self.get_full_text()

        with self.app.suspend():
            result = editor.edit(current_text)

        if result is not None:
            self.clear()
            self.insert(result)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if not self._navigating_history and self.text != self._last_text:
            self._reset_prefix()
            self._original_text = ""
            self._cursor_pos_after_load = None
            self._cursor_moved_since_load = False
            self.post_message(self.HistoryReset())
        self._last_text = self.text
        was_navigating_history = self._navigating_history
        self._navigating_history = False

        if self._completion_manager and not was_navigating_history:
            self._completion_manager.on_text_changed(
                self.get_full_text(), self._get_full_cursor_offset()
            )

    def _reset_prefix(self) -> None:
        self._history_prefix = None
        self._last_used_prefix = None

    def _mark_cursor_moved_if_needed(self) -> None:
        if (
            self._cursor_pos_after_load is not None
            and not self._cursor_moved_since_load
            and self.cursor_location != self._cursor_pos_after_load
        ):
            self._cursor_moved_since_load = True
            self._reset_prefix()

    def _get_prefix_up_to_cursor(self) -> str:
        cursor_row, cursor_col = self.cursor_location
        lines = self.text.split("\n")
        if cursor_row < len(lines):
            visible_prefix = lines[cursor_row][:cursor_col]
            if cursor_row == 0 and self._input_mode != self.DEFAULT_MODE:
                return self._input_mode + visible_prefix
            return visible_prefix
        return ""

    def _handle_history_up(self) -> bool:
        cursor_row, cursor_col = self.cursor_location
        if cursor_row == 0:
            if self._history_prefix is not None and cursor_col != self._last_cursor_col:
                self._reset_prefix()
                self._last_cursor_col = 0

            if self._history_prefix is None:
                self._history_prefix = self._get_prefix_up_to_cursor()

            self._navigating_history = True
            self.post_message(self.HistoryPrevious(self._history_prefix))
            return True
        return False

    def _handle_history_down(self) -> bool:
        cursor_row, cursor_col = self.cursor_location
        total_lines = self.text.count("\n") + 1

        on_first_line_unmoved = cursor_row == 0 and not self._cursor_moved_since_load
        on_last_line = cursor_row == total_lines - 1

        should_intercept = (
            on_first_line_unmoved and self._history_prefix is not None
        ) or on_last_line

        if not should_intercept:
            return False

        if self._history_prefix is not None and cursor_col != self._last_cursor_col:
            self._reset_prefix()
            self._last_cursor_col = 0

        if self._history_prefix is None:
            self._history_prefix = self._get_prefix_up_to_cursor()

        self._navigating_history = True
        self.post_message(self.HistoryNext(self._history_prefix))
        return True

    async def _on_key(self, event: events.Key) -> None:  # noqa: PLR0911
        self._mark_cursor_moved_if_needed()

        manager = self._completion_manager
        if manager:
            match manager.on_key(
                event, self.get_full_text(), self._get_full_cursor_offset()
            ):
                case CompletionResult.HANDLED:
                    event.prevent_default()
                    event.stop()
                    return
                case CompletionResult.SUBMIT:
                    event.prevent_default()
                    event.stop()
                    value = self.get_full_text().strip()
                    if value:
                        self._reset_prefix()
                        self.post_message(self.Submitted(value))
                    return

        if event.key == "enter":
            event.prevent_default()
            event.stop()
            value = self.get_full_text().strip()
            if value:
                self._reset_prefix()
                self.post_message(self.Submitted(value))
            return

        if event.key == "shift+enter":
            event.prevent_default()
            event.stop()
            return

        if (
            event.character
            and event.character in self.mode_characters
            and not self.text
            and self._input_mode == self.DEFAULT_MODE
        ):
            self._set_mode(event.character)
            event.prevent_default()
            event.stop()
            return

        if event.key == "backspace" and self._should_reset_mode_on_backspace():
            self._set_mode(self.DEFAULT_MODE)
            event.prevent_default()
            event.stop()
            return

        if event.key == "up" and self._handle_history_up():
            event.prevent_default()
            event.stop()
            return

        if event.key == "down" and self._handle_history_down():
            event.prevent_default()
            event.stop()
            return

        await super()._on_key(event)
        self._mark_cursor_moved_if_needed()

    def set_completion_manager(self, manager: MultiCompletionManager | None) -> None:
        self._completion_manager = manager
        if self._completion_manager:
            self._completion_manager.on_text_changed(
                self.get_full_text(), self._get_full_cursor_offset()
            )

    def get_cursor_offset(self) -> int:
        text = self.text
        row, col = self.cursor_location

        if not text:
            return 0

        lines = text.split("\n")
        row = max(0, min(row, len(lines) - 1))
        col = max(0, col)

        offset = sum(len(lines[i]) + 1 for i in range(row))
        return offset + min(col, len(lines[row]))

    def set_cursor_offset(self, offset: int) -> None:
        text = self.text
        if offset <= 0:
            self.move_cursor((0, 0))
            return

        if offset >= len(text):
            lines = text.split("\n")
            if not lines:
                self.move_cursor((0, 0))
                return
            last_row = len(lines) - 1
            self.move_cursor((last_row, len(lines[last_row])))
            return

        remaining = offset
        lines = text.split("\n")

        for row, line in enumerate(lines):
            line_length = len(line)
            if remaining <= line_length:
                self.move_cursor((row, remaining))
                return
            remaining -= line_length + 1

        last_row = len(lines) - 1
        self.move_cursor((last_row, len(lines[last_row])))

    def reset_history_state(self) -> None:
        self._reset_prefix()
        self._original_text = ""
        self._cursor_pos_after_load = None
        self._cursor_moved_since_load = False
        self._last_text = self.text

    def clear_text(self) -> None:
        self.clear()
        self.reset_history_state()
        self._set_mode(self.DEFAULT_MODE)

    def _set_mode(self, mode: InputMode) -> None:
        if self._input_mode == mode:
            return
        self._input_mode = mode
        self.post_message(self.ModeChanged(mode))
        if self._completion_manager:
            self._completion_manager.on_text_changed(
                self.get_full_text(), self._get_full_cursor_offset()
            )

    def _should_reset_mode_on_backspace(self) -> bool:
        return (
            self._input_mode != self.DEFAULT_MODE
            and not self.text
            and self.get_cursor_offset() == 0
        )

    def get_full_text(self) -> str:
        if self._input_mode != self.DEFAULT_MODE:
            return self._input_mode + self.text
        return self.text

    def _get_full_cursor_offset(self) -> int:
        return self.get_cursor_offset() + self._get_mode_prefix_length()

    def _get_mode_prefix_length(self) -> int:
        return {">": 0, "/": 1, "!": 1, "&": 1}[self._input_mode]

    @property
    def mode_characters(self) -> set[InputMode]:
        chars: set[InputMode] = {"!", "/"}
        if self._nuage_enabled:
            chars.add("&")
        return chars

    @property
    def input_mode(self) -> InputMode:
        return self._input_mode

    def set_mode(self, mode: InputMode) -> None:
        if self._input_mode != mode:
            self._input_mode = mode
            self.post_message(self.ModeChanged(mode))

    def adjust_from_full_text_coords(
        self, start: int, end: int, replacement: str
    ) -> tuple[int, int, str]:
        """Translate from full-text coordinates to widget coordinates.

        The completion manager works with 'full text' that includes the mode prefix.
        This adjusts coordinates and replacement text for the actual widget text.
        """
        mode_len = self._get_mode_prefix_length()

        adj_start = max(0, start - mode_len)
        adj_end = max(adj_start, end - mode_len)

        if mode_len > 0 and replacement.startswith(self._input_mode):
            replacement = replacement[mode_len:]

        return adj_start, adj_end, replacement
