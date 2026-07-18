from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.timer import Timer

from vibe.cli.autocompletion.path_completion import PathCompletionController
from vibe.cli.autocompletion.slash_command import SlashCommandController
from vibe.cli.commands import CommandRegistry
from vibe.cli.textual_ui.widgets.chat_input.body import ChatInputBody
from vibe.cli.textual_ui.widgets.chat_input.completion_manager import (
    MultiCompletionManager,
)
from vibe.cli.textual_ui.widgets.chat_input.completion_popup import CompletionPopup
from vibe.cli.textual_ui.widgets.chat_input.glow import (
    GLOW_INTERVAL_SECONDS,
    glow_color,
)
from vibe.cli.textual_ui.widgets.chat_input.text_area import ChatTextArea
from vibe.cli.voice_manager.voice_manager_port import VoiceManagerPort
from vibe.core.agents import AgentSafety
from vibe.core.autocompletion.completers import CommandCompleter, PathCompleter

SAFETY_BORDER_CLASSES: dict[AgentSafety, str] = {
    AgentSafety.SAFE: "border-safe",
    AgentSafety.GUARDED: "border-guarded",
    AgentSafety.DESTRUCTIVE: "border-warning",
    AgentSafety.YOLO: "border-error",
}

GLOWING_SAFETIES = frozenset({AgentSafety.GUARDED})


class ChatInputContainer(Vertical):
    ID_INPUT_BOX = "input-box"

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def __init__(
        self,
        command_registry: CommandRegistry,
        history_file: Path | None = None,
        safety: AgentSafety = AgentSafety.NEUTRAL,
        agent_name: str = "",
        skill_entries_getter: Callable[[], list[tuple[str, str]]] | None = None,
        file_watcher_for_autocomplete_getter: Callable[[], bool] | None = None,
        voice_manager: VoiceManagerPort | None = None,
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
        self._custom_border_label: str | None = None
        self._glow_timer: Timer | None = None
        self._glow_step = 0

        self._completion_manager = MultiCompletionManager([
            SlashCommandController(CommandCompleter(self._get_slash_entries), self),
            PathCompletionController(
                PathCompleter(
                    watcher_enabled_getter=self._file_watcher_for_autocomplete_getter
                ),
                self,
            ),
        ])
        self._body: ChatInputBody | None = None

    def _get_slash_entries(self) -> list[tuple[str, str]]:
        entries = [
            (alias, command.description)
            for command in self._command_registry.commands.values()
            for alias in sorted(command.aliases)
        ]
        if self._skill_entries_getter:
            entries.extend(self._skill_entries_getter())
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
            )

            yield self._body

    def on_mount(self) -> None:
        if not self._body:
            return

        self._body.set_completion_reset_callback(self._completion_manager.reset)
        if self._body.input_widget:
            self._body.input_widget.set_completion_manager(self._completion_manager)
            self._body.focus_input()
        # compose() runs before the app is reachable, so a session that starts in a
        # glowing mode needs the timer picked up here.
        self._sync_glow_timer()

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

    def dismiss_completion(self) -> bool:
        if self._completion_manager.is_active:
            self._completion_manager.reset()
            return True
        return False

    def focus_input(self) -> None:
        if self._body:
            self._body.focus_input()

    def render_completion_suggestions(
        self, suggestions: list[tuple[str, str]], selected_index: int
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

    def _is_glowing(self) -> bool:
        if self._custom_border_label is not None:
            return False
        return self._safety in GLOWING_SAFETIES

    def _sync_glow_timer(self) -> None:
        # Honour reduced motion: a frozen first frame keeps the colour without the
        # pulse, and keeps snapshot tests deterministic.
        try:
            animated = self.app.animation_level != "none"
        except Exception:
            return
        wanted = self._is_glowing() and animated
        if wanted and self._glow_timer is None:
            self._glow_timer = self.set_interval(
                GLOW_INTERVAL_SECONDS, self._advance_glow
            )
        elif not wanted and self._glow_timer is not None:
            self._glow_timer.stop()
            self._glow_timer = None
            self._glow_step = 0
            try:
                self.get_widget_by_id(
                    self.ID_INPUT_BOX
                ).styles.border_title_color = None
            except Exception:
                return

    def _advance_glow(self) -> None:
        self._glow_step += 1
        self._apply_glow_color(glow_color(self._glow_step))

    def _apply_glow_color(self, color: str) -> None:
        try:
            input_box = self.get_widget_by_id(self.ID_INPUT_BOX)
        except Exception:
            return
        input_box.styles.border_title_color = color

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

        self._sync_glow_timer()
        input_box.border_title = self._get_border_title()
