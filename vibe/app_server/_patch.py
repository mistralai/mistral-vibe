from __future__ import annotations

from collections.abc import Collection
from copy import deepcopy
from typing import Any, cast

from jsonpatch import apply_patch, make_patch
from jsonpointer import resolve_pointer
from pydantic import JsonValue

from vibe.app_server.models import JsonPatchOperation


def apply_json_patch(
    value: JsonValue, operations: list[JsonPatchOperation]
) -> JsonValue:
    document: JsonValue = deepcopy(value)
    for operation in operations:
        document = cast(
            JsonValue,
            apply_patch(
                document, [_standard_operation(document, operation)], in_place=True
            ),
        )
    return document


def make_json_patch(
    source: dict[str, Any],
    target: dict[str, Any],
    *,
    append_paths: Collection[str] = (),
) -> list[JsonPatchOperation]:
    operations: list[JsonPatchOperation] = []
    for raw_operation in make_patch(source, target).patch:
        operation = JsonPatchOperation.model_validate(raw_operation)
        if operation.op != "replace" or operation.path not in append_paths:
            operations.append(operation)
            continue
        previous = resolve_pointer(source, operation.path)
        current = operation.value
        if not (
            isinstance(previous, str)
            and isinstance(current, str)
            and current.startswith(previous)
        ):
            operations.append(operation)
            continue
        operations.append(
            JsonPatchOperation(
                op="append", path=operation.path, value=current[len(previous) :]
            )
        )
    return operations


def _standard_operation(
    document: JsonValue, operation: JsonPatchOperation
) -> dict[str, JsonValue]:
    match operation.op:
        case "append":
            current = resolve_pointer(document, operation.path)
            if not isinstance(current, str) or not isinstance(operation.value, str):
                raise ValueError("Append patches require string values")
            return {
                "op": "replace",
                "path": operation.path,
                "value": current + operation.value,
            }
        case "remove":
            return {"op": operation.op, "path": operation.path}
        case "add" | "replace" | "test":
            return {
                "op": operation.op,
                "path": operation.path,
                "value": operation.value,
            }
