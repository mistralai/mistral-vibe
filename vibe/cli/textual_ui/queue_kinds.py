from __future__ import annotations

from enum import StrEnum, auto


class QueuedItemKind(StrEnum):
    PROMPT = auto()
    BASH = auto()
    COMMAND = auto()
