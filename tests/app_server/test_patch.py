from __future__ import annotations

from typing import Literal

from jsonpatch import JsonPatchConflict
from jsonpointer import JsonPointerException
from pydantic import JsonValue
import pytest

from vibe.app_server._patch import apply_json_patch
from vibe.app_server.models import JsonPatchOperation


def test_standard_operations_apply_sequentially_without_mutating_input() -> None:
    original = {"items": ["first"], "status": "pending"}

    patched = apply_json_patch(
        original,
        [
            JsonPatchOperation(op="add", path="/items/-", value="second"),
            JsonPatchOperation(op="replace", path="/status", value="completed"),
            JsonPatchOperation(op="remove", path="/items/0"),
        ],
    )

    assert patched == {"items": ["second"], "status": "completed"}
    assert original == {"items": ["first"], "status": "pending"}


@pytest.mark.parametrize("operation", ["replace", "remove"])
def test_standard_operations_reject_missing_paths(
    operation: Literal["replace", "remove"],
) -> None:
    with pytest.raises(JsonPatchConflict):
        apply_json_patch(
            {"present": True},
            [JsonPatchOperation(op=operation, path="/missing", value=None)],
        )


def test_standard_operations_reject_invalid_json_pointers() -> None:
    with pytest.raises(JsonPointerException):
        apply_json_patch(
            {"value": "before"},
            [JsonPatchOperation(op="replace", path="value", value="after")],
        )


def test_append_updates_an_existing_string_after_prior_operations() -> None:
    patched = apply_json_patch(
        {"message": "ignored"},
        [
            JsonPatchOperation(op="replace", path="/message", value="hell"),
            JsonPatchOperation(op="append", path="/message", value="o"),
        ],
    )

    assert patched == {"message": "hello"}


def test_append_supports_escaped_json_pointer_paths() -> None:
    patched = apply_json_patch(
        {"a/b": {"~text": "before"}},
        [JsonPatchOperation(op="append", path="/a~1b/~0text", value=" after")],
    )

    assert patched == {"a/b": {"~text": "before after"}}


def test_append_rejects_a_missing_path() -> None:
    with pytest.raises(JsonPointerException):
        apply_json_patch(
            {}, [JsonPatchOperation(op="append", path="/missing", value="text")]
        )


@pytest.mark.parametrize(
    ("document", "value"), [({"target": 1}, "text"), ({"target": "text"}, 1)]
)
def test_append_requires_string_target_and_value(
    document: JsonValue, value: JsonValue
) -> None:
    with pytest.raises(ValueError, match="Append patches require string values"):
        apply_json_patch(
            document, [JsonPatchOperation(op="append", path="/target", value=value)]
        )
