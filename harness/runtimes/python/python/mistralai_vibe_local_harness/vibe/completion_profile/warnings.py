# Twin: mistralai.vibe.harness.runtime.completion.profile.warnings. Keep both copies identical below this line.
"""The WarningSink: where profile and compaction warnings go in every host.

The sink logs each distinct warning once and hands it to its listeners (a toast in
one host, a notice in another). A host keeps one sink per session, so a warning is
shown once per session however often it is raised.
"""

from collections.abc import Callable
from typing import Protocol

from .types import CompletionWarning, WarningCode

type WarningListener = Callable[[CompletionWarning], None]

# A model without image input gets `[Image]` in place of each image it cannot
# open; the host tells its user once per message, switch, or reopen that does so.
IMAGES_REPLACED = CompletionWarning(
    WarningCode.IMAGES_REPLACED,
    "input_modalities",
    "This model can't take images, so it gets [Image] in place of each one.",
)


class WarningSink(Protocol):
    """Receives warnings as data; never raises for them."""

    def emit(self, warning: CompletionWarning) -> None:
        """Deliver one warning."""
        ...


class RuntimeWarningSink:
    """Logs each distinct warning once and forwards it to every listener."""

    def __init__(self, log: WarningListener) -> None:
        self._log = log
        self._listeners: list[WarningListener] = []
        self._seen: set[CompletionWarning] = set()

    def subscribe(self, listener: WarningListener) -> Callable[[], None]:
        """Add a listener; the returned callable removes it."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def emit(self, warning: CompletionWarning) -> None:
        """Deliver a warning unless this sink already delivered the same one."""
        if warning in self._seen:
            return
        self._seen.add(warning)
        self._log(warning)
        for listener in tuple(self._listeners):
            listener(warning)

    def emit_all(self, warnings: tuple[CompletionWarning, ...]) -> None:
        """Deliver warnings in order."""
        for warning in warnings:
            self.emit(warning)
