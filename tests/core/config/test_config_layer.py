from __future__ import annotations

import asyncio
from pathlib import Path
import tomllib
from typing import Annotated, Any

from jsonpatch import JsonPatchException
from pydantic import BaseModel, Field, ValidationError
import pytest

from vibe.core.config.layer import (
    ConfigLayer,
    ConfigPatchApplicationError,
    LayerImplementationError,
    RawConfig,
    UntrustedLayerError,
)
from vibe.core.config.layers.agent_profile import AgentProfileLayer
from vibe.core.config.layers.default import DefaultConfigLayer
from vibe.core.config.layers.discovered import DiscoveredConfigLayer
from vibe.core.config.patch import (
    AddOperationPatch,
    ConfigPatch,
    RemoveOperationPatch,
    ReplaceOperationPatch,
)
from vibe.core.config.schema import ConfigSchema, WithConcatMerge, WithReplaceMerge
from vibe.core.config.types import (
    ConcurrencyConflictError,
    ConflictStrategy,
    LayerConfigSnapshot,
)


class StubLayer(ConfigLayer[BaseModel]):
    """Minimal concrete layer for testing."""

    def __init__(
        self,
        *,
        name: str = "stub",
        output_schema: type[BaseModel] | None = None,
        trusted: bool = True,
        data: dict[str, Any] | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"name": name}
        if output_schema is not None:
            kwargs["output_schema"] = output_schema
        super().__init__(**kwargs)
        self._stub_trusted = trusted
        self._data = data or {}
        self.read_count = 0

    async def _check_trust(self) -> bool:
        return self._stub_trusted

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        self.read_count += 1
        return LayerConfigSnapshot(
            data=dict(self._data), fingerprint=f"fp-{self.read_count}"
        )

    async def _save_to_store(self, _next_config: BaseModel) -> str:
        raise NotImplementedError("StubLayer.apply() is not implemented")


class WritableStubLayer(StubLayer):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.writes: list[dict[str, Any]] = []

    async def _save_to_store(self, _next_config: BaseModel) -> str:
        next_data = _next_config.model_dump()
        self.writes.append(next_data)
        self._data = next_data
        return f"write-fp-{len(self.writes)}"


class SampleSchema(BaseModel):
    name: str
    count: int = 0


class DefaultLayerSchema(ConfigSchema):
    name: Annotated[str, WithReplaceMerge()] = "default-name"
    items: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=lambda: ["default-item"]
    )


def test_abstract_build_config_snapshot_enforced() -> None:
    class IncompleteLayer(ConfigLayer[BaseModel]):
        pass

    with pytest.raises(TypeError):
        IncompleteLayer(name="incomplete")  # type: ignore[abstract]


def test_repr() -> None:
    layer = StubLayer(name="my-layer")
    assert repr(layer) == "StubLayer(name='my-layer')"


def test_layer_config_snapshot_strips_fingerprint() -> None:
    snapshot = LayerConfigSnapshot(data={}, fingerprint=" fp ")
    assert snapshot.fingerprint == "fp"


def test_layer_config_snapshot_rejects_empty_fingerprint() -> None:
    with pytest.raises(ValidationError):
        LayerConfigSnapshot(data={}, fingerprint=" ")


@pytest.mark.asyncio
async def test_default_config_layer_loads_schema_defaults() -> None:
    layer = DefaultConfigLayer(schema=DefaultLayerSchema)

    result = await layer.load()

    assert result.model_dump() == {"name": "default-name", "items": ["default-item"]}


@pytest.mark.asyncio
async def test_default_config_layer_is_read_only() -> None:
    layer = DefaultConfigLayer(schema=DefaultLayerSchema)
    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)

    with pytest.raises(NotImplementedError, match="read-only"):
        await layer.apply(
            ConfigPatch(
                ReplaceOperationPatch(path="/name", value="updated"),
                fingerprint=fingerprint,
            )
        )


@pytest.mark.asyncio
async def test_discovered_config_layer_persists_to_memory() -> None:
    layer = DiscoveredConfigLayer(data={"items": ["default"]})
    initial = await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)
    assert initial.model_extra is not None

    initial.model_extra["items"].append("mutated")
    await layer.apply(
        ConfigPatch(
            AddOperationPatch(path="/items/-", value="discovered"),
            fingerprint=fingerprint,
        )
    )

    assert (await layer.load(force=True)).model_dump() == {
        "items": ["default", "discovered"]
    }


@pytest.mark.asyncio
async def test_agent_profile_layer_replaces_full_payload() -> None:
    layer = AgentProfileLayer(
        data={
            "enabled_tools": ["grep"],
            "tools": {"write_file": {"permission": "never"}},
        }
    )

    await layer.load()
    fingerprint = layer.fingerprint
    assert fingerprint is not None
    await layer.apply(
        ConfigPatch(
            ReplaceOperationPatch(
                path="", value={"disabled_tools": ["exit_plan_mode"]}
            ),
            fingerprint=fingerprint,
            reason="agent profile changed",
        )
    )

    assert (await layer.load()).model_dump() == {"disabled_tools": ["exit_plan_mode"]}


@pytest.mark.asyncio
async def test_agent_profile_layer_clear_removes_profile_overrides() -> None:
    layer = AgentProfileLayer(data={"bypass_tool_permissions": True})

    await layer.load()
    fingerprint = layer.fingerprint
    assert fingerprint is not None
    await layer.apply(
        ConfigPatch(
            ReplaceOperationPatch(path="", value={}),
            fingerprint=fingerprint,
            reason="agent profile cleared",
        )
    )

    assert (await layer.load()).model_dump() == {}


@pytest.mark.asyncio
async def test_default_check_trust_returns_false() -> None:
    class DefaultTrustLayer(ConfigLayer[BaseModel]):
        async def _build_config_snapshot(self) -> LayerConfigSnapshot:
            return LayerConfigSnapshot(data={}, fingerprint="fp")

        async def _save_to_store(self, _next_config: BaseModel) -> str:
            raise NotImplementedError

    layer = DefaultTrustLayer(name="default")
    result = await layer.resolve_trust()
    assert result is False


@pytest.mark.asyncio
async def test_trust_initially_none() -> None:
    layer = StubLayer()
    assert layer.is_trusted is None


@pytest.mark.asyncio
async def test_resolve_trust_trusted() -> None:
    layer = StubLayer(trusted=True)
    result = await layer.resolve_trust()
    assert result is True
    assert layer.is_trusted is True


@pytest.mark.asyncio
async def test_resolve_trust_untrusted() -> None:
    layer = StubLayer(trusted=False)
    result = await layer.resolve_trust()
    assert result is False
    assert layer.is_trusted is False


@pytest.mark.asyncio
async def test_check_trust_failure_wrapped() -> None:
    class BrokenTrustLayer(StubLayer):
        async def _check_trust(self) -> bool:
            raise OSError("trust store unavailable")

    layer = BrokenTrustLayer()
    with pytest.raises(LayerImplementationError, match="_check_trust") as exc_info:
        await layer.resolve_trust()
    assert isinstance(exc_info.value.__cause__, IOError)


@pytest.mark.asyncio
async def test_load_returns_data() -> None:
    layer = StubLayer(data={"key": "value"})
    result = await layer.load()
    assert isinstance(result, RawConfig)
    assert result.model_dump() == {"key": "value"}
    assert layer.fingerprint == "fp-1"


@pytest.mark.asyncio
async def test_load_auto_resolves_trust() -> None:
    layer = StubLayer(trusted=True, data={"a": 1})
    assert layer.is_trusted is None
    result = await layer.load()
    assert layer.is_trusted is True
    assert result.model_dump() == {"a": 1}
    assert layer.fingerprint == "fp-1"

    await layer.resolve_trust()
    assert layer.fingerprint == "fp-1"


@pytest.mark.asyncio
async def test_load_caches_result() -> None:
    layer = StubLayer()
    await layer.load()
    await layer.load()
    await layer.load()
    assert layer.read_count == 1


@pytest.mark.asyncio
async def test_load_force_bypasses_cache() -> None:
    layer = StubLayer()
    await layer.load()
    assert layer.read_count == 1
    await layer.load(force=True)
    assert layer.read_count == 2
    assert layer.fingerprint == "fp-2"


@pytest.mark.asyncio
async def test_load_untrusted_raises() -> None:
    layer = StubLayer(trusted=False)
    with pytest.raises(UntrustedLayerError, match="stub"):
        await layer.load()


@pytest.mark.asyncio
async def test_invalidate_cache_causes_reload() -> None:
    layer = StubLayer()
    await layer.load()
    assert layer.read_count == 1
    await layer.invalidate_cache()
    await layer.load()
    assert layer.read_count == 2


@pytest.mark.asyncio
async def test_resolve_trust_clears_data_on_revocation() -> None:
    layer = StubLayer(data={"v": 1})
    result1 = await layer.load()
    assert result1.model_dump() == {"v": 1}

    # External revocation via resolve_trust (not revoke_trust)
    layer._stub_trusted = False
    await layer.resolve_trust()
    assert layer.is_trusted is False
    assert layer.fingerprint is None

    # Re-trust and update backing data while revoked
    layer._stub_trusted = True
    layer._data = {"v": 2}
    await layer.resolve_trust()

    result2 = await layer.load()
    assert result2.model_dump() == {"v": 2}


@pytest.mark.asyncio
async def test_load_returns_deep_copy() -> None:
    layer = StubLayer(data={"items": ["a", "b"]})
    result1 = await layer.load()
    assert result1.model_extra is not None
    result1.model_extra["items"].append("mutated")

    result2 = await layer.load()
    assert result2.model_dump() == {"items": ["a", "b"]}
    assert layer.read_count == 1


@pytest.mark.asyncio
async def test_build_config_snapshot_failure_wrapped() -> None:
    class BrokenReadLayer(StubLayer):
        async def _build_config_snapshot(self) -> LayerConfigSnapshot:
            raise OSError("config file missing")

    layer = BrokenReadLayer()
    with pytest.raises(
        LayerImplementationError, match="_build_config_snapshot"
    ) as exc_info:
        await layer.load()
    assert isinstance(exc_info.value.__cause__, IOError)


@pytest.mark.asyncio
async def test_build_config_snapshot_concurrency_conflict_propagates() -> None:
    class ConflictingReadLayer(StubLayer):
        async def _build_config_snapshot(self) -> LayerConfigSnapshot:
            raise ConcurrencyConflictError(expected_fp="before", actual_fp="after")

    layer = ConflictingReadLayer()
    with pytest.raises(ConcurrencyConflictError):
        await layer.load()


@pytest.mark.asyncio
async def test_default_schema_preserves_extras() -> None:
    layer = StubLayer(data={"anything": "goes"})
    result = await layer.load()
    assert isinstance(result, RawConfig)
    assert result.model_dump() == {"anything": "goes"}


@pytest.mark.asyncio
async def test_custom_schema_validates() -> None:
    layer = StubLayer(output_schema=SampleSchema, data={"name": "test", "count": 3})
    result = await layer.load()
    assert isinstance(result, SampleSchema)
    assert result.name == "test"
    assert result.count == 3


@pytest.mark.asyncio
async def test_invalid_data_raises_layer_implementation_error() -> None:
    layer = StubLayer(output_schema=SampleSchema, data={"count": "bad"})
    with pytest.raises(
        LayerImplementationError, match="_build_config_snapshot"
    ) as exc_info:
        await layer.load()
    assert isinstance(exc_info.value.__cause__, ValidationError)


@pytest.mark.asyncio
async def test_concurrent_loads_serialize() -> None:
    class SlowLayer(ConfigLayer[BaseModel]):
        def __init__(self) -> None:
            super().__init__(name="slow")
            self.read_count = 0

        async def _check_trust(self) -> bool:
            return True

        async def _build_config_snapshot(self) -> LayerConfigSnapshot:
            self.read_count += 1
            await asyncio.sleep(0.05)
            return LayerConfigSnapshot(
                data={"v": self.read_count}, fingerprint=f"fp-{self.read_count}"
            )

        async def _save_to_store(self, _next_config: BaseModel) -> str:
            raise NotImplementedError

    layer = SlowLayer()
    results = await asyncio.gather(layer.load(), layer.load(), layer.load())
    assert layer.read_count == 1
    assert all(r == results[0] for r in results)


@pytest.mark.asyncio
async def test_fingerprint_returns_none_before_load() -> None:
    layer = StubLayer()
    assert layer.fingerprint is None


@pytest.mark.asyncio
async def test_apply_not_implemented() -> None:
    layer = StubLayer()
    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)

    with pytest.raises(NotImplementedError):
        await layer.apply(ConfigPatch(fingerprint=fingerprint))


@pytest.mark.asyncio
async def test_apply_operation_failure_wrapped() -> None:
    original_data = {"tools": {"disabled_tools": ["bash"]}}
    layer = WritableStubLayer(data=original_data)
    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)

    with pytest.raises(
        ConfigPatchApplicationError, match="Layer 'stub': failed to apply patch"
    ) as exc_info:
        await layer.apply(
            ConfigPatch(
                RemoveOperationPatch(path="/tools/enabled_tools"),
                fingerprint=fingerprint,
            )
        )

    assert exc_info.value.layer_name == "stub"
    assert isinstance(exc_info.value.__cause__, JsonPatchException)
    assert "enabled_tools" in str(exc_info.value.__cause__)
    assert layer.fingerprint == fingerprint
    assert (await layer.load()).model_dump() == original_data
    assert layer.writes == []


@pytest.mark.asyncio
async def test_apply_operation_failure_after_successful_operation_is_atomic() -> None:
    original_data = {"active_model": "old", "tools": {"disabled_tools": ["bash"]}}
    layer = WritableStubLayer(data=original_data)
    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)

    with pytest.raises(ConfigPatchApplicationError):
        await layer.apply(
            ConfigPatch(
                ReplaceOperationPatch(path="/active_model", value="new"),
                RemoveOperationPatch(path="/tools/enabled_tools"),
                fingerprint=fingerprint,
            )
        )

    assert layer.fingerprint == fingerprint
    assert (await layer.load()).model_dump() == original_data
    assert layer.writes == []


@pytest.mark.asyncio
async def test_apply_schema_validation_failure_wrapped() -> None:
    original_data = {"name": "test", "count": 1}
    layer = WritableStubLayer(output_schema=SampleSchema, data=original_data)
    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)

    with pytest.raises(
        ConfigPatchApplicationError, match="Layer 'stub': failed to apply patch"
    ) as exc_info:
        await layer.apply(
            ConfigPatch(
                ReplaceOperationPatch(path="/count", value={"bad": "value"}),
                fingerprint=fingerprint,
            )
        )

    assert exc_info.value.layer_name == "stub"
    assert isinstance(exc_info.value.__cause__, ValidationError)
    assert layer.fingerprint == fingerprint
    assert (await layer.load()).model_dump() == original_data
    assert layer.writes == []


@pytest.mark.asyncio
async def test_apply_cancel_rejects_stale_fingerprint() -> None:
    layer = WritableStubLayer(data={"key": "old"})
    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)

    await layer.apply(
        ConfigPatch(
            ReplaceOperationPatch(path="/key", value="first"), fingerprint=fingerprint
        )
    )

    with pytest.raises(ConcurrencyConflictError) as exc_info:
        await layer.apply(
            ConfigPatch(
                ReplaceOperationPatch(path="/key", value="second"),
                fingerprint=fingerprint,
            )
        )

    assert exc_info.value.expected_fp == fingerprint
    assert exc_info.value.actual_fp == layer.fingerprint
    assert (await layer.load()).model_dump() == {"key": "first"}


@pytest.mark.asyncio
async def test_apply_replace_accepts_stale_fingerprint() -> None:
    layer = WritableStubLayer(data={"key": "old"})
    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)

    await layer.apply(
        ConfigPatch(
            ReplaceOperationPatch(path="/key", value="first"), fingerprint=fingerprint
        )
    )
    await layer.apply(
        ConfigPatch(
            ReplaceOperationPatch(path="/key", value="second"), fingerprint=fingerprint
        ),
        on_conflict=ConflictStrategy.REPLACE,
    )

    assert (await layer.load()).model_dump() == {"key": "second"}
    assert layer.writes == [{"key": "first"}, {"key": "second"}]


@pytest.mark.asyncio
async def test_save_to_store_failure_wrapped() -> None:
    class BrokenApplyLayer(StubLayer):
        async def _save_to_store(self, _next_config: BaseModel) -> str:
            raise OSError("disk full")

    layer = BrokenApplyLayer(data={"key": "old"})
    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)

    with pytest.raises(LayerImplementationError, match="_save_to_store") as exc_info:
        await layer.apply(
            ConfigPatch(
                ReplaceOperationPatch(path="/key", value="new"), fingerprint=fingerprint
            )
        )

    assert isinstance(exc_info.value.__cause__, OSError)


# Scenario: LocalUserConfigLayer


class UserConfigSchema(BaseModel):
    active_model: str
    theme: str = "dark"


class FakeLocalUserLayer(ConfigLayer[UserConfigSchema]):
    """Simulates ~/.vibe/config.toml — always trusted, typed output."""

    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__(name="user-toml", output_schema=UserConfigSchema)
        self._data = data

    async def _check_trust(self) -> bool:
        return True

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        return LayerConfigSnapshot(data=dict(self._data), fingerprint="user-fp")

    async def _save_to_store(self, _next_config: UserConfigSchema) -> str:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_scenario_local_user_layer_always_trusted() -> None:
    layer = FakeLocalUserLayer({"active_model": "devstral-2", "theme": "light"})

    assert layer.is_trusted is None
    result = await layer.load()
    assert layer.is_trusted is True

    assert isinstance(result, UserConfigSchema)
    assert result.active_model == "devstral-2"
    assert result.theme == "light"

    validated = layer.validate_output({
        "active_model": "mistral-large",
        "theme": "dark",
    })
    assert isinstance(validated, UserConfigSchema)
    assert validated.active_model == "mistral-large"


def test_toml_snapshot_drops_none_optional_fields_and_round_trips(
    tmp_path: Path,
) -> None:
    from vibe.core.config.layers._base import _read_toml_snapshot, _write_toml_snapshot
    from vibe.core.config.models import ModelConfig

    config = RawConfig.model_validate({
        "active_model": "paid",
        "models": {
            "paid": ModelConfig(
                name="paid-latest",
                provider="mistral",
                alias="paid",
                input_price=1.5,
                output_price=7.5,
                cached_input_price=0.15,
            ),
            "free": ModelConfig(name="free-latest", provider="llamacpp", alias="free"),
        },
    })
    path = tmp_path / "config.toml"

    _write_toml_snapshot(path, config)

    with path.open("rb") as file:
        data = tomllib.load(file)
    models = {model["alias"]: model for model in data["models"]}
    assert models["paid"]["cached_input_price"] == 0.15
    assert "cached_input_price" not in models["free"]

    reloaded = _read_toml_snapshot(path).data
    reloaded_models = reloaded["models"]
    assert reloaded_models["paid"]["cached_input_price"] == 0.15
    assert "cached_input_price" not in reloaded_models["free"]
