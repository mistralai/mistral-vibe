from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, auto


@dataclass(frozen=True, slots=True)
class ConfigOrigin:
    layer_name: str


class ConflictStrategy(StrEnum):
    CANCEL = auto()  # abort on conflict (default)
    REPLACE = auto()  # force-overwrite, discard external changes


class ConcurrencyConflictError(Exception):
    """Raised when backing store was modified externally between load and apply."""

    def __init__(self, expected_fp: str, actual_fp: str) -> None:
        super().__init__(
            f"Backing store was modified externally (expected fingerprint '{expected_fp}', got '{actual_fp}')"
        )
        self.expected_fp = expected_fp
        self.actual_fp = actual_fp
