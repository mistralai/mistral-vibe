from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, ValidationError
import pytest

from vibe.core.config.builder import ConfigBuilder
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.schema import (
    ConfigFragment,
    ConfigSchema,
    WithConcatMerge,
    WithConflictMerge,
    WithReplaceMerge,
    WithShallowMerge,
    WithUnionMerge,
)
from vibe.core.config.types import LayerConfigSnapshot
from vibe.core.utils.merge import MergeConflictError


class FakeLayer(ConfigLayer[RawConfig]):
    def __init__(self, *, name: str, data: dict[str, Any]) -> None:
        super().__init__(name=name)
        self._data = data

    async def _check_trust(self) -> bool:
        return True

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        return LayerConfigSnapshot(data=dict(self._data), fingerprint="fp")

    async def _save_to_store(self, _next_config: RawConfig) -> str:
        raise NotImplementedError


class UntrustedFakeLayer(FakeLayer):
    async def _check_trust(self) -> bool:
        return False


class InnerFragment(ConfigFragment):
    value: Annotated[str, WithReplaceMerge()] = "default"
    items: Annotated[list[str], WithConcatMerge()] = Field(default_factory=list)


class SampleSchema(ConfigSchema):
    name: Annotated[str, WithReplaceMerge()] = "unnamed"
    tags: Annotated[list[str], WithConcatMerge()] = Field(default_factory=list)
    entries: Annotated[list[dict[str, str]], WithUnionMerge(merge_key="id")] = Field(
        default_factory=list
    )
    inner: InnerFragment = Field(default_factory=InnerFragment)


@pytest.mark.asyncio
async def test_replace_strategy_higher_layer_wins() -> None:
    builder = ConfigBuilder(SampleSchema)
    builder.add_layer(FakeLayer(name="low", data={"name": "low-name"}))
    builder.add_layer(FakeLayer(name="high", data={"name": "high-name"}))
    config = await builder.build()
    assert config.name == "high-name"


@pytest.mark.asyncio
async def test_concat_strategy_appends_lists() -> None:
    builder = ConfigBuilder(SampleSchema)
    builder.add_layer(FakeLayer(name="base", data={"tags": ["a", "b"]}))
    builder.add_layer(FakeLayer(name="extra", data={"tags": ["c"]}))
    config = await builder.build()
    assert config.tags == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_union_strategy_merges_by_key() -> None:
    builder = ConfigBuilder(SampleSchema)
    builder.add_layer(
        FakeLayer(
            name="base",
            data={"entries": [{"id": "1", "v": "old"}, {"id": "2", "v": "keep"}]},
        )
    )
    builder.add_layer(
        FakeLayer(name="override", data={"entries": [{"id": "1", "v": "new"}]})
    )
    config = await builder.build()
    assert config.entries == [{"id": "1", "v": "new"}, {"id": "2", "v": "keep"}]


@pytest.mark.asyncio
async def test_fragment_recursion() -> None:
    builder = ConfigBuilder(SampleSchema)
    builder.add_layer(
        FakeLayer(
            name="layer1", data={"inner": {"value": "from-layer1", "items": ["x"]}}
        )
    )
    builder.add_layer(FakeLayer(name="layer2", data={"inner": {"items": ["y"]}}))
    config = await builder.build()
    assert config.inner.value == "from-layer1"
    assert config.inner.items == ["x", "y"]


@pytest.mark.asyncio
async def test_untrusted_layer_skipped() -> None:
    builder = ConfigBuilder(SampleSchema)
    builder.add_layer(FakeLayer(name="trusted", data={"name": "good"}))
    builder.add_layer(UntrustedFakeLayer(name="untrusted", data={"name": "bad"}))
    config = await builder.build()
    assert config.name == "good"


@pytest.mark.asyncio
async def test_empty_layers_uses_schema_defaults() -> None:
    builder = ConfigBuilder(SampleSchema)
    config = await builder.build()
    assert config.name == "unnamed"
    assert config.tags == []
    assert config.inner.value == "default"


@pytest.mark.asyncio
async def test_single_layer_partial_data() -> None:
    builder = ConfigBuilder(SampleSchema)
    builder.add_layer(FakeLayer(name="partial", data={"name": "custom"}))
    config = await builder.build()
    assert config.name == "custom"
    assert config.tags == []
    assert config.inner.value == "default"


# --- MERGE (shallow dict merge) ---


class MergeSchema(ConfigSchema):
    settings: Annotated[dict[str, Any], WithShallowMerge()] = Field(
        default_factory=dict
    )


@pytest.mark.asyncio
async def test_shallow_merge_combines_dicts() -> None:
    builder = ConfigBuilder(MergeSchema)
    builder.add_layer(FakeLayer(name="base", data={"settings": {"a": 1, "b": 2}}))
    builder.add_layer(FakeLayer(name="override", data={"settings": {"b": 99, "c": 3}}))
    config = await builder.build()
    assert config.settings == {"a": 1, "b": 99, "c": 3}


@pytest.mark.asyncio
async def test_shallow_merge_single_layer() -> None:
    builder = ConfigBuilder(MergeSchema)
    builder.add_layer(FakeLayer(name="only", data={"settings": {"x": 1}}))
    config = await builder.build()
    assert config.settings == {"x": 1}


# --- CONFLICT ---


class ConflictSchema(ConfigSchema):
    unique_id: Annotated[str, WithConflictMerge()] = ""


@pytest.mark.asyncio
async def test_conflict_single_layer_succeeds() -> None:
    builder = ConfigBuilder(ConflictSchema)
    builder.add_layer(FakeLayer(name="only", data={"unique_id": "abc"}))
    config = await builder.build()
    assert config.unique_id == "abc"


@pytest.mark.asyncio
async def test_conflict_two_layers_raises() -> None:
    builder = ConfigBuilder(ConflictSchema)
    builder.add_layer(FakeLayer(name="first", data={"unique_id": "abc"}))
    builder.add_layer(FakeLayer(name="second", data={"unique_id": "def"}))
    with pytest.raises(MergeConflictError):
        await builder.build()


# --- 4-layer merge with mixed strategies ---


class FourLayerSchema(ConfigSchema):
    active_model: Annotated[str, WithReplaceMerge()] = "default"
    tags: Annotated[list[str], WithConcatMerge()] = Field(default_factory=list)
    models: Annotated[list[dict[str, str]], WithUnionMerge(merge_key="alias")] = Field(
        default_factory=list
    )
    settings: Annotated[dict[str, Any], WithShallowMerge()] = Field(
        default_factory=dict
    )


@pytest.mark.asyncio
async def test_four_layers_with_mixed_strategies() -> None:
    builder = ConfigBuilder(FourLayerSchema)
    builder.add_layer(
        FakeLayer(
            name="defaults",
            data={
                "active_model": "model-a",
                "tags": ["core"],
                "models": [{"alias": "m1", "provider": "p1"}],
                "settings": {"timeout": 30},
            },
        )
    )
    builder.add_layer(
        FakeLayer(
            name="user",
            data={
                "active_model": "model-b",
                "tags": ["user"],
                "models": [{"alias": "m2", "provider": "p2"}],
                "settings": {"timeout": 60, "debug": True},
            },
        )
    )
    builder.add_layer(
        FakeLayer(
            name="project",
            data={
                "tags": ["project"],
                "models": [{"alias": "m1", "provider": "p1-override"}],
            },
        )
    )
    builder.add_layer(FakeLayer(name="cli", data={"active_model": "model-d"}))

    config = await builder.build()

    assert config.active_model == "model-d"

    assert config.tags == ["core", "user", "project"]

    assert config.models == [
        {"alias": "m1", "provider": "p1-override"},
        {"alias": "m2", "provider": "p2"},
    ]

    assert config.settings == {"timeout": 60, "debug": True}


# --- Validation errors ---


class StrictSchema(ConfigSchema):
    count: Annotated[int, WithReplaceMerge()]
    name: Annotated[str, WithReplaceMerge()] = "default"


@pytest.mark.asyncio
async def test_validation_error_on_missing_required_field() -> None:
    builder = ConfigBuilder(StrictSchema)
    builder.add_layer(FakeLayer(name="incomplete", data={"name": "hello"}))
    with pytest.raises(ValidationError, match="count"):
        await builder.build()


@pytest.mark.asyncio
async def test_validation_error_on_wrong_type() -> None:
    builder = ConfigBuilder(StrictSchema)
    builder.add_layer(
        FakeLayer(name="bad-type", data={"count": "not-a-number", "name": "hello"})
    )
    with pytest.raises(ValidationError):
        await builder.build()


# --- Fragment defaults ---


@pytest.mark.asyncio
async def test_fragment_defaults_when_no_layer_provides() -> None:
    builder = ConfigBuilder(SampleSchema)
    config = await builder.build()

    assert config.inner.value == "default"
    assert config.inner.items == []


# --- Edge cases ---


@pytest.mark.asyncio
async def test_concat_with_empty_list_from_one_layer() -> None:
    builder = ConfigBuilder(SampleSchema)
    builder.add_layer(FakeLayer(name="empty", data={"tags": []}))
    builder.add_layer(FakeLayer(name="full", data={"tags": ["a"]}))
    config = await builder.build()
    assert config.tags == ["a"]


@pytest.mark.asyncio
async def test_union_with_empty_list_from_one_layer() -> None:
    builder = ConfigBuilder(SampleSchema)
    builder.add_layer(FakeLayer(name="empty", data={"entries": []}))
    builder.add_layer(FakeLayer(name="full", data={"entries": [{"id": "1", "v": "a"}]}))
    config = await builder.build()
    assert config.entries == [{"id": "1", "v": "a"}]


@pytest.mark.asyncio
async def test_all_layers_untrusted_uses_defaults() -> None:
    builder = ConfigBuilder(SampleSchema)
    builder.add_layer(UntrustedFakeLayer(name="u1", data={"name": "bad1"}))
    builder.add_layer(UntrustedFakeLayer(name="u2", data={"name": "bad2"}))
    config = await builder.build()
    assert config.name == "unnamed"


class NullableSchema(ConfigSchema):
    value: Annotated[str | None, WithReplaceMerge()] = "default"


@pytest.mark.asyncio
async def test_replace_none_means_absent_so_base_wins() -> None:
    builder = ConfigBuilder(NullableSchema)
    builder.add_layer(FakeLayer(name="base", data={"value": "hello"}))
    builder.add_layer(FakeLayer(name="nullifier", data={"value": None}))
    config = await builder.build()
    # None is treated as "not provided" by MergeStrategy, so base wins
    assert config.value == "hello"
