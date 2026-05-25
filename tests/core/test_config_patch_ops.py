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
from vibe.core.config.patch import ConfigPatch


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


# --- ConfigPatch ---


def test_config_patch_stores_operations_and_metadata() -> None:
    op = SetField("active_model", "devstral-small")
    patch = ConfigPatch(op, fingerprint="fp-1", reason="test")

    assert patch.operations == [op]
    assert patch.fingerprint == "fp-1"
    assert patch.reason == "test"


def test_config_patch_defaults() -> None:
    patch = ConfigPatch(fingerprint="fp-1")

    assert patch.reason == ""
    assert patch.operations == []


def test_config_patch_accepts_multiple_operations() -> None:
    ops: list[PatchOp] = [
        SetField("active_model", "devstral-small"),
        AppendToList("tools.disabled_tools", ("bash",)),
    ]
    patch = ConfigPatch(*ops, fingerprint="fp-1")

    assert patch.operations == ops


def test_config_patch_add_appends_operations() -> None:
    patch = ConfigPatch(SetField("active_model", "devstral-small"), fingerprint="fp-1")
    patch.add(DeleteField("tools.deprecated_setting"))

    assert patch.operations == [
        SetField("active_model", "devstral-small"),
        DeleteField("tools.deprecated_setting"),
    ]


def test_config_patch_add_returns_self() -> None:
    patch = ConfigPatch(fingerprint="fp-1")
    result = patch.add(SetField("active_model", "devstral-small"))

    assert result is patch


def test_config_patch_add_accepts_multiple_operations() -> None:
    patch = ConfigPatch(fingerprint="fp-1")
    patch.add(
        SetField("active_model", "devstral-small"),
        AppendToList("tools.disabled_tools", ("bash",)),
    )

    assert len(patch.operations) == 2


def test_config_patch_add_is_chainable() -> None:
    patch = (
        ConfigPatch(fingerprint="fp-1")
        .add(SetField("active_model", "devstral-small"))
        .add(DeleteField("tools.deprecated_setting"))
    )

    assert len(patch.operations) == 2


def test_config_patch_describe_set_field() -> None:
    patch = ConfigPatch(SetField("active_model", "devstral-small"), fingerprint="fp-1")

    assert patch.describe() == ["set 'active_model' = 'devstral-small'"]


def test_config_patch_describe_append_to_list() -> None:
    patch = ConfigPatch(
        AppendToList("tools.disabled_tools", ("bash", "python")), fingerprint="fp-1"
    )

    assert patch.describe() == ["append to 'tools.disabled_tools': ['bash', 'python']"]


def test_config_patch_describe_remove_from_list() -> None:
    patch = ConfigPatch(
        RemoveFromList("models.available_models", ("codestral-latest",)),
        fingerprint="fp-1",
    )

    assert patch.describe() == [
        "remove from 'models.available_models': ['codestral-latest']"
    ]


def test_config_patch_describe_delete_field() -> None:
    patch = ConfigPatch(DeleteField("tools.deprecated_setting"), fingerprint="fp-1")

    assert patch.describe() == ["delete 'tools.deprecated_setting'"]


def test_config_patch_describe_empty_returns_empty_list() -> None:
    patch = ConfigPatch(fingerprint="fp-1")

    assert patch.describe() == []


def test_config_patch_describe_multiple_operations() -> None:
    patch = ConfigPatch(
        SetField("active_model", "devstral-small"),
        AppendToList("tools.disabled_tools", ("bash",)),
        DeleteField("tools.deprecated_setting"),
        fingerprint="fp-1",
    )

    assert patch.describe() == [
        "set 'active_model' = 'devstral-small'",
        "append to 'tools.disabled_tools': ['bash']",
        "delete 'tools.deprecated_setting'",
    ]


def test_scenario_build_patch_incrementally() -> None:
    patch = ConfigPatch(fingerprint="fp-abc", reason="/model command")
    patch.add(SetField("active_model", "devstral-small"))
    patch.add(AppendToList("tools.disabled_tools", ("bash",)))

    assert patch.fingerprint == "fp-abc"
    assert patch.reason == "/model command"
    assert len(patch.operations) == 2
    assert patch.describe() == [
        "set 'active_model' = 'devstral-small'",
        "append to 'tools.disabled_tools': ['bash']",
    ]
