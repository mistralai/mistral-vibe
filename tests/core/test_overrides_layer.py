from __future__ import annotations

import pytest

from vibe.core.config.layers.overrides import OverridesLayer


@pytest.mark.asyncio
async def test_returns_provided_dict() -> None:
    data = {"active_model": "custom", "api_timeout": 30.0}
    layer = OverridesLayer(data=data)
    result = await layer.load()
    assert result.model_extra == data


@pytest.mark.asyncio
async def test_always_trusted() -> None:
    layer = OverridesLayer(data={})
    assert await layer.resolve_trust() is True


@pytest.mark.asyncio
async def test_apply_raises_not_implemented() -> None:
    layer = OverridesLayer(data={})
    with pytest.raises(NotImplementedError, match="M2"):
        await layer.apply({"op": "set"})


@pytest.mark.asyncio
async def test_default_name() -> None:
    layer = OverridesLayer(data={})
    assert layer.name == "overrides"


@pytest.mark.asyncio
async def test_custom_name() -> None:
    layer = OverridesLayer(data={}, name="cli-overrides")
    assert layer.name == "cli-overrides"


@pytest.mark.asyncio
async def test_empty_dict() -> None:
    layer = OverridesLayer(data={})
    result = await layer.load()
    assert result.model_extra == {}


@pytest.mark.asyncio
async def test_nested_data_preserved() -> None:
    data = {"models": {"active_model": "test"}, "tools": {"enabled_tools": ["a"]}}
    layer = OverridesLayer(data=data)
    result = await layer.load()
    assert result.model_extra == data


@pytest.mark.asyncio
async def test_force_reload_returns_same_data() -> None:
    data = {"key": "value"}
    layer = OverridesLayer(data=data)
    await layer.load()
    result = await layer.load(force=True)
    assert result.model_extra == data


@pytest.mark.asyncio
async def test_output_isolated_from_internal_data() -> None:
    data = {"key": "value"}
    layer = OverridesLayer(data=data)
    result = await layer.load()
    # Mutating the returned model_extra must not affect subsequent loads
    assert result.model_extra is not None
    result.model_extra["key"] = "mutated"
    result2 = await layer.load(force=True)
    assert result2.model_extra == {"key": "value"}


@pytest.mark.asyncio
async def test_live_reference_picks_up_caller_mutation() -> None:
    data: dict[str, object] = {"key": "original"}
    layer = OverridesLayer(data=data)
    await layer.load()
    data["key"] = "updated"
    result = await layer.load(force=True)
    assert result.model_extra == {"key": "updated"}
