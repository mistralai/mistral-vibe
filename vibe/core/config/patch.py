from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
import copy
from typing import Annotated, Any

from jsonpointer import JsonPointer, JsonPointerException
from pydantic import AfterValidator, BaseModel, ConfigDict, StringConstraints


class ConfigPatch:
    """A storage-agnostic set of config changes expressed as JSON Patch operations."""

    def __init__(
        self, *operations: PatchOp, fingerprint: str, reason: str = ""
    ) -> None:
        self.operations = list(operations)
        self.fingerprint = fingerprint
        self.reason = reason

    def add(self, *operations: PatchOp) -> ConfigPatch:
        """Append operations after construction."""
        self.operations.extend(operations)
        return self

    def to_json_patch(self) -> list[dict[str, Any]]:
        return [op.to_json_patch() for op in self.operations]

    def describe(self) -> list[str]:
        """Human-readable summary of each operation."""
        return [op.describe() for op in self.operations]


class _OperationPatch(BaseModel, ABC):
    model_config = ConfigDict(frozen=True, extra="forbid")

    @staticmethod
    def _validate_json_pointer(path: str) -> str:
        try:
            JsonPointer(path)
        except JsonPointerException as e:
            raise ValueError("path must be a valid JSON Pointer") from e

        return path

    path: Annotated[str, AfterValidator(_validate_json_pointer)]
    target_layer_name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] | None
    ) = None

    @abstractmethod
    def to_json_patch(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def describe(self) -> str:
        raise NotImplementedError


class AddOperationPatch(_OperationPatch):
    """Add a value at a JSON Pointer path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: Any

    def to_json_patch(self) -> dict[str, Any]:
        return {"op": "add", "path": self.path, "value": self.value}

    def describe(self) -> str:
        return f"add {self.path!r} = {self.value!r}"


class ReplaceOperationPatch(_OperationPatch):
    """Replace the existing value at a JSON Pointer path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: Any

    def to_json_patch(self) -> dict[str, Any]:
        return {"op": "replace", "path": self.path, "value": self.value}

    def describe(self) -> str:
        return f"replace {self.path!r} = {self.value!r}"


class RemoveOperationPatch(_OperationPatch):
    """Remove the existing value at a JSON Pointer path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    def to_json_patch(self) -> dict[str, Any]:
        return {"op": "remove", "path": self.path}

    def describe(self) -> str:
        return f"remove {self.path!r}"


type PatchOp = AddOperationPatch | ReplaceOperationPatch | RemoveOperationPatch


def ensure_parent_paths(
    data: dict[str, Any], operations: Sequence[PatchOp]
) -> dict[str, Any]:
    """Auto-vivify missing intermediate dicts for object-key ``add`` ops.

    Returns a deep copy of *data* with intermediate dicts filled in so a leaf
    like ``/tools/bash/allowlist`` can be set on a config with no ``[tools.bash]``
    table. Array-index and ``-`` append tokens are skipped, and existing non-dict
    parents are left untouched. The input is never mutated.
    """
    data = copy.deepcopy(data)
    for op in operations:
        if not isinstance(op, AddOperationPatch):
            continue
        current: Any = data
        for token in JsonPointer(op.path).parts[:-1]:
            if not isinstance(current, dict) or token == "-" or token.isdigit():
                break
            if token not in current:
                current[token] = {}
            elif not isinstance(current[token], dict):
                break
            current = current[token]
    return data
