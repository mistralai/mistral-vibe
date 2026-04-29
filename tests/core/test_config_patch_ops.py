from __future__ import annotations

from collections.abc import Callable
from dataclasses import FrozenInstanceError
from typing import Any, get_args

import pytest

from vibe.core.config import (
    AppendToList,
    DeleteField,
    PatchOp,
    RemoveFromList,
    SetField,
)


def test_patch_op_union_contains_all_operations() -> None:
    assert get_args(PatchOp) == (SetField, AppendToList, RemoveFromList, DeleteField)


def test_set_field_accepts_top_level_key() -> None:
    op = SetField("active_model", "devstral-small")

    assert op.key == "active_model"
    assert op.value == "devstral-small"


def test_set_field_accepts_nested_key() -> None:
    op = SetField("models.providers", {"mistral": {"region": "eu"}})

    assert op.key == "models.providers"


def test_append_to_list_accepts_nested_key() -> None:
    op = AppendToList("tools.disabled_tools", ("bash", "python"))

    assert op.key == "tools.disabled_tools"
    assert op.items == ("bash", "python")


def test_remove_from_list_accepts_nested_key() -> None:
    op = RemoveFromList("models.available_models", ("codestral-latest",))

    assert op.key == "models.available_models"
    assert op.values == ("codestral-latest",)


def test_delete_field_accepts_nested_key() -> None:
    op = DeleteField("tools.deprecated_setting")

    assert op.key == "tools.deprecated_setting"


@pytest.mark.parametrize(
    "factory",
    [
        lambda key: SetField(key, "value"),
        lambda key: AppendToList(key, ("value",)),
        lambda key: RemoveFromList(key, ("value",)),
        lambda key: DeleteField(key),
    ],
)
@pytest.mark.parametrize(
    "invalid_key", ["", ".active_model", "active_model.", "tools..bash"]
)
def test_patch_operations_reject_invalid_key_paths(
    factory: Callable[[str], object], invalid_key: str
) -> None:
    with pytest.raises(ValueError, match="dot-separated path|must not be empty"):
        factory(invalid_key)


@pytest.mark.parametrize(
    "factory",
    [
        lambda key: SetField(key, "value"),
        lambda key: AppendToList(key, ("value",)),
        lambda key: RemoveFromList(key, ("value",)),
        lambda key: DeleteField(key),
    ],
)
def test_patch_operations_reject_non_string_keys(
    factory: Callable[[Any], object],
) -> None:
    with pytest.raises(TypeError, match="Patch operation key must be a string"):
        factory(1)


def test_append_to_list_rejects_non_tuple_items() -> None:
    bad_items: Any = ["bash"]

    with pytest.raises(TypeError, match="AppendToList.items must be a tuple"):
        AppendToList("tools.disabled_tools", bad_items)


def test_remove_from_list_rejects_non_tuple_values() -> None:
    bad_values: Any = ["bash"]

    with pytest.raises(TypeError, match="RemoveFromList.values must be a tuple"):
        RemoveFromList("tools.disabled_tools", bad_values)


def test_patch_operations_are_frozen() -> None:
    op = SetField("active_model", "devstral-small")

    with pytest.raises(FrozenInstanceError):
        op.__setattr__("key", "models.active_model")


def test_scenario_mini_vibe_patch_operations() -> None:
    operations: list[PatchOp] = [
        SetField("active_model", "devstral-small"),
        AppendToList("tools.disabled_tools", ("bash",)),
        RemoveFromList("models.available_models", ("codestral-latest",)),
        DeleteField("tools.deprecated_setting"),
    ]

    assert operations == [
        SetField("active_model", "devstral-small"),
        AppendToList("tools.disabled_tools", ("bash",)),
        RemoveFromList("models.available_models", ("codestral-latest",)),
        DeleteField("tools.deprecated_setting"),
    ]
