from __future__ import annotations

import pytest

from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.patch import AddOperationPatch, ConfigPatch, ReplaceOperationPatch
from vibe.core.config.types import ConflictStrategy


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
async def test_apply_persists_patch_and_refreshes_cache() -> None:
    layer = OverridesLayer(data={"active_model": "old"})

    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)

    await layer.apply(
        ConfigPatch(
            ReplaceOperationPatch(path="/active_model", value="new"),
            AddOperationPatch(path="/api_timeout", value=30.0),
            fingerprint=fingerprint,
        )
    )

    result = await layer.load(force=True)
    assert result.model_extra == {"active_model": "new", "api_timeout": 30.0}
    assert layer.fingerprint != fingerprint


@pytest.mark.asyncio
async def test_apply_fingerprint_matches_rebuilt_snapshot() -> None:
    layer = OverridesLayer(data={"active_model": "old"})

    await layer.load()
    fingerprint = layer.fingerprint
    assert isinstance(fingerprint, str)

    await layer.apply(
        ConfigPatch(
            ReplaceOperationPatch(path="/active_model", value="new"),
            fingerprint=fingerprint,
        )
    )
    fingerprint_after_apply = layer.fingerprint

    await layer.load(force=True)
    assert layer.fingerprint == fingerprint_after_apply


@pytest.mark.asyncio
async def test_apply_persists_to_in_memory_data() -> None:
    layer = OverridesLayer(data={"active_model": "custom"})
    await layer.load()
    old_fp = layer.fingerprint
    patch = ConfigPatch(
        AddOperationPatch(path="/tools/bash/allowlist", value=["ls"]),
        fingerprint=old_fp or "",
    )

    await layer.apply(patch, on_conflict=ConflictStrategy.CANCEL)

    assert layer.fingerprint != old_fp
    result = await layer.load(force=True)
    assert result.model_extra == {
        "active_model": "custom",
        "tools": {"bash": {"allowlist": ["ls"]}},
    }


@pytest.mark.asyncio
async def test_live_reference_picks_up_caller_mutation() -> None:
    data: dict[str, object] = {"key": "original"}
    layer = OverridesLayer(data=data)
    await layer.load()
    fp1 = layer.fingerprint
    data["key"] = "updated"
    result = await layer.load(force=True)
    fp2 = layer.fingerprint

    assert result.model_extra == {"key": "updated"}
    assert fp1 != fp2
