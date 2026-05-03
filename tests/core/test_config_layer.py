from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, ValidationError
import pytest

from vibe.core.config.layer import (
    ConfigLayer,
    LayerImplementationError,
    RawConfig,
    UntrustedLayerError,
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

    async def _read_config(self) -> dict[str, Any]:
        self.read_count += 1
        return dict(self._data)


class ObservableStubLayer(StubLayer):
    """Stub that records _on_trust_changed calls."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.trust_changes: list[tuple[bool | None, bool | None]] = []

    async def _on_trust_changed(self, old: bool | None, new: bool | None) -> None:
        self.trust_changes.append((old, new))


class SampleSchema(BaseModel):
    name: str
    count: int = 0


def test_abstract_read_config_enforced() -> None:
    class IncompleteLayer(ConfigLayer[BaseModel]):
        pass

    with pytest.raises(TypeError):
        IncompleteLayer(name="incomplete")  # type: ignore[abstract]


def test_repr() -> None:
    layer = StubLayer(name="my-layer")
    assert repr(layer) == "StubLayer(name='my-layer')"


@pytest.mark.asyncio
async def test_default_check_trust_returns_false() -> None:
    class DefaultTrustLayer(ConfigLayer[BaseModel]):
        async def _read_config(self) -> dict[str, Any]:
            return {}

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
async def test_resolve_trust_fires_on_trust_changed() -> None:
    layer = ObservableStubLayer(trusted=True)
    await layer.resolve_trust()
    assert layer.trust_changes == [(None, True)]


@pytest.mark.asyncio
async def test_resolve_trust_no_callback_when_unchanged() -> None:
    layer = ObservableStubLayer(trusted=True)
    await layer.resolve_trust()
    layer.trust_changes.clear()
    await layer.resolve_trust()
    assert layer.trust_changes == []


@pytest.mark.asyncio
async def test_grant_trust() -> None:
    layer = StubLayer(trusted=False)
    await layer.grant_trust()
    assert layer.is_trusted is True


@pytest.mark.asyncio
async def test_grant_trust_fires_callback() -> None:
    layer = ObservableStubLayer(trusted=False)
    await layer.grant_trust()
    assert layer.trust_changes == [(None, True)]


@pytest.mark.asyncio
async def test_grant_trust_noop_when_already_trusted() -> None:
    layer = ObservableStubLayer(trusted=True)
    await layer.resolve_trust()
    layer.trust_changes.clear()
    await layer.grant_trust()
    assert layer.trust_changes == []


@pytest.mark.asyncio
async def test_revoke_trust() -> None:
    layer = StubLayer(trusted=True)
    await layer.resolve_trust()
    await layer.revoke_trust()
    assert layer.is_trusted is False


@pytest.mark.asyncio
async def test_revoke_trust_fires_callback() -> None:
    layer = ObservableStubLayer(trusted=True)
    await layer.resolve_trust()
    layer.trust_changes.clear()
    await layer.revoke_trust()
    assert layer.trust_changes == [(True, False)]


@pytest.mark.asyncio
async def test_revoke_trust_clears_data() -> None:
    layer = StubLayer(data={"k": "v"})
    await layer.load()
    assert layer.read_count == 1
    await layer.revoke_trust()
    await layer.grant_trust()
    await layer.load()
    assert layer.read_count == 2


@pytest.mark.asyncio
async def test_on_trust_changed_failure_preserves_state() -> None:
    class FailingLayer(StubLayer):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.should_fail = True

        async def _on_trust_changed(self, old: bool | None, new: bool | None) -> None:
            if self.should_fail:
                raise RuntimeError("persistence failure")

    layer = FailingLayer(trusted=False)
    assert layer.is_trusted is None

    with pytest.raises(LayerImplementationError, match="_on_trust_changed") as exc_info:
        await layer.grant_trust()
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert layer.is_trusted is None

    layer.should_fail = False
    await layer.grant_trust()
    assert layer.is_trusted is True


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
    assert result.model_extra == {"key": "value"}


@pytest.mark.asyncio
async def test_load_auto_resolves_trust() -> None:
    layer = StubLayer(trusted=True, data={"a": 1})
    assert layer.is_trusted is None
    result = await layer.load()
    assert layer.is_trusted is True
    assert result.model_extra == {"a": 1}


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


@pytest.mark.asyncio
async def test_load_untrusted_raises() -> None:
    layer = StubLayer(trusted=False)
    with pytest.raises(UntrustedLayerError, match="stub"):
        await layer.load()


@pytest.mark.asyncio
async def test_load_after_grant_trust() -> None:
    layer = StubLayer(trusted=False)
    await layer.grant_trust()
    result = await layer.load()
    assert isinstance(result, RawConfig)


@pytest.mark.asyncio
async def test_load_after_revoke_trust_raises() -> None:
    layer = StubLayer(trusted=True)
    await layer.load()
    await layer.revoke_trust()
    with pytest.raises(UntrustedLayerError):
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
async def test_revoke_grant_cycle_refreshes_data() -> None:
    layer = StubLayer(data={"v": 1})
    result1 = await layer.load()
    assert result1.model_extra == {"v": 1}

    await layer.revoke_trust()
    layer._data = {"v": 2}
    await layer.grant_trust()

    result2 = await layer.load()
    assert result2.model_extra == {"v": 2}


@pytest.mark.asyncio
async def test_resolve_trust_clears_data_on_revocation() -> None:
    layer = StubLayer(data={"v": 1})
    result1 = await layer.load()
    assert result1.model_extra == {"v": 1}

    # External revocation via resolve_trust (not revoke_trust)
    layer._stub_trusted = False
    await layer.resolve_trust()
    assert layer.is_trusted is False

    # Re-trust and update backing data while revoked
    layer._stub_trusted = True
    layer._data = {"v": 2}
    await layer.resolve_trust()

    result2 = await layer.load()
    assert result2.model_extra == {"v": 2}


@pytest.mark.asyncio
async def test_load_returns_deep_copy() -> None:
    layer = StubLayer(data={"items": ["a", "b"]})
    result1 = await layer.load()
    assert result1.model_extra is not None
    result1.model_extra["items"].append("mutated")

    result2 = await layer.load()
    assert result2.model_extra == {"items": ["a", "b"]}
    assert layer.read_count == 1


@pytest.mark.asyncio
async def test_read_config_failure_wrapped() -> None:
    class BrokenReadLayer(StubLayer):
        async def _read_config(self) -> dict[str, Any]:
            raise OSError("config file missing")

    layer = BrokenReadLayer()
    with pytest.raises(LayerImplementationError, match="_read_config") as exc_info:
        await layer.load()
    assert isinstance(exc_info.value.__cause__, IOError)


@pytest.mark.asyncio
async def test_default_schema_preserves_extras() -> None:
    layer = StubLayer(data={"anything": "goes"})
    result = await layer.load()
    assert isinstance(result, RawConfig)
    assert result.model_extra == {"anything": "goes"}


@pytest.mark.asyncio
async def test_custom_schema_validates() -> None:
    layer = StubLayer(output_schema=SampleSchema, data={"name": "test", "count": 3})
    result = await layer.load()
    assert isinstance(result, SampleSchema)
    assert result.name == "test"
    assert result.count == 3


@pytest.mark.asyncio
async def test_invalid_data_raises_validation_error() -> None:
    layer = StubLayer(output_schema=SampleSchema, data={"count": "bad"})
    with pytest.raises(ValidationError):
        await layer.load()


@pytest.mark.asyncio
async def test_concurrent_loads_serialize() -> None:
    class SlowLayer(ConfigLayer[BaseModel]):
        def __init__(self) -> None:
            super().__init__(name="slow")
            self.read_count = 0

        async def _check_trust(self) -> bool:
            return True

        async def _read_config(self) -> dict[str, Any]:
            self.read_count += 1
            await asyncio.sleep(0.05)
            return {"v": self.read_count}

    layer = SlowLayer()
    results = await asyncio.gather(layer.load(), layer.load(), layer.load())
    assert layer.read_count == 1
    assert all(r == results[0] for r in results)


@pytest.mark.asyncio
async def test_get_fingerprint_not_implemented() -> None:
    layer = StubLayer()
    with pytest.raises(NotImplementedError):
        await layer.get_fingerprint()


@pytest.mark.asyncio
async def test_apply_not_implemented() -> None:
    layer = StubLayer()
    with pytest.raises(NotImplementedError):
        await layer.apply({"op": "set"})


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

    async def _read_config(self) -> dict[str, Any]:
        return dict(self._data)


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


# Scenario: LocalProjectConfigLayer


class FakeLocalProjectLayer(ConfigLayer[BaseModel]):
    def __init__(
        self, *, project_path: str, data: dict[str, Any], trust_store: dict[str, bool]
    ) -> None:
        super().__init__(name=f"project-toml:{project_path}")
        self._project_path = project_path
        self._data = data
        self._trust_store = trust_store

    async def _check_trust(self) -> bool:
        return self._trust_store.get(self._project_path, False)

    async def _on_trust_changed(self, old: bool | None, new: bool | None) -> None:
        if new:
            self._trust_store[self._project_path] = True
        else:
            self._trust_store.pop(self._project_path, None)

    async def _read_config(self) -> dict[str, Any]:
        return dict(self._data)


@pytest.mark.asyncio
async def test_scenario_local_project_layer_trust_lifecycle() -> None:
    trust_store: dict[str, bool] = {}
    project_data = {"disabled_tools": ["rm"], "max_tokens": 4096}

    # 1. Fresh layer with empty trust store — load raises
    layer = FakeLocalProjectLayer(
        project_path="/tmp/my-project", data=project_data, trust_store=trust_store
    )
    with pytest.raises(UntrustedLayerError):
        await layer.load()

    # 2. Grant trust — persisted in store, load succeeds
    await layer.grant_trust()
    assert trust_store == {"/tmp/my-project": True}
    result = await layer.load()
    assert result.model_extra == project_data

    # 3. New instance with same trust store — loads directly
    layer2 = FakeLocalProjectLayer(
        project_path="/tmp/my-project", data=project_data, trust_store=trust_store
    )
    assert layer2.is_trusted is None
    result2 = await layer2.load()
    assert layer2.is_trusted is True
    assert result2.model_extra == project_data

    # 4. Revoke trust — removed from store, load raises
    await layer2.revoke_trust()
    assert "/tmp/my-project" not in trust_store
    with pytest.raises(UntrustedLayerError):
        await layer2.load()
