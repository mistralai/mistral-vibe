from __future__ import annotations

from textual import events

from vibe.cli.autocompletion.base import CompletionResult, CompletionView
from vibe.core.autocompletion.completers import CommandCompleter


class SlashCommandController:
    def __init__(self, completer: CommandCompleter, view: CompletionView) -> None:
        self._completer = completer
        self._view = view
        self._suggestions: list[tuple[str, str]] = []
        self._selected_index = 0

    def can_handle(self, text: str, cursor_index: int) -> bool:
        return text.startswith("/")

    def reset(self) -> None:
        if self._suggestions:
            self._suggestions.clear()
            self._selected_index = 0
            self._view.clear_completion_suggestions()

    def on_text_changed(self, text: str, cursor_index: int) -> None:
        if cursor_index < 0 or cursor_index > len(text):
            self.reset()
            return

        if not self.can_handle(text, cursor_index):
            self.reset()
            return

        suggestions = self._completer.get_completion_items(text, cursor_index)
        if suggestions:
            self._suggestions = suggestions
            self._selected_index = 0
            self._view.render_completion_suggestions(
                self._suggestions, self._selected_index
            )
        else:
            self.reset()

    def on_key(
        self, event: events.Key, text: str, cursor_index: int
    ) -> CompletionResult:
        if not self._suggestions:
            return CompletionResult.IGNORED

        alias, _ = self._suggestions[self._selected_index]
        is_subcommand = " " in alias

        match event.key:
            case "tab":
                result = self._handle_tab(text, cursor_index, is_subcommand)
            case "shift+tab":
                result = self._handle_shift_tab(is_subcommand)
            case "enter":
                result = self._handle_enter(text, cursor_index, alias)
            case "down":
                self._move_selection(1)
                result = CompletionResult.HANDLED
            case "up":
                self._move_selection(-1)
                result = CompletionResult.HANDLED
            case _:
                result = CompletionResult.IGNORED

        return result

    def _handle_tab(
        self, text: str, cursor_index: int, is_subcommand: bool
    ) -> CompletionResult:
        if is_subcommand and len(self._suggestions) > 1:
            self._move_selection(1)
            return CompletionResult.HANDLED
        if self._apply_selected_completion(text, cursor_index):
            return CompletionResult.HANDLED
        return CompletionResult.IGNORED

    def _handle_shift_tab(self, is_subcommand: bool) -> CompletionResult:
        if is_subcommand and len(self._suggestions) > 1:
            self._move_selection(-1)
            return CompletionResult.HANDLED
        return CompletionResult.IGNORED

    def _handle_enter(
        self, text: str, cursor_index: int, alias: str
    ) -> CompletionResult:
        has_deeper = False
        aliases, _ = self._completer._build_lookup()
        prefix = alias + " "
        for a in aliases:
            if a.startswith(prefix):
                has_deeper = True
                break

        if self._apply_selected_completion(text, cursor_index):
            return CompletionResult.HANDLED if has_deeper else CompletionResult.SUBMIT
        return CompletionResult.HANDLED

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

        alias, _ = self._suggestions[self._selected_index]
        replacement_range = self._completer.get_replacement_range(text, cursor_index)
        if replacement_range is None:
            self.reset()
            return False

        start, end = replacement_range
        self._view.replace_completion_range(start, end, alias)

        # Check if there are any deeper completions starting with this alias + " "
        has_deeper = False
        aliases, _ = self._completer._build_lookup()
        prefix = alias + " "
        for a in aliases:
            if a.startswith(prefix):
                has_deeper = True
                break

        if not has_deeper:
            self.reset()
        return True
