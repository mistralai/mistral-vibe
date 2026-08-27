from __future__ import annotations

import pytest

from vibe.core.config.layers.admin import AdminConfigLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.vibe_schema import VibeConfigSchema


@pytest.mark.asyncio
async def test_empty_by_default() -> None:
    layer = AdminConfigLayer()
    result = await layer.load()
    assert result.model_extra == {}


@pytest.mark.asyncio
async def test_always_trusted() -> None:
    assert await AdminConfigLayer().resolve_trust() is True


@pytest.mark.asyncio
async def test_name() -> None:
    assert AdminConfigLayer().name == "admin"


@pytest.mark.asyncio
async def test_load_managed_toml_sets_data() -> None:
    layer = AdminConfigLayer()
    layer.load_managed_toml('active_model = "enforced"\napi_timeout = 12.0\n')
    result = await layer.load(force=True)
    assert result.model_extra == {"active_model": "enforced", "api_timeout": 12.0}


@pytest.mark.asyncio
async def test_load_managed_toml_normalizes_model_arrays() -> None:
    layer = AdminConfigLayer()
    layer.load_managed_toml('[[models]]\nalias = "m1"\nname = "n1"\nprovider = "p"\n')
    result = await layer.load(force=True)
    assert result.model_extra is not None
    assert isinstance(result.model_extra["models"], dict)
    assert "m1" in result.model_extra["models"]


@pytest.mark.asyncio
async def test_save_to_store_raises() -> None:
    layer = AdminConfigLayer()
    with pytest.raises(NotImplementedError):
        await layer._save_to_store(layer.output_schema())


@pytest.mark.asyncio
async def test_admin_layer_shadows_lower_layers() -> None:
    overrides = OverridesLayer(data={"api_timeout": 10.0})
    admin = AdminConfigLayer()

    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[overrides, admin],
        default_layer_resolver=lambda: overrides,
    )
    assert orchestrator.config.api_timeout == 10.0

    admin.load_managed_toml("api_timeout = 99.0\n")
    await orchestrator.reload()

    assert orchestrator.config.api_timeout == 99.0


@pytest.mark.asyncio
async def test_write_to_lower_layer_stays_shadowed() -> None:
    overrides = OverridesLayer(data={"api_timeout": 10.0})
    admin = AdminConfigLayer()
    admin.load_managed_toml("api_timeout = 99.0\n")

    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[overrides, admin],
        default_layer_resolver=lambda: overrides,
    )
    assert orchestrator.config.api_timeout == 99.0

    await orchestrator.set_field("/api_timeout", 20.0)

    # The write lands in the overrides layer but admin still wins the merge.
    assert orchestrator.config.api_timeout == 99.0
