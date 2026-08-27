from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message

from vibe.app_server.models import AgentSafety
from vibe.cli.autocompletion.base import CompletionEntry
from vibe.cli.autocompletion.completers import CommandCompleter, PathCompleter
from vibe.cli.autocompletion.inline_skill_completion import (
    InlineSkillCompletionController,
)
from vibe.cli.autocompletion.path_completion import PathCompletionController
from vibe.cli.autocompletion.slash_command import SlashCommandController
from vibe.cli.commands import CommandRegistry
from vibe.cli.textual_ui.queue_kinds import QueuedItemKind
from vibe.cli.textual_ui.widgets.chat_input.body import ChatInputBody
from vibe.cli.textual_ui.widgets.chat_input.completion_manager import (
    MultiCompletionManager,
)
from vibe.cli.textual_ui.widgets.chat_input.completion_popup import CompletionPopup
from vibe.cli.textual_ui.widgets.chat_input.text_area import ChatTextArea
from vibe.cli.voice_manager.voice_manager_port import VoiceManagerPort

SAFETY_BORDER_CLASSES: dict[AgentSafety, str] = {
    AgentSafety.SAFE: "border-safe",
    AgentSafety.DESTRUCTIVE: "border-warning",
    AgentSafety.YOLO: "border-error",
}


class ChatInputContainer(Vertical):
    ID_INPUT_BOX = "input-box"

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    class QueueEditSubmitted(Message):
        def __init__(self, value: str, kind: QueuedItemKind) -> None:
            self.value = value
            self.kind = kind
            super().__init__()

    class QueueEditConsumed(Message):
        def __init__(self, value: str, kind: QueuedItemKind) -> None:
            self.value = value
            self.kind = kind
            super().__init__()

    class QueueRemoveRequested(Message):
        pass

    class QueueSelectionScroll(Message):
        def __init__(self, queue_index: int) -> None:
            self.queue_index = queue_index
            super().__init__()

    class QueueModeExited(Message):
        pass

    def __init__(
        self,
        command_registry: CommandRegistry,
        history_file: Path | None = None,
        safety: AgentSafety = AgentSafety.NEUTRAL,
        agent_name: str = "",
        skill_entries_getter: Callable[[], list[tuple[str, str]]] | None = None,
        file_watcher_for_autocomplete_getter: Callable[[], bool] | None = None,
        voice_manager: VoiceManagerPort | None = None,
        queue_edit_active_getter: Callable[[], bool] | None = None,
        queue_items_getter: Callable[[], list[tuple[int, QueuedItemKind, str]]]
        | None = None,
        queue_selected_index_getter: Callable[[], int | None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._history_file = history_file
        self._command_registry = command_registry
        self._safety = safety
        self._agent_name = agent_name
        self._skill_entries_getter = skill_entries_getter
        self._file_watcher_for_autocomplete_getter = (
            file_watcher_for_autocomplete_getter
        )
        self._voice_manager = voice_manager
        self._queue_edit_active_getter = queue_edit_active_getter
        self._queue_items_getter = queue_items_getter
        self._queue_selected_index_getter = queue_selected_index_getter
        self._custom_border_label: str | None = None

        self._completion_manager = MultiCompletionManager([
            SlashCommandController(CommandCompleter(self._get_slash_entries), self),
            PathCompletionController(
                PathCompleter(
                    watcher_enabled_getter=self._file_watcher_for_autocomplete_getter
                ),
                self,
            ),
            InlineSkillCompletionController(
                self._skill_entries_getter or (lambda: []),
                self,
                self._input_is_default_mode,
            ),
        ])
        self._body: ChatInputBody | None = None

    def _get_slash_entries(self) -> list[CompletionEntry]:
        entries = [
            CompletionEntry(alias, command.description)
            for command in self._command_registry.commands.values()
            for alias in sorted(command.aliases)
        ]
        if self._skill_entries_getter:
            entries.extend(
                CompletionEntry(alias, desc)
                for alias, desc in self._skill_entries_getter()
            )
        return sorted(entries)

    def compose(self) -> ComposeResult:
        yield CompletionPopup()

        border_class = self._get_border_class()
        with Vertical(id=self.ID_INPUT_BOX, classes=border_class) as input_box:
            input_box.border_title = self._get_border_title()
            self._body = ChatInputBody(
                history_file=self._history_file,
                command_registry=self._command_registry,
                id="input-body",
                voice_manager=self._voice_manager,
                queue_edit_active_getter=self._queue_edit_active_getter,
                queue_items_getter=self._queue_items_getter,
                queue_selected_index_getter=self._queue_selected_index_getter,
            )

            yield self._body

    def on_mount(self) -> None:
        if not self._body:
            return

        if self._body.input_widget:
            self._body.input_widget.set_completion_manager(self._completion_manager)
            self._body.focus_input()

    def on_chat_input_body_completion_reset_requested(
        self, _event: ChatInputBody.CompletionResetRequested
    ) -> None:
        self._completion_manager.reset()

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

    def _input_is_default_mode(self) -> bool:
        widget = self.input_widget
        return widget is None or widget.is_default_mode

    def dismiss_completion(self) -> bool:
        return self._completion_manager.dismiss()

    def focus_input(self) -> None:
        if self._body:
            self._body.focus_input()

    def render_completion_suggestions(
        self, suggestions: list[CompletionEntry], selected_index: int
    ) -> None:
        try:
            popup = self.query_one(CompletionPopup)
        except Exception:
            return
        popup.update_suggestions(suggestions, selected_index)

    def clear_completion_suggestions(self) -> None:
        try:
            popup = self.query_one(CompletionPopup)
        except Exception:
            return
        popup.hide()

    def show_inline_suggestion(self, suggestion: str) -> None:
        if widget := self.input_widget:
            widget.set_inline_suggestion(suggestion)

    def clear_inline_suggestion(self) -> None:
        if widget := self.input_widget:
            widget.clear_inline_suggestion()

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

    def replace_completion_range(
        self, start: int, end: int, replacement: str, *, suppress_update: bool = False
    ) -> None:
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

        if suppress_update:
            widget.applying_completion = True
        self._body.replace_input(new_text, cursor_offset=start + len(insertion))

    def on_chat_input_body_submitted(self, event: ChatInputBody.Submitted) -> None:
        event.stop()
        self.post_message(self.Submitted(event.value))

    def on_chat_input_body_queue_edit_submitted(
        self, event: ChatInputBody.QueueEditSubmitted
    ) -> None:
        event.stop()
        self.post_message(self.QueueEditSubmitted(event.value, event.kind))

    def on_chat_input_body_queue_edit_consumed(
        self, event: ChatInputBody.QueueEditConsumed
    ) -> None:
        event.stop()
        self.post_message(self.QueueEditConsumed(event.value, event.kind))

    def on_chat_input_body_queue_remove_requested(
        self, event: ChatInputBody.QueueRemoveRequested
    ) -> None:
        event.stop()
        self.post_message(self.QueueRemoveRequested())

    def on_chat_input_body_queue_selection_scroll(
        self, event: ChatInputBody.QueueSelectionScroll
    ) -> None:
        event.stop()
        self.post_message(self.QueueSelectionScroll(event.queue_index))

    def on_chat_input_body_queue_mode_exited(
        self, _event: ChatInputBody.QueueModeExited
    ) -> None:
        self.post_message(self.QueueModeExited())

    @property
    def switching_mode(self) -> bool:
        return self._body.switching_mode if self._body else False

    @switching_mode.setter
    def switching_mode(self, value: bool) -> None:
        self.set_switching_mode(value)

    def set_switching_mode(
        self, value: bool, *, show_indicator: bool | None = None
    ) -> None:
        if self._body:
            self._body.set_switching_mode(value, show_indicator=show_indicator)

    def set_safety(self, safety: AgentSafety) -> None:
        self._safety = safety
        self._apply_input_box_chrome()

    def replace_command_registry(self, registry: CommandRegistry) -> None:
        self._command_registry = registry

    def replace_voice_manager(self, voice_manager: VoiceManagerPort | None) -> None:
        if self._voice_manager is voice_manager:
            return
        self._voice_manager = voice_manager
        if self._body:
            self._body.replace_voice_manager(voice_manager)

    def set_agent_name(self, name: str) -> None:
        self._agent_name = name
        self._apply_input_box_chrome()

    def set_custom_border(self, label: str | None) -> None:
        self._custom_border_label = label
        self._apply_input_box_chrome()

    def _get_border_class(self) -> str:
        if self._custom_border_label is not None:
            return ""
        return SAFETY_BORDER_CLASSES.get(self._safety, "")

    def _get_border_title(self) -> str:
        label = self._custom_border_label or self._agent_name
        return f" {label} " if label else ""

    def _apply_input_box_chrome(self) -> None:
        try:
            input_box = self.get_widget_by_id(self.ID_INPUT_BOX)
        except Exception:
            return

        for border_class in SAFETY_BORDER_CLASSES.values():
            input_box.remove_class(border_class)

        border_class = self._get_border_class()
        if border_class:
            input_box.add_class(border_class)

        input_box.border_title = self._get_border_title()
