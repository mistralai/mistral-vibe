from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SetField:
    """Set a top-level or nested field to a value."""

    key: str
    value: Any

    def __post_init__(self) -> None:
        _validate_key_path(self.key)


@dataclass(frozen=True, slots=True)
class AppendToList:
    """Append items to a list field."""

    key: str
    items: tuple[Any, ...]

    def __post_init__(self) -> None:
        _validate_key_path(self.key)
        _validate_tuple_value("AppendToList.items", self.items)


@dataclass(frozen=True, slots=True)
class RemoveFromList:
    """Remove items from a list field by value."""

    key: str
    values: tuple[Any, ...]

    def __post_init__(self) -> None:
        _validate_key_path(self.key)
        _validate_tuple_value("RemoveFromList.values", self.values)


@dataclass(frozen=True, slots=True)
class DeleteField:
    """Remove a field entirely from the config."""

    key: str

    def __post_init__(self) -> None:
        _validate_key_path(self.key)


PatchOp = SetField | AppendToList | RemoveFromList | DeleteField


def _validate_key_path(key: object) -> None:
    if not isinstance(key, str):
        raise TypeError(
            f"Patch operation key must be a string, got {type(key).__name__}"
        )
    if not key:
        raise ValueError("Patch operation key must not be empty")
    if any(not segment for segment in key.split(".")):
        raise ValueError(
            "Patch operation key must be a dot-separated path without empty segments"
        )


def _validate_tuple_value(field_name: str, value: object) -> None:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple, got {type(value).__name__}")
