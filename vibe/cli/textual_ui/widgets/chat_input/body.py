from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget

from vibe.cli.history_manager import HistoryManager
from vibe.cli.textual_ui.widgets.chat_input.text_area import ChatTextArea, InputMode
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic

# Braille spinner frames (same as codex)
_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
# Block elements from silent to loud, used for the VU meter.
# Starts at ▁ so even silence shows visible bars.
_BARS = "▁▂▃▄▅▆▇█"


def _peak_to_meter(peak: float) -> str:
    """Convert a 0.0-1.0 peak value to a block-character VU meter string."""
    idx = int(peak * (len(_BARS) - 1))
    idx = max(0, min(idx, len(_BARS) - 1))
    # Centre bar at full level, neighbours taper for a waveform look
    centre = _BARS[idx]
    side1 = _BARS[max(0, idx - 1)]
    side2 = _BARS[max(0, idx - 2)]
    return f"●{side2}{side1}{centre}{side1}{side2}"


class ChatInputBody(Widget):
    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def __init__(
        self,
        history_file: Path | None = None,
        nuage_enabled: bool = False,
        voice_mode: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.input_widget: ChatTextArea | None = None
        self.prompt_widget: NoMarkupStatic | None = None
        self._nuage_enabled = nuage_enabled
        self._voice_mode = voice_mode

        if history_file:
            self.history = HistoryManager(history_file)
        else:
            self.history = None

        self._completion_reset: Callable[[], None] | None = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            self.prompt_widget = NoMarkupStatic(">", id="prompt")
            yield self.prompt_widget

            self.input_widget = ChatTextArea(
                id="input",
                nuage_enabled=self._nuage_enabled,
                voice_mode=self._voice_mode,
            )
            yield self.input_widget

    def on_mount(self) -> None:
        if self.input_widget:
            self.input_widget.focus()

    def _parse_mode_and_text(self, text: str) -> tuple[InputMode, str]:
        if text.startswith("!"):
            return "!", text[1:]
        elif text.startswith("/"):
            return "/", text[1:]
        elif text.startswith("&") and self._nuage_enabled:
            return "&", text[1:]
        else:
            return ">", text

    def _update_prompt(self) -> None:
        if not self.input_widget or not self.prompt_widget:
            return

        self.prompt_widget.update(self.input_widget.input_mode)

    def on_chat_text_area_mode_changed(self, event: ChatTextArea.ModeChanged) -> None:
        if self.prompt_widget:
            self.prompt_widget.update(event.mode)

    def _load_history_entry(self, text: str, cursor_col: int | None = None) -> None:
        if not self.input_widget:
            return

        mode, display_text = self._parse_mode_and_text(text)

        self.input_widget._navigating_history = True
        self.input_widget.set_mode(mode)
        self.input_widget.load_text(display_text)

        first_line = display_text.split("\n")[0]
        col = cursor_col if cursor_col is not None else len(first_line)
        cursor_pos = (0, col)

        self.input_widget.move_cursor(cursor_pos)
        self.input_widget._last_cursor_col = col
        self.input_widget._cursor_pos_after_load = cursor_pos
        self.input_widget._cursor_moved_since_load = False

        self._update_prompt()
        self._notify_completion_reset()

    def on_chat_text_area_history_previous(
        self, event: ChatTextArea.HistoryPrevious
    ) -> None:
        if not self.history or not self.input_widget:
            return

        if self.history._current_index == -1:
            self.input_widget._original_text = self.input_widget.text

        if (
            self.history._current_index != -1
            and self.input_widget._last_used_prefix is not None
            and self.input_widget._last_used_prefix != event.prefix
        ):
            self.history.reset_navigation()

        self.input_widget._last_used_prefix = event.prefix
        previous = self.history.get_previous(
            self.input_widget._original_text, prefix=event.prefix
        )

        if previous is not None:
            self._load_history_entry(previous)

    def on_chat_text_area_history_next(self, event: ChatTextArea.HistoryNext) -> None:
        if not self.history or not self.input_widget:
            return

        if self.history._current_index == -1:
            return

        if (
            self.input_widget._last_used_prefix is not None
            and self.input_widget._last_used_prefix != event.prefix
        ):
            self.history.reset_navigation()

        self.input_widget._last_used_prefix = event.prefix

        has_next = any(
            self.history._entries[i].startswith(event.prefix)
            for i in range(self.history._current_index + 1, len(self.history._entries))
        )

        original_matches = self.input_widget._original_text.startswith(event.prefix)

        if has_next or original_matches:
            next_entry = self.history.get_next(prefix=event.prefix)
            if next_entry is not None:
                cursor_col = (
                    len(event.prefix) if self.history._current_index == -1 else None
                )
                self._load_history_entry(next_entry, cursor_col=cursor_col)

    def on_chat_text_area_history_reset(self, event: ChatTextArea.HistoryReset) -> None:
        if self.history:
            self.history.reset_navigation()
        if self.input_widget:
            self.input_widget._original_text = ""
            self.input_widget._cursor_pos_after_load = None
            self.input_widget._cursor_moved_since_load = False

    def on_chat_text_area_submitted(self, event: ChatTextArea.Submitted) -> None:
        event.stop()

        if not self.input_widget:
            return

        value = event.value.strip()
        if value:
            if self.history:
                self.history.add(value)
                self.history.reset_navigation()

            self.input_widget.clear_text()
            self._update_prompt()

            self._notify_completion_reset()

            self.post_message(self.Submitted(value))

    @property
    def value(self) -> str:
        if not self.input_widget:
            return ""
        return self.input_widget.get_full_text()

    @value.setter
    def value(self, text: str) -> None:
        if self.input_widget:
            mode, display_text = self._parse_mode_and_text(text)
            self.input_widget.set_mode(mode)
            self.input_widget.load_text(display_text)
            self._update_prompt()

    def focus_input(self) -> None:
        if self.input_widget:
            self.input_widget.focus()

    def set_completion_reset_callback(
        self, callback: Callable[[], None] | None
    ) -> None:
        self._completion_reset = callback

    def _notify_completion_reset(self) -> None:
        if self._completion_reset:
            self._completion_reset()

    # -- voice helpers ----------------------------------------------------------

    def set_voice_recording(self, recording: bool) -> None:
        """Toggle the recording flag and update prompt classes."""
        if not self.input_widget or not self.prompt_widget:
            return
        self.input_widget._voice_recording = recording
        if recording:
            self.prompt_widget.add_class("voice-recording")
            self.prompt_widget.remove_class("voice-transcribing")
        else:
            self.prompt_widget.remove_class("voice-recording")
            self._update_prompt()

    def set_voice_transcribing(self, transcribing: bool) -> None:
        """Toggle the transcribing state on the prompt."""
        if not self.prompt_widget:
            return
        if transcribing:
            self.prompt_widget.add_class("voice-transcribing")
            self.prompt_widget.remove_class("voice-recording")
        else:
            self.prompt_widget.remove_class("voice-transcribing")
            self._update_prompt()

    def update_voice_prompt(self, text: str) -> None:
        """Set the prompt widget text directly (used by app-level animation).

        Forces a layout refresh so the Horizontal parent re-flows.
        """
        if not self.prompt_widget:
            return
        self.prompt_widget.update(text)
        self.prompt_widget.refresh(layout=True)
        if self.prompt_widget.parent:
            self.prompt_widget.parent.refresh(layout=True)

    def replace_input(self, text: str, cursor_offset: int | None = None) -> None:
        if not self.input_widget:
            return

        self.input_widget.load_text(text)
        self.input_widget.reset_history_state()
        self._update_prompt()

        if cursor_offset is not None:
            self.input_widget.set_cursor_offset(max(0, min(cursor_offset, len(text))))
