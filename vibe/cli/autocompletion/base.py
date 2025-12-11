from __future__ import annotations

from typing import Protocol

from vibe.core.compatibility import StrEnum


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
