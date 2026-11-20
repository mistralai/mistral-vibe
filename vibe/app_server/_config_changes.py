"""Changes to the config a session opened now would run, told process-wide.

A session follows its own config, and tells its client with
``runtime/updated``. A settings screen with no session open has nothing to
follow: the org config and the model catalog the process holds are refreshed
in the background, and another connection may write a setting. Each connection that subscribed
(``config/subscribe``) is told here, and re-reads what it shows.
"""

from __future__ import annotations

from collections.abc import Callable

from vibe.observability.logging import logger


class ConfigChanges:
    def __init__(self) -> None:
        self._listeners: list[Callable[[], None]] = []

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` on every change; the returned function stops it."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def publish(self) -> None:
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                logger.debug("Config change listener failed", exc_info=True)


__all__ = ["ConfigChanges"]
