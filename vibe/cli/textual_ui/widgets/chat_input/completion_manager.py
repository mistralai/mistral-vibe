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

    def is_showing(self) -> bool: ...

    def reset(self) -> None: ...


class MultiCompletionManager:
    def __init__(self, controllers: Sequence[CompletionController]) -> None:
        self._controllers = list(controllers)
        self._active_controller: CompletionController | None = None

    def on_text_changed(self, text: str, cursor_index: int) -> None:
        candidate = None
        for controller in self._controllers:
            if controller.can_handle(text, cursor_index):
                candidate = controller
                break

        if candidate is None:
            if self._active_controller is not None:
                self._active_controller.reset()
                self._active_controller = None
            return

        if candidate is not self._active_controller:
            if self._active_controller is not None:
                self._active_controller.reset()
            self._active_controller = candidate

        candidate.on_text_changed(text, cursor_index)

    def on_key(
        self, event: events.Key, text: str, cursor_index: int
    ) -> CompletionResult:
        if self._active_controller is None:
            return CompletionResult.IGNORED
        return self._active_controller.on_key(event, text, cursor_index)

    def has_active_completion(self) -> bool:
        return (
            self._active_controller is not None and self._active_controller.is_showing()
        )

    def dismiss(self) -> bool:
        # Always reset — even when nothing is visible yet, the active controller
        # may hold pending work (e.g. an in-flight path query) that must be
        # cancelled. Report whether a completion was on screen so the caller can
        # decide whether the key was consumed.
        dismissed = self.has_active_completion()
        self.reset()
        return dismissed

    def reset(self) -> None:
        if self._active_controller is not None:
            self._active_controller.reset()
            self._active_controller = None
