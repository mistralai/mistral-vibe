from __future__ import annotations

import platform
import re
import time
from typing import Any, ClassVar

from rich.style import Style
from textual import events
from textual._context import NoActiveAppError
from textual.binding import Binding
from textual.message import Message
from textual.widgets import TextArea
from textual.widgets.text_area import Location, Selection, TextAreaTheme

from vibe.cli.autocompletion.base import CompletionResult
from vibe.cli.commands import CommandRegistry
from vibe.cli.constants import CLIPBOARD_IMAGE_PASTE_SUPPORTED_SYSTEM
from vibe.cli.input_modes import DEFAULT_MODE, InputMode
from vibe.cli.textual_ui.external_editor import ExternalEditor
from vibe.cli.textual_ui.widgets.chat_input.completion_manager import (
    MultiCompletionManager,
)
from vibe.cli.textual_ui.widgets.chat_input.paste_path import (
    maybe_prepend_at_for_image_path,
    rewrite_bare_image_paths_in_text,
)
from vibe.cli.textual_ui.widgets.vscode_compat import patch_vscode_space
from vibe.cli.voice_manager.voice_manager_port import (
    RecordingStartError,
    TranscribeState,
    VoiceManagerPort,
)

_WORD = re.compile(r"\w+")
_TRAILING_WORD = re.compile(r"\w+$")
_DOUBLE_CLICK = 2
_TRIPLE_CLICK = 3
_DEFAULT_CLICK_CHAIN_TIME_THRESHOLD = 0.5

FEEDBACK_RATING_KEYS: dict[str, str] = {"1": "good", "2": "fine", "3": "bad"}
FEEDBACK_SNOOZE_KEY = "0"
FEEDBACK_SNOOZE_LABEL = "snooze"


class ChatTextArea(TextArea):
    ALLOW_SELECT: ClassVar[bool] = False

    _CHAT_THEME: ClassVar[TextAreaTheme] = TextAreaTheme(
        name="vibe-chat",
        base_style=None,
        selection_style=Style(reverse=True),
        cursor_line_style=None,
    )

    BINDINGS: ClassVar[list[Binding]] = [
        Binding(
            "shift+enter,ctrl+j",
            "insert_newline",
            "New Line",
            show=False,
            priority=True,
        ),
        Binding("shift+backspace", "delete_left", "Delete character left", show=False),
        Binding("shift+delete", "delete_right", "Delete character right", show=False),
        Binding("ctrl+g", "open_external_editor", "External Editor", show=False),
        Binding("alt+left", "cursor_word_left", "Word Left", show=False, priority=True),
        Binding(
            "alt+right", "cursor_word_right", "Word Right", show=False, priority=True
        ),
        # Ctrl+V triggers an explicit clipboard-image paste on platforms where
        # we support it. On other platforms the binding is not registered, so
        # Textual's default text-paste action handles the key instead and the
        # user never discovers a feature that wouldn't work for them.
        *(
            [
                Binding(
                    "ctrl+v",
                    "paste_image_from_clipboard",
                    "Paste image from clipboard",
                    show=False,
                    priority=True,
                )
            ]
            if platform.system() == CLIPBOARD_IMAGE_PASTE_SUPPORTED_SYSTEM
            else []
        ),
    ]

    DEFAULT_MODE: ClassVar[InputMode] = DEFAULT_MODE

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

    class ClipboardImagePasted(Message):
        """Posted when the OS clipboard should be probed for an image.

        `notify_when_empty` is True for explicit user actions (ctrl+v key
        binding, /paste-image command) so the user gets clear feedback if
        nothing pasteable is on the clipboard. It is False for the implicit
        empty-bracketed-paste trigger, which should stay silent because a
        no-op is the expected outcome of pressing Cmd+V with an empty
        clipboard.
        """

        def __init__(self, *, notify_when_empty: bool = False) -> None:
            self.notify_when_empty = notify_when_empty
            super().__init__()

    def __init__(
        self,
        command_registry: CommandRegistry,
        voice_manager: VoiceManagerPort | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._command_registry = command_registry
        self.register_theme(self._CHAT_THEME)
        self.theme = self._CHAT_THEME.name
        self._input_mode: InputMode = self.DEFAULT_MODE
        self._last_text = ""
        self._navigating_history = False
        self._applying_completion = False
        self._original_text: str = ""
        self._cursor_pos_after_load: tuple[int, int] | None = None
        self._cursor_moved_since_load: bool = False
        self._completion_manager: MultiCompletionManager | None = None
        self._app_has_focus: bool = True
        self._voice_manager = voice_manager
        self._last_keystroke_time: float = 0.0
        self._click_chain: int = 0
        self._chain_consumed: bool = False
        self._last_down_time: float | None = None
        self._last_down_location: Location | None = None
        self._drag_chain: int = 0
        self._drag_anchor: Location | None = None
        self._dragged: bool = False

    # Workaround for an undo crash fixed upstream in
    # https://github.com/Textualize/textual/pull/6687 — remove this override
    # once that fix ships in our pinned Textual version.
    def _recompute_cursor_offset(self) -> None:
        cursor_location = self.clamp_visitable(self.cursor_location)
        self._cursor_offset = self.wrapped_document.location_to_offset(cursor_location)

    def on_blur(self, event: events.Blur) -> None:
        # set_reactive avoids the selection watcher, which would call
        # app.clear_selection() and wipe an in-progress selection elsewhere.
        self.set_reactive(TextArea.selection, Selection.cursor(self.cursor_location))
        self.refresh()
        if self._app_has_focus:
            self.call_after_refresh(self.focus)

    def set_app_focus(self, has_focus: bool) -> None:
        self._app_has_focus = has_focus
        self.cursor_blink = has_focus
        if has_focus and not self.has_focus:
            self.call_after_refresh(self.focus)

    def on_click(self, event: events.Click) -> None:
        self._mark_cursor_moved_if_needed()

    def _select_word_at(self, location: Location) -> None:
        boundary = self._word_boundary_around(location)
        if boundary is not None:
            self._set_selection(Selection(boundary[0], boundary[1]))

    def _set_selection(self, selection: Selection) -> None:
        # set_reactive avoids the selection watcher, which would call
        # app.clear_selection() and wipe an in-progress selection.
        self.set_reactive(TextArea.selection, selection)
        self.refresh()

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 1:
            return
        event.prevent_default()
        target = self.get_target_document_location(event)
        self._set_selection(Selection.cursor(target))
        # A mouse click relocates the caret without firing on_key/on_text_changed.
        # Re-evaluate completions at the new caret so a now-invalid inline ghost
        # clears, while the slash/path popups simply re-render in place.
        if self._completion_manager:
            self._completion_manager.on_text_changed(
                self.get_full_text(), self._get_full_cursor_offset()
            )
        self._selecting = True
        self.capture_mouse()
        self._pause_blink(visible=False)
        self.history.checkpoint()

        self._dragged = False
        self._update_click_chain(target, event.time)
        self._drag_chain = self._click_chain
        if self._click_chain >= _TRIPLE_CLICK:
            self._drag_anchor = target
            row = max(0, min(target[0], self.document.line_count - 1))
            self._set_selection(Selection((row, 0), (row, len(self.document[row]))))
        elif self._click_chain == _DOUBLE_CLICK:
            self._drag_anchor = target
            self._select_word_at(target)
        else:
            self._drag_anchor = None

    async def _on_mouse_move(self, event: events.MouseMove) -> None:
        event.prevent_default()
        if (
            self._selecting
            and self._drag_anchor is not None
            and self._drag_chain in {_DOUBLE_CLICK, _TRIPLE_CLICK}
        ):
            self._expand_drag_selection(event)
        else:
            await super()._on_mouse_move(event)

    async def _on_mouse_up(self, event: events.MouseUp) -> None:
        await super()._on_mouse_up(event)
        if self._dragged:
            self._click_chain = 0
            self._chain_consumed = False
            self._last_down_time = None
            self._last_down_location = None
        self._drag_chain = 0
        self._drag_anchor = None
        self._dragged = False

    def _update_click_chain(self, target: Location, now: float) -> None:
        try:
            threshold = self.app.CLICK_CHAIN_TIME_THRESHOLD
        except NoActiveAppError:
            threshold = _DEFAULT_CLICK_CHAIN_TIME_THRESHOLD
        within = (
            self._last_down_location == target
            and self._last_down_time is not None
            and now - self._last_down_time <= threshold
        )
        if not within:
            self._click_chain = 1
            self._chain_consumed = False
        elif self._chain_consumed:
            # Wrap the chain back to a fresh single click so the cycle
            # (char -> word -> paragraph) loops instead of sticking on CHAR.
            self._click_chain = 1
            self._chain_consumed = False
        else:
            self._click_chain += 1
        if self._click_chain >= _TRIPLE_CLICK:
            self._chain_consumed = True
        self._last_down_time = now
        self._last_down_location = target

    def _expand_drag_selection(self, event: events.MouseMove) -> None:
        anchor = self._drag_anchor
        if anchor is None:
            return
        target = self.get_target_document_location(event)
        if self._drag_chain >= _TRIPLE_CLICK:
            start, end = self._snap_paragraph_drag(anchor, target)
        else:
            start, end = self._snap_word_drag(anchor, target)
        new_selection = Selection(start, end)
        changed = self.selection != new_selection
        self._set_selection(new_selection)
        if changed:
            self._dragged = True

    def _snap_word_drag(
        self, anchor: Location, current: Location
    ) -> tuple[Location, Location]:
        if anchor <= current:
            start_loc, end_loc = anchor, current
        else:
            start_loc, end_loc = current, anchor
        return self._word_start_at(start_loc), self._word_end_at(end_loc)

    def _snap_paragraph_drag(
        self, anchor: Location, current: Location
    ) -> tuple[Location, Location]:
        if anchor <= current:
            start_row, end_row = anchor[0], current[0]
        else:
            start_row, end_row = current[0], anchor[0]
        return self._paragraph_start_at((start_row, 0)), self._paragraph_end_at((
            end_row,
            0,
        ))

    def _word_boundary_around(
        self, location: Location
    ) -> tuple[Location, Location] | None:
        row, col = location
        if not 0 <= row < self.document.line_count:
            return None
        line = self.document[row]
        col = max(0, min(col, len(line)))
        left = _TRAILING_WORD.search(line[:col])
        right = _WORD.match(line[col:])
        left_len = len(left.group()) if left else 0
        right_len = len(right.group()) if right else 0
        if not left_len and not right_len:
            return None
        return (row, col - left_len), (row, col + right_len)

    def _word_start_at(self, location: Location) -> Location:
        boundary = self._word_boundary_around(location)
        return boundary[0] if boundary is not None else location

    def _word_end_at(self, location: Location) -> Location:
        boundary = self._word_boundary_around(location)
        return boundary[1] if boundary is not None else location

    def _paragraph_start_at(self, location: Location) -> Location:
        row = location[0]
        if not 0 <= row < self.document.line_count:
            return location
        return (row, 0)

    def _paragraph_end_at(self, location: Location) -> Location:
        row = location[0]
        if not 0 <= row < self.document.line_count:
            return location
        return (row, len(self.document[row]))

    async def _on_paste(self, event: events.Paste) -> None:
        # Best-effort: terminals that emit bracketed paste sequences will
        # land here, and we can rewrite event.text directly. event.stop()
        # prevents App.on_event from auto-forwarding the Paste back to the
        # focused widget (which would otherwise dispatch this handler a
        # second time and double-insert). TextArea._on_paste in the same
        # MRO still runs inside this dispatch cycle and performs the
        # single insertion using the mutated text.
        event.text = maybe_prepend_at_for_image_path(event.text)
        # Empty paste = either truly empty clipboard, or clipboard holds
        # image bytes the terminal cannot deliver as text. The app handler
        # peeks the OS clipboard in a worker and, if it finds image bytes,
        # writes them to attachments and inserts an @<path> token.
        if not event.text.strip():
            self.post_message(self.ClipboardImagePasted())
        event.stop()

    def action_insert_newline(self) -> None:
        self.insert("\n")

    def action_paste_image_from_clipboard(self) -> None:
        self.post_message(self.ClipboardImagePasted(notify_when_empty=True))

    def action_open_external_editor(self) -> None:
        editor = ExternalEditor()
        current_text = self.get_full_text()

        with self.app.suspend():
            result = editor.edit(current_text)

        if result is not None:
            self.clear()
            self.insert(result)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        # Fallback for terminals that deliver drag-n-drop as a bulk text
        # edit rather than a Paste event (so _on_paste never fires).
        # Scan the full text for any bare absolute image path token and
        # rewrite it to @<path>. Idempotent: tokens already preceded by
        # `@` are skipped, so this is safe to run on every change.
        current = self.text
        rewritten = rewrite_bare_image_paths_in_text(current)
        if rewritten != current:
            self.text = rewritten
            last_line = rewritten.rsplit("\n", 1)[-1]
            self.move_cursor((rewritten.count("\n"), len(last_line)))
            return

        if not self._navigating_history and self.text != self._last_text:
            self._original_text = ""
            self._cursor_pos_after_load = None
            self._cursor_moved_since_load = False
            self.post_message(self.HistoryReset())
        self._last_text = self.text
        was_navigating_history = self._navigating_history
        self._navigating_history = False
        was_applying_completion = self._applying_completion
        self._applying_completion = False

        if (
            self._completion_manager
            and not was_navigating_history
            and not was_applying_completion
        ):
            self._completion_manager.on_text_changed(
                self.get_full_text(), self._get_full_cursor_offset()
            )

    def watch_selection(self, previous: Selection, selection: Selection) -> None:
        # A pure caret move (arrow, word/line nav, home/end, …) leaves the text
        # unchanged, so on_text_area_changed never fires. Re-evaluate completions
        # at the new caret here so a now-invalid inline ghost clears. Edits,
        # completion accepts and history loads change the text (their selection
        # update runs before _last_text is refreshed), so they are skipped and
        # handled by on_text_area_changed instead.
        # getattr: the selection reactive can fire during TextArea.__init__,
        # before this subclass sets _completion_manager / _last_text.
        manager = getattr(self, "_completion_manager", None)
        if manager is None or previous == selection or self.text != self._last_text:
            return
        manager.on_text_changed(self.get_full_text(), self._get_full_cursor_offset())

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
            if (
                self.text
                and self.cursor_location != (0, 0)
                and not history_loaded_and_cursor_unmoved
            ):
                self.move_cursor((0, 0))
            else:
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

    class SnoozeKeyPressed(Message):
        pass

    class NonFeedbackKeyPressed(Message):
        pass

    feedback_active: bool = False

    def replace_voice_manager(self, voice_manager: VoiceManagerPort | None) -> None:
        # The text area holds the manager reference for Ctrl+R and stop/cancel;
        # swap in the real one after the session is ready (cold mount-first path).
        self._voice_manager = voice_manager

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
                self.notify(str(e), severity="warning", markup=False)
            return True

        return False

    def time_since_last_keystroke(self) -> float:
        return time.monotonic() - self._last_keystroke_time

    async def _on_key(self, event: events.Key) -> None:  # noqa: PLR0911
        self._last_keystroke_time = time.monotonic()

        if await self._handle_voice_key(event):
            return

        self._mark_cursor_moved_if_needed()

        if self.feedback_active:
            if event.character in FEEDBACK_RATING_KEYS:
                event.prevent_default()
                event.stop()
                self.post_message(self.FeedbackKeyPressed(int(event.character)))
                return
            if event.character == FEEDBACK_SNOOZE_KEY:
                event.prevent_default()
                event.stop()
                self.post_message(self.SnoozeKeyPressed())
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
                    self.post_message(self.Submitted(self.get_full_text().strip()))
                    return

        if event.key == "enter":
            event.prevent_default()
            event.stop()
            self.post_message(self.Submitted(self.get_full_text().strip()))
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

        if (
            event.key in {"backspace", "shift+backspace"}
            and self._should_reset_mode_on_backspace()
        ):
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

    @property
    def applying_completion(self) -> bool:
        return self._applying_completion

    @applying_completion.setter
    def applying_completion(self, value: bool) -> None:
        self._applying_completion = value

    def set_completion_manager(self, manager: MultiCompletionManager | None) -> None:
        self._completion_manager = manager
        if self._completion_manager:
            self._completion_manager.on_text_changed(
                self.get_full_text(), self._get_full_cursor_offset()
            )

    def set_inline_suggestion(self, suggestion: str) -> None:
        self.suggestion = suggestion

    def clear_inline_suggestion(self) -> None:
        self.suggestion = ""

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
        return 0 if self.is_default_mode else 1

    @property
    def mode_characters(self) -> set[InputMode]:
        chars: set[InputMode] = {"!", "/"}
        if self._command_registry.has_command("teleport"):
            chars.add("&")
        return chars

    @property
    def input_mode(self) -> InputMode:
        return self._input_mode

    @property
    def is_default_mode(self) -> bool:
        return self._input_mode == self.DEFAULT_MODE

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
