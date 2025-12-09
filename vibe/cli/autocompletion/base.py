from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class CompletionResult(StrEnum):
    IGNORED = "ignored"
    HANDLED = "handled"
    SUBMIT = "submit"


class CompletionView(Protocol):
    def render_completion_suggestions(
        self, suggestions: list[tuple[str, str]], selected_index: int
    ) -> None: ...

    def clear_completion_suggestions(self) -> None: ...

    def replace_completion_range(
        self, start: int, end: int, replacement: str
    ) -> None: ...
