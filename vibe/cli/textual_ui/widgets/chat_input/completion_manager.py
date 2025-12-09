from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from textual import events

from vibe.cli.autocompletion.base import CompletionResult


class CompletionController(Protocol):
    def can_handle(self, text: str, cursor_index: int) -> bool: ...

    def on_text_changed(self, text: str, cursor_index: int) -> None: ...

    def on_key(
        self, event: events.Key, text: str, cursor_index: int
    ) -> CompletionResult: ...

    def reset(self) -> None: ...


class MultiCompletionManager:
    def __init__(self, controllers: Sequence[CompletionController]) -> None:
        self._controllers = list(controllers)
        self._active: CompletionController | None = None

    def on_text_changed(self, text: str, cursor_index: int) -> None:
        candidate = None
        for controller in self._controllers:
            if controller.can_handle(text, cursor_index):
                candidate = controller
                break

        if candidate is None:
            if self._active is not None:
                self._active.reset()
                self._active = None
            return

        if candidate is not self._active:
            if self._active is not None:
                self._active.reset()
            self._active = candidate

        candidate.on_text_changed(text, cursor_index)

    def on_key(
        self, event: events.Key, text: str, cursor_index: int
    ) -> CompletionResult:
        if self._active is None:
            return CompletionResult.IGNORED
        return self._active.on_key(event, text, cursor_index)

    def reset(self) -> None:
        if self._active is not None:
            self._active.reset()
            self._active = None
