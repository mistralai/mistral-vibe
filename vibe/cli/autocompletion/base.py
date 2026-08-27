from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple, Protocol


class CompletionResult(StrEnum):
    IGNORED = "ignored"
    HANDLED = "handled"
    SUBMIT = "submit"


class CompletionEntry(NamedTuple):
    label: str
    description: str


class CompletionView(Protocol):
    def render_completion_suggestions(
        self, suggestions: list[CompletionEntry], selected_index: int
    ) -> None: ...

    def clear_completion_suggestions(self) -> None: ...

    def replace_completion_range(
        self, start: int, end: int, replacement: str, *, suppress_update: bool = False
    ) -> None: ...


class InlineSuggestionView(Protocol):
    def show_inline_suggestion(self, suggestion: str) -> None: ...

    def clear_inline_suggestion(self) -> None: ...

    def replace_completion_range(
        self, start: int, end: int, replacement: str, *, suppress_update: bool = False
    ) -> None: ...
