"""Each missing-model notice is logged once per process however many sessions and
derivations repeat it, so the record is a process service (ADR 0019).
"""

from __future__ import annotations

from collections.abc import Iterable
import threading

from cachetools import LRUCache

from vibe.observability.logging import logger


class MissingModelWarnings:
    def __init__(self) -> None:
        self._warned: LRUCache[str, None] = LRUCache(maxsize=256)
        self._lock = threading.Lock()

    def warn(self, messages: Iterable[str]) -> None:
        for message in messages:
            with self._lock:
                if message in self._warned:
                    continue
                self._warned[message] = None
            logger.warning("%s", message)


__all__ = ["MissingModelWarnings"]
