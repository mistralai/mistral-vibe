from __future__ import annotations

from enum import StrEnum, auto


class RecordingMode(StrEnum):
    BUFFER = auto()
    STREAM = auto()
