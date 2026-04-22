from __future__ import annotations

from pathlib import Path
import shlex
from typing import Any, ClassVar, Literal

from textual import events
from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea

from vibe.cli.autocompletion.base import CompletionResult
from vibe.cli.clipboard import (
    MAX_IMAGE_BYTES,
    _encode_image_data_url,
    _read_clipboard,
    _read_clipboard_image,
)
from vibe.cli.textual_ui.external_editor import ExternalEditor
from vibe.cli.textual_ui.widgets.chat_input.completion_manager import (
    MultiCompletionManager,
)
from vibe.cli.textual_ui.widgets.vscode_compat import patch_vscode_space
from vibe.cli.voice_manager.voice_manager_port import (
    RecordingStartError,
    TranscribeState,
    VoiceManagerPort,
)
from vibe.core.logger import logger
from vibe.core.types import ImageContentPart

_IMAGE_SUFFIX_TO_MEDIA: dict[
    str, Literal["image/png", "image/jpeg", "image/webp", "image/gif"]
] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _image_from_dropped_path(
    text: str,
) -> tuple[bytes, Literal["image/png", "image/jpeg", "image/webp", "image/gif"]] | None:
    """Turn a pasted file path into raw image bytes if it points at one image.

    Handles the Finder drag-and-drop case: the terminal inserts the dragged
    file's absolute path. If that path is an image file under MAX_IMAGE_BYTES
    the caller can attach it as if it had been on the clipboard. Anything
    ambiguous (multi-word text, relative paths, non-image extensions, missing
    file) returns None so the normal text-paste path can take over.

    shlex.split handles macOS Terminal's shell-style escaping of dragged
    paths (backslash-escaped spaces, parens, quotes) uniformly — this is
    the same approach textual-filedrop uses for cross-terminal file drops.
    """
    candidate = text.strip()
    if not candidate or "\n" in candidate:
        return None
    try:
        tokens = shlex.split(candidate)
    except ValueError:
        tokens = []
    if len(tokens) != 1:
        return None
    path = Path(tokens[0])
    media_type = _IMAGE_SUFFIX_TO_MEDIA.get(path.suffix.lower())
    if media_type is None or not path.is_absolute() or not path.is_file():
        return None
    try:
        data = path.read_bytes() if path.stat().st_size <= MAX_IMAGE_BYTES else None
    except OSError:
        data = None
    return (data, media_type) if data is not None else None

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
        Binding(
            "ctrl+v",
            "paste_with_images",
            "Paste",
            show=False,
            priority=True,
        ),
    ]

    DEFAULT_MODE: ClassVar[Literal[">"]] = ">"

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    class HistoryPrevious(Message):
        pass

    class HistoryNext(Message):
        pass

    class HistoryReset(Message):
        """Message sent when history navigation should be reset."""

    class ModeChanged(Message):
        """Message sent when the input mode changes (>, !, /, &)."""

        def __init__(self, mode: InputMode) -> None:
            self.mode = mode
            super().__init__()

    def __init__(
        self,
        nuage_enabled: bool = False,
        voice_manager: VoiceManagerPort | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._nuage_enabled = nuage_enabled
        self._input_mode: InputMode = self.DEFAULT_MODE
        self._last_text = ""
        self._navigating_history = False
        self._original_text: str = ""
        self._cursor_pos_after_load: tuple[int, int] | None = None
        self._cursor_moved_since_load: bool = False
        self._completion_manager: MultiCompletionManager | None = None
        self._app_has_focus: bool = True
        self._voice_manager = voice_manager
        self._pending_images: list[ImageContentPart] = []

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

    def action_paste_with_images(self) -> None:
        if self._handle_image_attach(fallback_text=None):
            return
        text = _read_clipboard()
        if text:
            self.insert(text)

    def on_paste(self, event: events.Paste) -> None:
        if self._handle_image_attach(fallback_text=event.text):
            event.prevent_default()
            event.stop()

    def _handle_image_attach(self, fallback_text: str | None) -> bool:
        # A dragged file path wins over the clipboard: drag-drop does NOT
        # update the clipboard, so reading it first would attach an unrelated
        # stale screenshot instead of the file the user just dropped.
        if fallback_text:
            dropped = _image_from_dropped_path(fallback_text)
            if dropped is not None:
                self._attach_image_from_bytes(*dropped)
                return True

        try:
            clipboard_image = _read_clipboard_image()
        except Exception:
            logger.warning("clipboard image read failed", exc_info=True)
            clipboard_image = None

        if clipboard_image is not None:
            self._attach_image_from_bytes(*clipboard_image)
            return True

        return False

    def _attach_image_from_bytes(
        self,
        data: bytes,
        media_type: Literal["image/png", "image/jpeg", "image/webp", "image/gif"],
    ) -> None:
        url = _encode_image_data_url(data, media_type)
        part = ImageContentPart(image_url=url, media_type=media_type)
        kb = len(data) // 1024
        self._attach_image_placeholder(part, kb)

    def _attach_image_placeholder(self, part: ImageContentPart, size_kb: int) -> None:
        self._pending_images.append(part)
        placeholder = f"[Image #{len(self._pending_images)}]"
        self.insert(placeholder)
        self.notify(
            f"Image attached ({size_kb} KB)",
            severity="information",
            timeout=2,
            markup=False,
        )

    def take_pending_images(self) -> list[ImageContentPart]:
        images = self._pending_images
        self._pending_images = []
        return images

    @property
    def has_pending_images(self) -> bool:
        return bool(self._pending_images)

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

    def _mark_cursor_moved_if_needed(self) -> None:
        if (
            self._cursor_pos_after_load is not None
            and not self._cursor_moved_since_load
            and self.cursor_location != self._cursor_pos_after_load
        ):
            self._cursor_moved_since_load = True

    def _handle_history_up(self) -> bool:
        history_loaded_and_cursor_unmoved = (
            self._cursor_pos_after_load is not None
            and not self._cursor_moved_since_load
        )
        should_intercept = (
            self.navigator.is_first_wrapped_line(self.cursor_location)
            or history_loaded_and_cursor_unmoved
        )

        if should_intercept:
            self._navigating_history = True
            self.post_message(self.HistoryPrevious())
            return True
        return False

    def _is_on_loaded_history_entry(self) -> bool:
        return self._cursor_pos_after_load is not None

    def _should_intercept_history_down(self) -> bool:
        if self._is_on_loaded_history_entry() and not self._cursor_moved_since_load:
            return True

        if not self.navigator.is_last_wrapped_line(self.cursor_location):
            return False

        return self._is_on_loaded_history_entry()

    def _handle_history_down(self) -> bool:
        if not self._should_intercept_history_down():
            return False

        self._navigating_history = True
        self.post_message(self.HistoryNext())
        return True

    class FeedbackKeyPressed(Message):
        def __init__(self, rating: int) -> None:
            self.rating = rating
            super().__init__()

    class NonFeedbackKeyPressed(Message):
        pass

    feedback_active: bool = False

    async def _handle_voice_key(self, event: events.Key) -> bool:
        if not self._voice_manager:
            return False

        # Handle key pressed during audio recording
        if self._voice_manager.transcribe_state != TranscribeState.IDLE:
            event.prevent_default()
            event.stop()
            if event.key == "ctrl+c":  # Escape is handled in app.py
                self._voice_manager.cancel_recording()
            elif self._voice_manager.transcribe_state == TranscribeState.RECORDING:
                await self._voice_manager.stop_recording()
            return True

        # Handle audio record keybind
        if self._voice_manager.is_enabled and event.key == "ctrl+r":
            event.prevent_default()
            event.stop()
            try:
                self._voice_manager.start_recording()
            except RecordingStartError as e:
                self.notify(str(e), severity="warning")
            return True

        return False

    async def _on_key(self, event: events.Key) -> None:  # noqa: PLR0911
        if await self._handle_voice_key(event):
            return

        self._mark_cursor_moved_if_needed()

        if self.feedback_active:
            if event.character in {"1", "2", "3"}:
                event.prevent_default()
                event.stop()
                self.post_message(self.FeedbackKeyPressed(int(event.character)))
                return
            if event.character is not None:
                self.post_message(self.NonFeedbackKeyPressed())

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
                        self.post_message(self.Submitted(value))
                    return

        if event.key == "enter":
            event.prevent_default()
            event.stop()
            value = self.get_full_text().strip()
            if value:
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

        patch_vscode_space(event)

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
        self._original_text = ""
        self._cursor_pos_after_load = None
        self._cursor_moved_since_load = False
        self._last_text = self.text

    def clear_text(self) -> None:
        self.clear()
        self.reset_history_state()
        self._pending_images = []
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
