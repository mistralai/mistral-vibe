from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message

from vibe.cli.autocompletion.path_completion import PathCompletionController
from vibe.cli.autocompletion.slash_command import SlashCommandController
from vibe.cli.commands import CommandRegistry
from vibe.cli.textual_ui.widgets.chat_input.body import ChatInputBody
from vibe.cli.textual_ui.widgets.chat_input.completion_manager import (
    MultiCompletionManager,
)
from vibe.cli.textual_ui.widgets.chat_input.completion_popup import CompletionPopup
from vibe.cli.textual_ui.widgets.chat_input.text_area import ChatTextArea
from vibe.core.autocompletion.completers import CommandCompleter, PathCompleter
from vibe.core.modes import ModeSafety

SAFETY_BORDER_CLASSES: dict[ModeSafety, str] = {
    ModeSafety.SAFE: "border-safe",
    ModeSafety.DESTRUCTIVE: "border-warning",
    ModeSafety.YOLO: "border-error",
}


class ChatInputContainer(Vertical):
    ID_INPUT_BOX = "input-box"

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def __init__(
        self,
        history_file: Path | None = None,
        command_registry: CommandRegistry | None = None,
        safety: ModeSafety = ModeSafety.NEUTRAL,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._history_file = history_file
        self._command_registry = command_registry or CommandRegistry()
        self._safety = safety

        command_entries = [
            (alias, command.description)
            for command in self._command_registry.commands.values()
            for alias in sorted(command.aliases)
        ]

        self._completion_manager = MultiCompletionManager([
            SlashCommandController(CommandCompleter(command_entries), self),
            PathCompletionController(PathCompleter(), self),
        ])
        self._completion_popup: CompletionPopup | None = None
        self._body: ChatInputBody | None = None

    def compose(self) -> ComposeResult:
        self._completion_popup = CompletionPopup()
        yield self._completion_popup

        border_class = SAFETY_BORDER_CLASSES.get(self._safety, "")
        with Vertical(id=self.ID_INPUT_BOX, classes=border_class):
            self._body = ChatInputBody(history_file=self._history_file, id="input-body")

            yield self._body

    def on_mount(self) -> None:
        if not self._body:
            return

        self._body.set_completion_reset_callback(self._completion_manager.reset)
        if self._body.input_widget:
            self._body.input_widget.set_completion_manager(self._completion_manager)
            self._body.focus_input()

    @property
    def input_widget(self) -> ChatTextArea | None:
        return self._body.input_widget if self._body else None

    @property
    def value(self) -> str:
        if not self._body:
            return ""
        return self._body.value

    @value.setter
    def value(self, text: str) -> None:
        if not self._body:
            return
        self._body.value = text
        widget = self._body.input_widget
        if widget:
            self._completion_manager.on_text_changed(
                widget.get_full_text(), widget._get_full_cursor_offset()
            )

    def focus_input(self) -> None:
        if self._body:
            self._body.focus_input()

    def render_completion_suggestions(
        self, suggestions: list[tuple[str, str]], selected_index: int
    ) -> None:
        if self._completion_popup:
            self._completion_popup.update_suggestions(suggestions, selected_index)

    def clear_completion_suggestions(self) -> None:
        if self._completion_popup:
            self._completion_popup.hide()

    def _format_insertion(self, replacement: str, suffix: str) -> str:
        """Format the insertion text with appropriate spacing.

        Args:
            replacement: The text to insert
            suffix: The text that follows the insertion point

        Returns:
            The formatted insertion text with spacing if needed
        """
        if replacement.startswith("@"):
            if replacement.endswith("/"):
                return replacement
            # For @-prefixed completions, add space unless suffix starts with whitespace
            return replacement + (" " if not suffix or not suffix[0].isspace() else "")

        # For other completions, add space only if suffix exists and doesn't start with whitespace
        return replacement + (" " if suffix and not suffix[0].isspace() else "")

    def replace_completion_range(self, start: int, end: int, replacement: str) -> None:
        widget = self.input_widget
        if not widget or not self._body:
            return
        start, end, replacement = widget.adjust_from_full_text_coords(
            start, end, replacement
        )

        text = widget.text
        start = max(0, min(start, len(text)))
        end = max(start, min(end, len(text)))

        prefix = text[:start]
        suffix = text[end:]
        insertion = self._format_insertion(replacement, suffix)
        new_text = f"{prefix}{insertion}{suffix}"

        self._body.replace_input(new_text, cursor_offset=start + len(insertion))

    def on_chat_input_body_submitted(self, event: ChatInputBody.Submitted) -> None:
        event.stop()
        self.post_message(self.Submitted(event.value))

    def set_safety(self, safety: ModeSafety) -> None:
        self._safety = safety

        input_box = self.get_widget_by_id(self.ID_INPUT_BOX)
        for border_class in SAFETY_BORDER_CLASSES.values():
            input_box.remove_class(border_class)

        if safety in SAFETY_BORDER_CLASSES:
            input_box.add_class(SAFETY_BORDER_CLASSES[safety])
