from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum, auto
from typing import Any


class MergeConflictError(Exception):
    """Raised when CONFLICT strategy detects two layers both providing a value."""

    def __init__(self, field_name: str = "") -> None:
        msg = (
            f"Merge conflict on field '{field_name}'"
            if field_name
            else "Merge conflict"
        )
        super().__init__(msg)
        self.field_name = field_name


class MergeStrategy(StrEnum):
    REPLACE = auto()
    CONCAT = auto()
    UNION = auto()
    MERGE = auto()
    CONFLICT = auto()

    def apply(
        self, base: Any, override: Any, *, key_fn: Callable[[Any], str] | None = None
    ) -> Any:
        """Combine base and override according to this strategy."""
        if self is MergeStrategy.REPLACE:
            return self._replace(base, override)
        if self is MergeStrategy.CONCAT:
            return self._concat(base, override)
        if self is MergeStrategy.UNION:
            return self._union(base, override, key_fn)
        if self is MergeStrategy.MERGE:
            return self._merge(base, override)
        if self is MergeStrategy.CONFLICT:
            return self._conflict(base, override)
        raise NotImplementedError(f"Merge strategy {self!r} is not implemented")

    def _coalesce(self, base: Any, override: Any) -> tuple[bool, Any]:
        """Return the non-None operand when at most one side is present.

        Returns a (resolved, value) pair:
        - (True, <value>)  — one or both sides are None; use value as-is.
        - (False, None)    — both sides are present; caller must merge them.
        """
        if base is None:
            return True, override
        if override is None:
            return True, base
        return False, None

    def _replace(self, base: Any, override: Any) -> Any:
        resolved, value = self._coalesce(base, override)
        return value if resolved else override

    def _concat(self, base: Any, override: Any) -> Any:
        resolved, value = self._coalesce(base, override)
        if resolved:
            return value
        if not isinstance(base, list) or not isinstance(override, list):
            raise TypeError(
                f"CONCAT requires list operands, got {type(base).__name__} and {type(override).__name__}"
            )
        return base + override

    def _union(
        self, base: Any, override: Any, key_fn: Callable[[Any], str] | None
    ) -> Any:
        resolved, value = self._coalesce(base, override)
        if resolved:
            return value
        if key_fn is None:
            raise ValueError("UNION strategy requires key_fn")
        if not isinstance(base, list) or not isinstance(override, list):
            raise TypeError(
                f"UNION requires list operands, got {type(base).__name__} and {type(override).__name__}"
            )
        merged: dict[str, Any] = {}
        for item in base:
            merged[key_fn(item)] = item
        for item in override:
            merged[key_fn(item)] = item
        return list(merged.values())

    def _merge(self, base: Any, override: Any) -> Any:
        resolved, value = self._coalesce(base, override)
        if resolved:
            return value
        if not isinstance(base, dict) or not isinstance(override, dict):
            raise TypeError(
                f"MERGE requires dict operands, got {type(base).__name__} and {type(override).__name__}"
            )
        return {**base, **override}

    def _conflict(self, base: Any, override: Any) -> Any:
        resolved, value = self._coalesce(base, override)
        if resolved:
            return value
        raise MergeConflictError()
