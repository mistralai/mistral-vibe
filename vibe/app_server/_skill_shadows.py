"""Each shadowed skill is warned about once per process however many sessions resolve
it, so the record is a process service (ADR 0019).
"""

from __future__ import annotations

from collections.abc import Iterable
import threading

from cachetools import LRUCache

from vibe.core.skills.manager import ShadowedSkill
from vibe.observability.logging import logger


class ShadowWarnings:
    def __init__(self) -> None:
        self._warned: LRUCache[ShadowedSkill, None] = LRUCache(maxsize=256)
        self._lock = threading.Lock()

    def warn(self, shadowed: Iterable[ShadowedSkill]) -> None:
        for shadow in shadowed:
            with self._lock:
                if shadow in self._warned:
                    continue
                self._warned[shadow] = None
            logger.warning(shadow.message, *shadow.args)


__all__ = ["ShadowWarnings"]
