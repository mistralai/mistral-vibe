from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints


@dataclass(frozen=True, slots=True)
class ConfigOrigin:
    layer_name: str


class ConflictStrategy(StrEnum):
    CANCEL = auto()  # abort on conflict (default)
    REPLACE = auto()  # force-overwrite, discard external changes


class ConcurrencyConflictError(Exception):
    """Raised when a backing store is modified externally during an optimistic config operation."""

    def __init__(self, expected_fp: str, actual_fp: str) -> None:
        super().__init__(
            f"Backing store was modified externally (expected fingerprint '{expected_fp}', got '{actual_fp}')"
        )
        self.expected_fp = expected_fp
        self.actual_fp = actual_fp


class LayerConfigSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any]
    fingerprint: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


MISSING_BACKING_STORE_DATA_FINGERPRINT = "vibe:missing-backing-store-data"
EMPTY_CONFIG_SNAPSHOT = LayerConfigSnapshot(
    data={}, fingerprint=MISSING_BACKING_STORE_DATA_FINGERPRINT
)


@dataclass(frozen=True, slots=True)
class ConfigChangeEvent:
    """Emitted after a successfully applied change."""

    changed_keys: frozenset[str]
    before: dict[str, Any]
    after: dict[str, Any]
    reason: str


type ConfigChangeCallback = Callable[[ConfigChangeEvent], None]
"""Function signature for a callback that receives a config change event."""
