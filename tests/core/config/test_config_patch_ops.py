from __future__ import annotations

from typing import Any, get_args

from pydantic import ValidationError
import pytest

from vibe.core.config import (
    AddOperationPatch,
    RemoveOperationPatch,
    ReplaceOperationPatch,
)
from vibe.core.config.patch import (
    ConfigPatch,
    PatchOp,
    ensure_parent_paths,
    escape_json_pointer_token,
    resolve_upsert_op,
)


@pytest.mark.parametrize(
    ("operation", "expected"),
    [
        (
            AddOperationPatch(path="/tools/disabled_tools/0", value="bash"),
            {"op": "add", "path": "/tools/disabled_tools/0", "value": "bash"},
        ),
        (
            ReplaceOperationPatch(path="/active_model", value="devstral-small"),
            {"op": "replace", "path": "/active_model", "value": "devstral-small"},
        ),
        (
            RemoveOperationPatch(path="/tools/deprecated_setting"),
            {"op": "remove", "path": "/tools/deprecated_setting"},
        ),
    ],
)
def test_json_patch_operations_convert_to_json_patch_payload(
    operation: AddOperationPatch | ReplaceOperationPatch | RemoveOperationPatch,
    expected: dict[str, Any],
) -> None:
    assert operation.to_json_patch() == expected


def test_patch_op_union_contains_all_operations() -> None:
    assert set(get_args(PatchOp.__value__)) == {
        AddOperationPatch,
        ReplaceOperationPatch,
        RemoveOperationPatch,
    }


@pytest.mark.parametrize(
    "factory",
    [
        lambda path: AddOperationPatch(path=path, value="value"),
        lambda path: ReplaceOperationPatch(path=path, value="value"),
        lambda path: RemoveOperationPatch(path=path),
    ],
)
def test_json_patch_operations_reject_non_pointer_paths(factory: Any) -> None:
    with pytest.raises(ValidationError, match="valid JSON Pointer"):
        factory("tools.disabled_tools")


@pytest.mark.parametrize(
    "factory",
    [
        lambda path: AddOperationPatch(path=path, value="value"),
        lambda path: ReplaceOperationPatch(path=path, value="value"),
        lambda path: RemoveOperationPatch(path=path),
    ],
)
def test_json_patch_operations_reject_invalid_escapes(factory: Any) -> None:
    with pytest.raises(ValidationError, match="valid JSON Pointer"):
        factory("/tools/~2")


def test_json_patch_operations_accept_slash_prefixed_paths() -> None:
    op = ReplaceOperationPatch(path="/", value={"active_model": "devstral-small"})

    assert op.path == "/"


def test_escape_json_pointer_token_escapes_special_path_characters() -> None:
    assert escape_json_pointer_token("model/with~chars") == "model~1with~0chars"


def test_config_patch_stores_operations_and_metadata() -> None:
    op = ReplaceOperationPatch(path="/active_model", value="devstral-small")
    patch = ConfigPatch(op, fingerprint="fp-1", reason="test")

    assert patch.operations == [op]
    assert patch.fingerprint == "fp-1"
    assert patch.reason == "test"


def test_config_patch_defaults() -> None:
    patch = ConfigPatch(fingerprint="fp-1")

    assert patch.reason == ""
    assert patch.operations == []


def test_config_patch_accepts_multiple_operations() -> None:
    ops = [
        ReplaceOperationPatch(path="/active_model", value="devstral-small"),
        AddOperationPatch(path="/tools/disabled_tools/-", value="bash"),
    ]
    patch = ConfigPatch(*ops, fingerprint="fp-1")

    assert patch.operations == ops


def test_config_patch_add_appends_operations() -> None:
    patch = ConfigPatch(
        ReplaceOperationPatch(path="/active_model", value="devstral-small"),
        fingerprint="fp-1",
    )
    patch.add(RemoveOperationPatch(path="/tools/deprecated_setting"))

    assert patch.operations == [
        ReplaceOperationPatch(path="/active_model", value="devstral-small"),
        RemoveOperationPatch(path="/tools/deprecated_setting"),
    ]


def test_config_patch_add_returns_self() -> None:
    patch = ConfigPatch(fingerprint="fp-1")
    result = patch.add(
        ReplaceOperationPatch(path="/active_model", value="devstral-small")
    )

    assert result is patch


def test_config_patch_add_accepts_multiple_operations() -> None:
    patch = ConfigPatch(fingerprint="fp-1")
    patch.add(
        ReplaceOperationPatch(path="/active_model", value="devstral-small"),
        AddOperationPatch(path="/tools/disabled_tools/-", value="bash"),
    )

    assert len(patch.operations) == 2


def test_config_patch_add_is_chainable() -> None:
    patch = (
        ConfigPatch(fingerprint="fp-1")
        .add(ReplaceOperationPatch(path="/active_model", value="devstral-small"))
        .add(RemoveOperationPatch(path="/tools/deprecated_setting"))
    )

    assert len(patch.operations) == 2


def test_config_patch_to_json_patch_from_wrappers() -> None:
    patch = ConfigPatch(
        ReplaceOperationPatch(path="/active_model", value="devstral-small"),
        AddOperationPatch(path="/tools/disabled_tools/-", value="bash"),
        RemoveOperationPatch(path="/tools/deprecated_setting"),
        fingerprint="fp-1",
    )

    assert patch.to_json_patch() == [
        {"op": "replace", "path": "/active_model", "value": "devstral-small"},
        {"op": "add", "path": "/tools/disabled_tools/-", "value": "bash"},
        {"op": "remove", "path": "/tools/deprecated_setting"},
    ]


def test_config_patch_describe_add_operation() -> None:
    patch = ConfigPatch(
        AddOperationPatch(path="/tools/disabled_tools/-", value="bash"),
        fingerprint="fp-1",
    )

    assert patch.describe() == ["add '/tools/disabled_tools/-' = 'bash'"]


def test_config_patch_describe_replace_operation() -> None:
    patch = ConfigPatch(
        ReplaceOperationPatch(path="/active_model", value="devstral-small"),
        fingerprint="fp-1",
    )

    assert patch.describe() == ["replace '/active_model' = 'devstral-small'"]


def test_config_patch_describe_remove_operation() -> None:
    patch = ConfigPatch(
        RemoveOperationPatch(path="/tools/deprecated_setting"), fingerprint="fp-1"
    )

    assert patch.describe() == ["remove '/tools/deprecated_setting'"]


def test_config_patch_describe_empty_returns_empty_list() -> None:
    patch = ConfigPatch(fingerprint="fp-1")

    assert patch.describe() == []


def test_config_patch_describe_multiple_operations() -> None:
    patch = ConfigPatch(
        ReplaceOperationPatch(path="/active_model", value="devstral-small"),
        AddOperationPatch(path="/tools/disabled_tools/-", value="bash"),
        RemoveOperationPatch(path="/tools/deprecated_setting"),
        fingerprint="fp-1",
    )

    assert patch.describe() == [
        "replace '/active_model' = 'devstral-small'",
        "add '/tools/disabled_tools/-' = 'bash'",
        "remove '/tools/deprecated_setting'",
    ]


def test_ensure_parent_paths_vivifies_missing_dicts() -> None:
    data: dict[str, Any] = {}
    ops = [AddOperationPatch(path="/tools/bash/allowlist", value=["ls"])]

    result = ensure_parent_paths(data, ops)

    assert data == {}
    assert result == {"tools": {"bash": {}}}


def test_ensure_parent_paths_leaves_existing_dicts() -> None:
    data: dict[str, Any] = {"tools": {"bash": {"allowlist": ["ls"]}}}
    ops = [AddOperationPatch(path="/tools/bash/allowlist", value=["cat"])]

    result = ensure_parent_paths(data, ops)

    assert result == {"tools": {"bash": {"allowlist": ["ls"]}}}


def test_ensure_parent_paths_leaves_non_dict_parents() -> None:
    data: dict[str, Any] = {"tools": "not-a-dict"}
    ops = [AddOperationPatch(path="/tools/bash/allowlist", value=["ls"])]

    result = ensure_parent_paths(data, ops)

    assert result == {"tools": "not-a-dict"}


def test_ensure_parent_paths_skips_array_index_tokens() -> None:
    data: dict[str, Any] = {}
    ops = [AddOperationPatch(path="/tools/0/name", value="bash")]

    result = ensure_parent_paths(data, ops)

    assert result == {"tools": {}}


def test_ensure_parent_paths_skips_append_token() -> None:
    data: dict[str, Any] = {}
    ops = [AddOperationPatch(path="/tools/-/name", value="bash")]

    result = ensure_parent_paths(data, ops)

    assert result == {"tools": {}}


def test_ensure_parent_paths_ignores_non_add_ops() -> None:
    data: dict[str, Any] = {}
    ops: list[PatchOp] = [
        ReplaceOperationPatch(path="/tools/bash/allowlist", value=["ls"]),
        RemoveOperationPatch(path="/tools/bash/allowlist"),
    ]

    result = ensure_parent_paths(data, ops)

    assert result == {}


def test_scenario_build_patch_incrementally() -> None:
    patch = ConfigPatch(fingerprint="fp-abc", reason="/model command")
    patch.add(ReplaceOperationPatch(path="/active_model", value="devstral-small"))
    patch.add(AddOperationPatch(path="/tools/disabled_tools/-", value="bash"))

    assert patch.fingerprint == "fp-abc"
    assert patch.reason == "/model command"
    assert len(patch.operations) == 2
    assert patch.describe() == [
        "replace '/active_model' = 'devstral-small'",
        "add '/tools/disabled_tools/-' = 'bash'",
    ]


_PATH = "/providers"
_PAYLOAD = {"name": "mistral", "api_base": "https://api.mistral.ai/v1"}


def test_resolve_upsert_op_empty_list_creates_section() -> None:
    op = resolve_upsert_op([], _PATH, "name", _PAYLOAD)

    assert isinstance(op, AddOperationPatch)
    assert op.path == _PATH
    assert op.value == [_PAYLOAD]


def test_resolve_upsert_op_non_list_existing_creates_section() -> None:
    op = resolve_upsert_op("not-a-list", _PATH, "name", _PAYLOAD)

    assert isinstance(op, AddOperationPatch)
    assert op.path == _PATH
    assert op.value == [_PAYLOAD]


def test_resolve_upsert_op_missing_existing_creates_section() -> None:
    # Mirrors how JsonPointer(...).resolve(raw, default=[]) surfaces a missing path.
    op = resolve_upsert_op([], _PATH, "name", _PAYLOAD)

    assert isinstance(op, AddOperationPatch)
    assert op.value == [_PAYLOAD]


def test_resolve_upsert_op_match_at_index_zero_replaces_in_place() -> None:
    existing = [
        {"name": "mistral", "api_base": "https://old.example/v1"},
        {"name": "custom", "api_base": "https://custom.example/v1"},
    ]

    op = resolve_upsert_op(existing, _PATH, "name", _PAYLOAD)

    assert isinstance(op, ReplaceOperationPatch)
    assert op.path == f"{_PATH}/0"
    assert op.value == _PAYLOAD


def test_resolve_upsert_op_match_at_index_n_replaces_in_place() -> None:
    existing = [
        {"name": "custom", "api_base": "https://custom.example/v1"},
        {"name": "mistral", "api_base": "https://old.example/v1"},
    ]

    op = resolve_upsert_op(existing, _PATH, "name", _PAYLOAD)

    assert isinstance(op, ReplaceOperationPatch)
    assert op.path == f"{_PATH}/1"
    assert op.value == _PAYLOAD


def test_resolve_upsert_op_no_match_appends_to_end() -> None:
    existing = [{"name": "custom", "api_base": "https://custom.example/v1"}]

    op = resolve_upsert_op(existing, _PATH, "name", _PAYLOAD)

    assert isinstance(op, AddOperationPatch)
    assert op.path == f"{_PATH}/-"
    assert op.value == _PAYLOAD


def test_resolve_upsert_op_first_match_wins_with_duplicate_keys() -> None:
    existing = [
        {"name": "mistral", "api_base": "https://first.example/v1"},
        {"name": "mistral", "api_base": "https://second.example/v1"},
    ]

    op = resolve_upsert_op(existing, _PATH, "name", _PAYLOAD)

    assert isinstance(op, ReplaceOperationPatch)
    assert op.path == f"{_PATH}/0"


def test_resolve_upsert_op_skips_non_dict_entries() -> None:
    existing = ["stray", 42, {"name": "mistral", "api_base": "https://old.example/v1"}]

    op = resolve_upsert_op(existing, _PATH, "name", _PAYLOAD)

    assert isinstance(op, ReplaceOperationPatch)
    assert op.path == f"{_PATH}/2"


def test_resolve_upsert_op_forwards_target_layer_name() -> None:
    op = resolve_upsert_op([], _PATH, "name", _PAYLOAD, target_layer_name="user")

    assert isinstance(op, AddOperationPatch)
    assert op.target_layer_name == "user"
