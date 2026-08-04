from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Annotated

from pydantic import BaseModel, Field
import pytest

from vibe.core.config.schema import (
    DuplicateMergeMetadataError,
    MergeFieldMetadata,
    WithConcatMerge,
    WithConflictMerge,
    WithReplaceMerge,
    WithShallowMerge,
    WithUnionMerge,
)
from vibe.core.utils.merge import MergeConflictError, MergeKeyError, MergeStrategy


class TestMergeFieldMetadata:
    def test_frozen(self) -> None:
        info = WithReplaceMerge()
        with pytest.raises(FrozenInstanceError):
            info.merge_strategy = MergeStrategy.CONCAT  # type: ignore[misc]

    def test_equality(self) -> None:
        assert WithReplaceMerge() == WithReplaceMerge()

    def test_inequality(self) -> None:
        assert WithReplaceMerge() != WithConcatMerge()

    def test_from_field_returns_correct_subclass(self) -> None:
        class _M(BaseModel):
            x: Annotated[str, WithReplaceMerge()] = "a"

        info = MergeFieldMetadata.from_field(_M.model_fields["x"])
        assert isinstance(info, WithReplaceMerge)
        assert info.merge_strategy is MergeStrategy.REPLACE

    def test_from_field_rejects_duplicate_markers(self) -> None:
        class _M(BaseModel):
            x: Annotated[str, WithReplaceMerge(), WithConflictMerge()] = "a"

        with pytest.raises(DuplicateMergeMetadataError):
            MergeFieldMetadata.from_field(_M.model_fields["x"])

    def test_from_field_returns_none_for_plain_field(self) -> None:
        class _M(BaseModel):
            x: str = "a"

        assert MergeFieldMetadata.from_field(_M.model_fields["x"]) is None


class TestWithUnionMerge:
    def test_merge_key_required(self) -> None:
        with pytest.raises(TypeError, match="requires merge_key"):
            WithUnionMerge()

    def test_merge_key_stored(self) -> None:
        marker = WithUnionMerge(merge_key="alias")
        assert marker.merge_key == "alias"
        assert marker.merge_strategy is MergeStrategy.UNION


class _ToolConfig(BaseModel):
    enabled: bool = True
    timeout: int = 30


class _ModelConfig(BaseModel):
    alias: str
    provider: str


class _SampleConfig(BaseModel):
    active_model: Annotated[str, WithReplaceMerge()] = "devstral-2"
    disabled_tools: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list
    )
    models: Annotated[list[_ModelConfig], WithUnionMerge(merge_key="alias")] = Field(
        default_factory=list
    )
    tools: Annotated[dict[str, _ToolConfig], WithShallowMerge()] = Field(
        default_factory=dict
    )
    allowed_hosts: Annotated[list[str] | None, WithConflictMerge()] = None


class TestAnnotatedFieldInModel:
    def test_model_instantiates_with_defaults(self) -> None:
        cfg = _SampleConfig()
        assert cfg.active_model == "devstral-2"
        assert cfg.disabled_tools == []
        assert cfg.models == []
        assert cfg.tools == {}
        assert cfg.allowed_hosts is None

    def test_all_fields_have_merge_info(self) -> None:
        for name, field_info in _SampleConfig.model_fields.items():
            info = MergeFieldMetadata.from_field(field_info)
            assert info is not None, f"Field '{name}' missing MergeFieldMetadata"

    def test_merge_replace_via_field(self) -> None:
        info = MergeFieldMetadata.from_field(_SampleConfig.model_fields["active_model"])
        assert info is not None
        assert (
            info.merge_strategy.apply("devstral-2", "mistral-large") == "mistral-large"
        )

    def test_merge_concat_via_field(self) -> None:
        info = MergeFieldMetadata.from_field(
            _SampleConfig.model_fields["disabled_tools"]
        )
        assert info is not None
        result = info.merge_strategy.apply(["tool_a"], ["tool_b", "tool_c"])
        assert result == ["tool_a", "tool_b", "tool_c"]

    def test_merge_union_via_field(self) -> None:
        info = MergeFieldMetadata.from_field(_SampleConfig.model_fields["models"])
        assert info is not None
        assert info.merge_key == "alias"

        base = [{"alias": "m1", "provider": "p1"}, {"alias": "m2", "provider": "p2"}]
        override = [{"alias": "m2", "provider": "p2-override"}]
        key_fn = lambda item: item[info.merge_key]
        result = info.merge_strategy.apply(base, override, key_fn=key_fn)

        assert len(result) == 2
        assert result[0] == {"alias": "m1", "provider": "p1"}
        assert result[1] == {"alias": "m2", "provider": "p2-override"}

    def test_merge_union_missing_key_raises(self) -> None:
        base = [{"alias": "m1", "provider": "p1"}]
        override = [{"provider": "p2"}]  # missing "alias"
        key_fn = lambda item: item["alias"]
        with pytest.raises(MergeKeyError):
            MergeStrategy.UNION.apply(base, override, key_fn=key_fn)

    def test_merge_merge_via_field(self) -> None:
        info = MergeFieldMetadata.from_field(_SampleConfig.model_fields["tools"])
        assert info is not None

        base = {
            "search": _ToolConfig(enabled=True, timeout=30),
            "browser": _ToolConfig(timeout=60),
        }
        override = {
            "browser": _ToolConfig(enabled=False, timeout=120),
            "code": _ToolConfig(),
        }
        result = info.merge_strategy.apply(base, override)
        assert result == {
            "search": _ToolConfig(enabled=True, timeout=30),
            "browser": _ToolConfig(enabled=False, timeout=120),
            "code": _ToolConfig(),
        }

    def test_merge_conflict_via_field(self) -> None:
        info = MergeFieldMetadata.from_field(
            _SampleConfig.model_fields["allowed_hosts"]
        )
        assert info is not None
        with pytest.raises(MergeConflictError):
            info.merge_strategy.apply(["host1"], ["host2"])

    def test_merge_conflict_single_side_ok(self) -> None:
        info = MergeFieldMetadata.from_field(
            _SampleConfig.model_fields["allowed_hosts"]
        )
        assert info is not None
        assert info.merge_strategy.apply(None, ["host1"]) == ["host1"]
        assert info.merge_strategy.apply(["host1"], None) == ["host1"]

    def test_full_merge_through_model_validate(self) -> None:
        base_layer: dict[str, object] = {
            "active_model": "devstral-2",
            "disabled_tools": ["tool_a"],
            "models": [{"alias": "m1", "provider": "p1"}],
            "tools": {"search": {"enabled": True, "timeout": 30}},
        }
        override_layer: dict[str, object] = {
            "active_model": "mistral-large",
            "disabled_tools": ["tool_b"],
            "models": [
                {"alias": "m1", "provider": "p1-override"},
                {"alias": "m2", "provider": "p2"},
            ],
            "tools": {"code": {"enabled": True, "timeout": 10}},
        }

        merged = {**base_layer}
        for key, override_val in override_layer.items():
            info = MergeFieldMetadata.from_field(_SampleConfig.model_fields[key])
            assert info is not None

            key_fn = None
            if info.merge_key is not None:
                attr = info.merge_key
                key_fn = lambda item, a=attr: item[a]

            merged[key] = info.merge_strategy.apply(
                base_layer.get(key), override_val, key_fn=key_fn
            )

        cfg = _SampleConfig.model_validate(merged)

        assert cfg.active_model == "mistral-large"
        assert cfg.disabled_tools == ["tool_a", "tool_b"]
        assert len(cfg.models) == 2
        assert cfg.models[0].alias == "m1"
        assert cfg.models[0].provider == "p1-override"
        assert cfg.models[1].alias == "m2"
        assert cfg.tools["search"].enabled is True
        assert cfg.tools["code"].timeout == 10
        assert cfg.allowed_hosts is None
