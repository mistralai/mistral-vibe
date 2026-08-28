from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from vibe.cli.commands import CommandRegistry
from vibe.cli.history_manager import HistoryManager
from vibe.cli.input_modes import InputMode
from vibe.cli.textual_ui.queue_kinds import QueuedItemKind
from vibe.cli.textual_ui.recording.recording_indicator import RecordingIndicator
from vibe.cli.textual_ui.widgets.chat_input.text_area import ChatTextArea
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.spinner import SpinnerMixin, SpinnerType
from vibe.cli.voice_manager.voice_manager_port import (
    TranscribeState,
    VoiceManagerListener,
    VoiceManagerPort,
)
from vibe.observability.logging import logger

# Queue item kinds that can be edited in-place. Others (e.g. payload-bearing
# commands on a sibling branch) can only be deleted.
_EDITABLE_QUEUE_KINDS: frozenset[QueuedItemKind] = frozenset({
    QueuedItemKind.PROMPT,
    QueuedItemKind.BASH,
})


class _PromptSpinner(SpinnerMixin, Static):
    SPINNER_TYPE: ClassVar[SpinnerType] = SpinnerType.BRAILLE

    def __init__(self) -> None:
        self._indicator_widget: Static | None = None
        self.init_spinner()
        super().__init__(self._spinner.current_frame(), id="prompt-spinner")

    def on_mount(self) -> None:
        self._indicator_widget = self
        self.start_spinner_timer()


class ChatInputBody(VoiceManagerListener, Widget):
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
        """Posted when the user submits an edit but the item was already consumed."""

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
        """Posted when queue selection/edit mode is fully exited."""

    class CompletionResetRequested(Message):
        pass

    class InlineNoticeRequested(Message):
        def __init__(self, message: str, *, timeout: float | None = 4.0) -> None:
            self.message = message
            self.timeout = timeout
            super().__init__()

    class InlineNoticeCleared(Message):
        pass

    def __init__(
        self,
        command_registry: CommandRegistry,
        history_file: Path | None = None,
        voice_manager: VoiceManagerPort | None = None,
        queue_edit_active_getter: Callable[[], bool] | None = None,
        queue_items_getter: Callable[[], list[tuple[int, QueuedItemKind, str]]]
        | None = None,
        queue_selected_index_getter: Callable[[], int | None] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.input_widget: ChatTextArea | None = None
        self.prompt_widget: NoMarkupStatic | None = None
        self._command_registry = command_registry
        self._switching_mode = False
        self._voice_manager = voice_manager
        self._recording_indicator: RecordingIndicator | None = None
        self._queue_edit_active_getter = queue_edit_active_getter
        self._queue_items_getter = queue_items_getter
        self._queue_selected_index_getter = queue_selected_index_getter
        # (queue_index, kind, content), newest-first (reversed from queue order).
        self._queue_items: list[tuple[int, QueuedItemKind, str]] = []
        self._queue_cursor: int = -1
        self._queue_original_text: str = ""
        self._queue_in_edit_mode: bool = False
        self._queue_edit_consumed: bool = False
        # Kind of the item being edited, captured at edit-enter time so it
        # survives the drain consuming the item (the cache then goes stale).
        self._queue_edit_kind: QueuedItemKind | None = None

        if history_file:
            self.history = HistoryManager(history_file)
        else:
            self.history = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            self.prompt_widget = NoMarkupStatic(">", id="prompt")
            yield self.prompt_widget

            self.input_widget = ChatTextArea(
                id="input",
                command_registry=self._command_registry,
                voice_manager=self._voice_manager,
            )
            yield self.input_widget

    def on_mount(self) -> None:
        if self.input_widget:
            self.input_widget.focus()
        if self._voice_manager:
            self._voice_manager.add_listener(self)

    def on_unmount(self) -> None:
        if self._voice_manager:
            self._voice_manager.remove_listener(self)

    def replace_voice_manager(self, voice_manager: VoiceManagerPort | None) -> None:
        # Compose binds the noop voice manager on the cold mount-first path; the
        # real manager arrives later via _initialize_client_dependencies. Re-bind
        # the listener and the text-area's manager reference so Ctrl+R and
        # transcribe callbacks reach the real manager.
        if self._voice_manager is voice_manager:
            return
        if self._voice_manager:
            self._voice_manager.remove_listener(self)
        self._voice_manager = voice_manager
        if voice_manager:
            voice_manager.add_listener(self)
        if self.input_widget:
            self.input_widget.replace_voice_manager(voice_manager)

    def _parse_mode_and_text(self, text: str) -> tuple[InputMode, str]:
        if text.startswith("!"):
            return "!", text[1:]
        elif text.startswith("/"):
            return "/", text[1:]
        elif text.startswith("&") and self._command_registry.has_command("teleport"):
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
        self.input_widget._cursor_pos_after_load = cursor_pos
        self.input_widget._cursor_moved_since_load = False

        self._update_prompt()
        self._notify_completion_reset()

    def on_chat_text_area_history_previous(
        self, _event: ChatTextArea.HistoryPrevious
    ) -> None:
        if not self.input_widget:
            return

        if self._queue_cursor >= 0:
            return

        if self._try_enter_queue_selection():
            return

        if not self.history:
            return

        if not self.history.is_navigating():
            self.input_widget._original_text = self.input_widget.text

        previous = self.history.get_previous(self.input_widget._original_text)

        if previous is not None:
            self._load_history_entry(previous)

    def on_chat_text_area_history_next(self, _event: ChatTextArea.HistoryNext) -> None:
        if not self.input_widget:
            return

        if self._queue_cursor >= 0:
            return

        if not self.history:
            return

        next_entry = self.history.get_next()
        if next_entry is not None:
            self._load_history_entry(next_entry)

    def on_chat_text_area_history_reset(
        self, _event: ChatTextArea.HistoryReset
    ) -> None:
        if self.history:
            self.history.reset_navigation()
        if self.input_widget:
            self.input_widget._original_text = ""
            self.input_widget._cursor_pos_after_load = None
            self.input_widget._cursor_moved_since_load = False

    # -- queue selection + edit state machine -----------------------------

    def _is_queue_edit_available(self) -> bool:
        return (
            self._queue_edit_active_getter is not None
            and self._queue_edit_active_getter()
        )

    def _lock_input_for_selection(self) -> None:
        if not self.input_widget:
            return
        self.input_widget.read_only = True
        self.input_widget.cursor_blink = False
        self.input_widget.show_cursor = False

    def _unlock_input_for_edit(self) -> None:
        if not self.input_widget:
            return
        self.input_widget.read_only = False
        self.input_widget.cursor_blink = True
        self.input_widget.show_cursor = True

    def _resnapshot_queue_items(self) -> list[tuple[int, QueuedItemKind, str]]:
        """Re-read the live queue (newest-first) so cached indices stay valid
        after the queue mutates (delete, or drain consuming an item).
        """
        if self._queue_items_getter is None:
            return []
        return list(reversed(self._queue_items_getter()))

    @property
    def in_queue_mode(self) -> bool:
        """Whether queue selection or edit mode is currently active."""
        return self._queue_cursor >= 0

    def _resync_selection(self) -> bool:
        """Reconcile the cached cursor with the live queue after a possible drain.

        The drain is FIFO and removes the oldest item first, so a highlighted
        item that is still alive shifts to a lower absolute index. We track it
        by widget identity (via ``queue_selected_index_getter``) rather than by
        cached index, which would misread a shift as consumption.

        Returns False (and exits selection mode) if the queue is now empty.
        """
        if self._queue_cursor < 0:
            return True
        live_index = (
            self._queue_selected_index_getter()
            if self._queue_selected_index_getter is not None
            else None
        )
        self._queue_items = self._resnapshot_queue_items()
        if not self._queue_items:
            self._exit_queue_mode()
            return False
        if live_index is None:
            # Highlighted item was consumed: stay on the same relative position
            # (newest-first), clamped — that now points to the next item. Re-post
            # the scroll so the app re-syncs ``_queue_selected_widget`` to the
            # new item; without it a following Enter/Backspace resolves to the
            # removed widget (edit → stray copy-on-write, delete → no-op).
            self._queue_cursor = min(self._queue_cursor, len(self._queue_items) - 1)
            self._post_scroll()
            return True
        for pos, (qi, _, _) in enumerate(self._queue_items):
            if qi == live_index:
                self._queue_cursor = pos
                return True
        self._queue_cursor = min(self._queue_cursor, len(self._queue_items) - 1)
        return True

    def _drop_selected_from_cache(self) -> None:
        """Synchronously remove the highlighted item from the local cache and
        re-number the newer items whose absolute queue index shifted down.

        The app removes the item asynchronously (posted message), so a plain
        re-snapshot here would still see it; mutating the cache keeps indices
        consistent until the app catches up.
        """
        c = self._queue_cursor
        if c < 0 or c >= len(self._queue_items):
            return
        del self._queue_items[c]
        # Newer items (positions 0..c-1 in the newest-first list) had a higher
        # absolute index than the removed one and shift down by 1.
        for pos in range(c):
            qi, kind, content = self._queue_items[pos]
            self._queue_items[pos] = (qi - 1, kind, content)

    def _try_enter_queue_selection(self) -> bool:
        if not self.input_widget or not self._is_queue_edit_available():
            return False
        if self._queue_items_getter is None:
            return False

        self._queue_items = self._resnapshot_queue_items()
        if not self._queue_items:
            return False

        self._queue_cursor = 0
        self._queue_original_text = self.input_widget.text
        self._queue_in_edit_mode = False
        self._queue_edit_consumed = False
        self.input_widget._queue_selection_active = True
        self._lock_input_for_selection()
        self._post_scroll()
        self.post_message(
            self.InlineNoticeRequested(
                "Up/Down: select  ·  Enter: edit  ·  Backspace/Delete: remove  ·  Esc: exit",
                timeout=3.0,
            )
        )
        return True

    def on_chat_text_area_queue_selection_previous(
        self, _event: ChatTextArea.QueueSelectionPrevious
    ) -> None:
        if self._queue_cursor < 0 or self._queue_in_edit_mode:
            return
        if not self._resync_selection():
            return
        if self._queue_cursor < len(self._queue_items) - 1:
            self._queue_cursor += 1
            self._post_scroll()

    def on_chat_text_area_queue_selection_next(
        self, _event: ChatTextArea.QueueSelectionNext
    ) -> None:
        if self._queue_cursor < 0 or self._queue_in_edit_mode:
            return
        if not self._resync_selection():
            return
        if self._queue_cursor > 0:
            self._queue_cursor -= 1
            self._post_scroll()
        else:
            self._exit_queue_mode()

    def on_chat_text_area_queue_selection_enter(
        self, _event: ChatTextArea.QueueSelectionEnter
    ) -> None:
        if self._queue_cursor < 0 or self._queue_in_edit_mode or not self.input_widget:
            return
        if not self._resync_selection():
            return
        _, kind, content = self._queue_items[self._queue_cursor]
        if kind not in _EDITABLE_QUEUE_KINDS:
            self.post_message(
                self.InlineNoticeRequested(
                    "This command can only be deleted — Enter to edit is disabled",
                    timeout=2.5,
                )
            )
            return
        self._queue_in_edit_mode = True
        self._queue_edit_kind = kind
        self.input_widget._queue_selection_active = False
        self.input_widget._queue_edit_active = True
        self._unlock_input_for_edit()
        # Bash content is stored without the leading "!" — restore it so the
        # edit happens in bash mode and copy-on-write preserves the kind.
        load_text = f"!{content}" if kind == QueuedItemKind.BASH else content
        self._load_history_entry(load_text)
        self.post_message(
            self.InlineNoticeRequested("Enter to save · Esc to discard", timeout=None)
        )

    def on_chat_text_area_queue_selection_remove(
        self, _event: ChatTextArea.QueueSelectionRemove
    ) -> None:
        if self._queue_cursor < 0 or self._queue_in_edit_mode:
            return
        if not self._resync_selection():
            return
        self.post_message(self.QueueRemoveRequested())

        # Synchronously drop the item from the local cache and re-number the
        # newer items whose absolute index shifted down. A live re-snapshot
        # here would still see the item (the app removes it asynchronously),
        # and a bare ``del`` would leave the shifted indices stale (F1).
        self._drop_selected_from_cache()
        if not self._queue_items:
            self._exit_queue_mode()
            return

        self._queue_cursor = max(0, self._queue_cursor - 1)
        self._post_scroll()

    def on_chat_text_area_queue_selection_exit(
        self, _event: ChatTextArea.QueueSelectionExit
    ) -> None:
        if self._queue_cursor < 0:
            return
        self._exit_queue_mode()

    def on_chat_text_area_queue_edit_cancelled(
        self, _event: ChatTextArea.QueueEditCancelled
    ) -> None:
        if self._queue_cursor < 0 or not self._queue_in_edit_mode:
            return
        self._queue_in_edit_mode = False
        self._queue_edit_consumed = False
        self._queue_edit_kind = None
        if self.input_widget:
            self.input_widget._queue_edit_active = False
            self.input_widget._queue_selection_active = True
            self.input_widget.clear_text()
            self._update_prompt()
        self._lock_input_for_selection()
        self._post_scroll()
        self.post_message(self.InlineNoticeCleared())

    def _exit_queue_mode(self) -> None:
        was_in_edit = self._queue_in_edit_mode
        self._queue_cursor = -1
        self._queue_items = []
        self._queue_in_edit_mode = False
        self._queue_edit_consumed = False
        self._queue_edit_kind = None
        if self.input_widget:
            self.input_widget._queue_selection_active = False
            self.input_widget._queue_edit_active = False
            self.input_widget.load_text(self._queue_original_text)
            self._update_prompt()
        if was_in_edit:
            if self.input_widget:
                self.input_widget.clear_text()
                self._update_prompt()
            self.post_message(self.InlineNoticeCleared())
        self._queue_original_text = ""
        if self.input_widget:
            self._unlock_input_for_edit()
        self.post_message(self.QueueModeExited())

    def _post_scroll(self) -> None:
        if self._queue_cursor < 0 or not self._queue_items:
            return
        queue_index = self._queue_items[self._queue_cursor][0]
        self.post_message(self.QueueSelectionScroll(queue_index))

    def _end_edit_mode_back_to_selection(self) -> None:
        self._queue_in_edit_mode = False
        self._queue_edit_consumed = False
        if self.input_widget:
            self.input_widget._queue_edit_active = False
            self.input_widget._queue_selection_active = True
            self.input_widget.clear_text()
            self._update_prompt()
        self._notify_completion_reset()
        self._lock_input_for_selection()
        self._post_scroll()
        self.post_message(self.InlineNoticeCleared())

    def on_chat_text_area_submitted(self, event: ChatTextArea.Submitted) -> None:
        event.stop()

        if self._switching_mode:
            return

        if not self.input_widget:
            return

        value = event.value.strip()
        if value and self._queue_in_edit_mode:
            kind = self._queue_edit_kind or QueuedItemKind.PROMPT
            if self._queue_edit_consumed:
                self.post_message(self.InlineNoticeCleared())
                self._queue_items = self._resnapshot_queue_items()
                if not self._queue_items:
                    self._exit_queue_mode()
                    self.input_widget.clear_text()
                    self._update_prompt()
                    self._notify_completion_reset()
                    self.post_message(self.QueueEditConsumed(value, kind))
                    return
                if self._queue_cursor >= len(self._queue_items):
                    self._queue_cursor = len(self._queue_items) - 1
                self._end_edit_mode_back_to_selection()
                self.post_message(self.QueueEditConsumed(value, kind))
                return
            # The drain may have consumed the item being edited while the user
            # was typing. Detected by widget identity, since the cached index
            # is unreliable once older items shift on FIFO consumption. Route
            # to the copy-on-write flow: keep the edit, let the user re-Enter to
            # submit as new or Escape to discard.
            if (
                self._queue_selected_index_getter is not None
                and self._queue_selected_index_getter() is None
            ):
                self._queue_edit_consumed = True
                self.post_message(
                    self.InlineNoticeRequested(
                        "This message was already processed — "
                        "press Enter to submit as new, or Escape to discard.",
                        timeout=8.0,
                    )
                )
                return
            self._end_edit_mode_back_to_selection()
            self.post_message(self.QueueEditSubmitted(value, kind))
            return

        if value:
            if self.history:
                self.history.add(value)
                self.history.reset_navigation()
                self.run_worker(
                    partial(self.history.persist, value),
                    group="history_persist",
                    thread=True,
                )

            self.input_widget.clear_text()
            self._update_prompt()

            self._notify_completion_reset()

        self.post_message(self.Submitted(value))

    @property
    def switching_mode(self) -> bool:
        return self._switching_mode

    @switching_mode.setter
    def switching_mode(self, value: bool) -> None:
        self.set_switching_mode(value)

    def set_switching_mode(
        self, value: bool, *, show_indicator: bool | None = None
    ) -> None:
        self._switching_mode = value
        self._set_switching_indicator(
            value if show_indicator is None else show_indicator
        )

    def _set_switching_indicator(self, visible: bool) -> None:
        if visible:
            if self.prompt_widget:
                self.prompt_widget.display = False
            if not self.query(_PromptSpinner):
                self.query_one(Horizontal).mount(_PromptSpinner(), before=0)
        else:
            for spinner in self.query(_PromptSpinner):
                spinner.remove()
            if self.prompt_widget:
                self.prompt_widget.display = True
                self._update_prompt()

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

    def _notify_completion_reset(self) -> None:
        self.post_message(self.CompletionResetRequested())

    def replace_input(self, text: str, cursor_offset: int | None = None) -> None:
        if not self.input_widget:
            return

        self.input_widget.load_text(text)
        self.input_widget.reset_history_state()
        self._update_prompt()

        if cursor_offset is not None:
            self.input_widget.set_cursor_offset(max(0, min(cursor_offset, len(text))))

    def on_transcribe_state_change(self, state: TranscribeState) -> None:
        if state == TranscribeState.RECORDING:
            self._start_recording_ui()
        elif state == TranscribeState.IDLE:
            self._stop_recording_ui()

    def on_transcribe_text(self, text: str) -> None:
        if not self.input_widget:
            return
        self.input_widget.insert(text)

    def on_transcribe_error(self, message: str) -> None:
        self._reset_recording_ui()
        self.notify(
            f"Voice transcription failed: {message}", severity="error", markup=False
        )

    def on_transcribe_notice(self, message: str) -> None:
        self.post_message(self.InlineNoticeRequested(message, timeout=2.0))

    def _start_recording_ui(self) -> None:
        if not self._voice_manager:
            return
        # Don't stack a second indicator if one is already showing (VIBE-3435).
        if self._recording_indicator is not None:
            return

        try:
            self.screen.get_widget_by_id("input-box").add_class("border-recording")

            if self.input_widget:
                self.input_widget.cursor_blink = False
                self.input_widget.add_class("recording")
            if self.prompt_widget:
                self.prompt_widget.display = False
            self._recording_indicator = RecordingIndicator(self._voice_manager)
            self.query_one(Horizontal).mount(self._recording_indicator, before=0)
        except Exception as e:
            logger.error("Failed to start recording UI", exc_info=e)
            self._reset_recording_ui()

    def _stop_recording_ui(self) -> None:
        try:
            self.screen.get_widget_by_id("input-box").remove_class("border-recording")

            if self.input_widget:
                self.input_widget.cursor_blink = True
                self.input_widget.remove_class("recording")
            if self.prompt_widget:
                self.prompt_widget.display = True
                self._update_prompt()
            if self._recording_indicator:
                self._recording_indicator.remove()
                self._recording_indicator = None
        except Exception as e:
            logger.error("Failed to stop recording UI", exc_info=e)
            self._reset_recording_ui()

    def _reset_recording_ui(self) -> None:
        try:
            self.screen.get_widget_by_id("input-box").remove_class("border-recording")
        except Exception:
            pass

        if self.input_widget:
            self.input_widget.cursor_blink = True
            self.input_widget.remove_class("recording")
        if self.prompt_widget:
            self.prompt_widget.display = True
            self._update_prompt()
        if self._recording_indicator:
            try:
                self._recording_indicator.remove()
            except Exception:
                pass
            self._recording_indicator = None
