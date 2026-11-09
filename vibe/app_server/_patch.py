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


def _model_operations(
    source: dict[str, Any], raw_operation: dict[str, JsonValue]
) -> list[JsonPatchOperation]:
    """Validate a raw ``jsonpatch`` operation, expanding unsupported ops.

    ``jsonpatch``'s diff builder can emit ``move`` and ``copy`` operations, which
    carry a ``from`` pointer and are not part of the wire ``JsonPatchOperation``
    op set. Expand them into the modeled ``add``/``remove`` equivalents before
    validation so a relocated value never crashes the event stream.
    """
    match raw_operation.get("op"):
        case "move":
            from_path = str(raw_operation["from"])
            value = cast(JsonValue, resolve_pointer(source, from_path))
            return [
                JsonPatchOperation(op="remove", path=from_path),
                JsonPatchOperation(
                    op="add", path=str(raw_operation["path"]), value=value
                ),
            ]
        case "copy":
            value = cast(JsonValue, resolve_pointer(source, str(raw_operation["from"])))
            return [
                JsonPatchOperation(
                    op="add", path=str(raw_operation["path"]), value=value
                )
            ]
    return [JsonPatchOperation.model_validate(raw_operation)]


def make_json_patch(
    source: dict[str, Any],
    target: dict[str, Any],
    *,
    append_paths: Collection[str] = (),
) -> list[JsonPatchOperation]:
    operations: list[JsonPatchOperation] = []
    for raw_operation in make_patch(source, target).patch:
        modeled = _model_operations(source, raw_operation)
        if not (
            len(modeled) == 1
            and modeled[0].op == "replace"
            and modeled[0].path in append_paths
        ):
            operations.extend(modeled)
            continue
        previous = resolve_pointer(source, modeled[0].path)
        current = modeled[0].value
        if not (
            isinstance(previous, str)
            and isinstance(current, str)
            and current.startswith(previous)
        ):
            operations.extend(modeled)
            continue
        operations.append(
            JsonPatchOperation(
                op="append", path=modeled[0].path, value=current[len(previous) :]
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
