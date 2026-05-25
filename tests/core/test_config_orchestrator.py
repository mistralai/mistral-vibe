from __future__ import annotations

from typing import Annotated, Any

from pydantic import ValidationError
import pytest

from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.schema import ConfigSchema, WithReplaceMerge


class FakeLayer(ConfigLayer[RawConfig]):
    def __init__(self, *, name: str, data: dict[str, Any]) -> None:
        super().__init__(name=name)
        self._data = data

    async def _check_trust(self) -> bool:
        return True

    async def _read_config(self) -> dict[str, Any]:
        return dict(self._data)


class SimpleSchema(ConfigSchema):
    value: Annotated[str, WithReplaceMerge()] = "default"


@pytest.mark.asyncio
async def test_create_builds_config() -> None:
    layer = FakeLayer(name="test", data={"value": "hello"})
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[layer])
    assert orch.config.value == "hello"


@pytest.mark.asyncio
async def test_get_layer_returns_named_layer() -> None:
    layer = FakeLayer(name="my-layer", data={})
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[layer])
    assert orch.get_layer("my-layer") is layer


@pytest.mark.asyncio
async def test_get_layer_unknown_raises() -> None:
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[])
    with pytest.raises(KeyError, match="unknown"):
        orch.get_layer("unknown")


@pytest.mark.asyncio
async def test_reload_picks_up_changes() -> None:
    layer = FakeLayer(name="mutable", data={"value": "original"})
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[layer])
    assert orch.config.value == "original"

    layer._data = {"value": "updated"}
    await orch.reload()
    assert orch.config.value == "updated"


@pytest.mark.asyncio
async def test_config_is_immutable() -> None:
    layer = FakeLayer(name="test", data={"value": "hello"})
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[layer])
    with pytest.raises(ValidationError, match="frozen"):
        orch.config.value = "changed"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_origin_of_missing_key_returns_none() -> None:
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[])
    assert orch.config.origin_of("nonexistent") is None


@pytest.mark.asyncio
async def test_apply_patch_raises_not_implemented() -> None:
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[])
    with pytest.raises(NotImplementedError, match="M2"):
        await orch.apply_patch({})


@pytest.mark.asyncio
async def test_subscribe_raises_not_implemented() -> None:
    orch = await ConfigOrchestrator.create(schema=SimpleSchema, layers=[])
    with pytest.raises(NotImplementedError, match="M3"):
        await orch.subscribe(lambda: None)
