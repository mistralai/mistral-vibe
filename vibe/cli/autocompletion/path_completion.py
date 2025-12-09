from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock

from textual import events

from vibe.cli.autocompletion.base import CompletionResult, CompletionView
from vibe.core.autocompletion.completers import PathCompleter

MAX_SUGGESTIONS_COUNT = 10


class PathCompletionController:
    _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="path-completion")

    def __init__(self, completer: PathCompleter, view: CompletionView) -> None:
        self._completer = completer
        self._view = view
        self._suggestions: list[tuple[str, str]] = []
        self._selected_index = 0
        self._pending_future: Future | None = None
        self._last_query: tuple[str, int] | None = None
        self._query_lock = Lock()

    def can_handle(self, text: str, cursor_index: int) -> bool:
        if cursor_index < 0 or cursor_index > len(text):
            return False

        if cursor_index == 0:
            return False

        before_cursor = text[:cursor_index]
        if "@" not in before_cursor:
            return False

        at_index = before_cursor.rfind("@")

        if cursor_index <= at_index:
            return False

        fragment = before_cursor[at_index:cursor_index]
        # fragment must not be empty (including @) and not contain any spaces
        return bool(fragment) and " " not in fragment

    def reset(self) -> None:
        with self._query_lock:
            if self._pending_future and not self._pending_future.done():
                self._pending_future.cancel()
            self._pending_future = None
            self._last_query = None
        if self._suggestions:
            self._suggestions.clear()
            self._selected_index = 0
            self._view.clear_completion_suggestions()

    def on_text_changed(self, text: str, cursor_index: int) -> None:
        if not self.can_handle(text, cursor_index):
            self.reset()
            return

        query = (text, cursor_index)
        with self._query_lock:
            if query == self._last_query:
                return

            if self._pending_future and not self._pending_future.done():
                # NOTE (Vince): this is a "best effort" cancellation: it only works if the task
                # hasn't started; once running in the thread pool, it cannot be cancelled
                self._pending_future.cancel()

            self._last_query = query

        app = getattr(self._view, "app", None)
        if app:
            with self._query_lock:
                self._pending_future = self._executor.submit(
                    self._compute_completions, text, cursor_index
                )
                self._pending_future.add_done_callback(
                    lambda f: self._handle_completion_result(f, query)
                )
        else:
            suggestions = self._compute_completions(text, cursor_index)
            self._update_suggestions(suggestions)

    def _compute_completions(
        self, text: str, cursor_index: int
    ) -> list[tuple[str, str]]:
        return self._completer.get_completion_items(text, cursor_index)

    def _handle_completion_result(self, future: Future, query: tuple[str, int]) -> None:
        if future.cancelled():
            return

        try:
            suggestions = future.result()
            with self._query_lock:
                if query == self._last_query:
                    self._update_suggestions(suggestions)
        except Exception:
            with self._query_lock:
                self._pending_future = None
                self._last_query = None

    def _update_suggestions(self, suggestions: list[tuple[str, str]]) -> None:
        if len(suggestions) > MAX_SUGGESTIONS_COUNT:
            suggestions = suggestions[:MAX_SUGGESTIONS_COUNT]

        app = getattr(self._view, "app", None)

        if suggestions:
            self._suggestions = suggestions
            self._selected_index = 0
            if app:
                app.call_after_refresh(
                    self._view.render_completion_suggestions,
                    self._suggestions,
                    self._selected_index,
                )
            else:
                self._view.render_completion_suggestions(
                    self._suggestions, self._selected_index
                )
        elif app:
            app.call_after_refresh(self.reset)
        else:
            self.reset()

    def on_key(
        self, event: events.Key, text: str, cursor_index: int
    ) -> CompletionResult:
        if not self._suggestions:
            return CompletionResult.IGNORED

        match event.key:
            case "tab" | "enter":
                if self._apply_selected_completion(text, cursor_index):
                    return CompletionResult.HANDLED
                return CompletionResult.IGNORED
            case "down":
                self._move_selection(1)
                return CompletionResult.HANDLED
            case "up":
                self._move_selection(-1)
                return CompletionResult.HANDLED
            case _:
                return CompletionResult.IGNORED

    def _move_selection(self, delta: int) -> None:
        if not self._suggestions:
            return

        count = len(self._suggestions)
        self._selected_index = (self._selected_index + delta) % count
        self._view.render_completion_suggestions(
            self._suggestions, self._selected_index
        )

    def _apply_selected_completion(self, text: str, cursor_index: int) -> bool:
        if not self._suggestions:
            return False

        completion, _ = self._suggestions[self._selected_index]
        replacement_range = self._completer.get_replacement_range(text, cursor_index)
        if replacement_range is None:
            self.reset()
            return False

        start, end = replacement_range
        self._view.replace_completion_range(start, end, completion)
        self.reset()
        return True
