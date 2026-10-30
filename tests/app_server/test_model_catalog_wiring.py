"""Model catalog configuration shared by the Unified runtime and host resources."""

from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Any
from unittest.mock import Mock

import pytest
import tomli_w

from vibe.app_server._process_services import ProcessServices
import vibe.app_server._runtime as runtime_module
from vibe.app_server.protocol import SessionOptions
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.layers.model_catalog import (
    apply_cached_model_catalog,
    apply_model_catalog,
)
from vibe.core.model_catalog import ModelCatalogResponse, parse_model_catalog_response


async def _orchestrator_with_catalog(catalog: ModelCatalogResponse) -> Any:
    orchestrator = await build_default_orchestrator({
        "experimental_enable_model_catalog": True
    })
    await apply_model_catalog(orchestrator, catalog)
    return orchestrator


def _runnable_catalog(model_id: str) -> ModelCatalogResponse:
    parsed = parse_model_catalog_response({
        "models": [
            {
                "id": model_id,
                "label": model_id,
                "capabilities": {"function_calling": True},
            }
        ]
    })
    assert parsed is not None
    return parsed


def _enable_model_catalog(config_dir: Path) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["experimental_enable_model_catalog"] = True
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


@pytest.mark.asyncio
async def test_the_cached_catalog_reaches_the_session_config(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enable_model_catalog(config_dir)
    monkeypatch.setattr(
        "vibe.core.model_catalog.load_cached_model_catalog",
        lambda _config: _runnable_catalog("cached-model"),
    )
    orchestrator = await build_default_orchestrator()

    await apply_cached_model_catalog(orchestrator)

    assert "cached-model" in orchestrator.config.models


@pytest.mark.asyncio
async def test_the_cached_catalog_is_ignored_with_the_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_cache = Mock(return_value=_runnable_catalog("cached-model"))
    monkeypatch.setattr("vibe.core.model_catalog.load_cached_model_catalog", load_cache)
    orchestrator = await build_default_orchestrator()

    await apply_cached_model_catalog(orchestrator)

    assert "cached-model" not in orchestrator.config.models
    load_cache.assert_not_called()


@pytest.mark.asyncio
async def test_a_field_write_to_a_catalog_model_stays_settings_only() -> None:
    from vibe.app_server._config_write import config_write_ops_to_patches
    from vibe.app_server.protocol import ConfigWriteOpWire
    from vibe.core.config.patch import AddOperationPatch

    orchestrator = await _orchestrator_with_catalog(_runnable_catalog("cat-model"))

    patches = config_write_ops_to_patches(
        orchestrator.config,
        [ConfigWriteOpWire(op="set", path="/models/cat-model/temperature", value=0.5)],
        sparse_override_model_aliases=(
            await orchestrator.sparse_override_model_aliases()
        ),
    )

    # The catalog layer redefines the model at launch: copying its identity
    # would keep a model running after the catalog retires it, so the write
    # carries the changed field alone.
    assert len(patches) == 1
    patch = patches[0]
    assert isinstance(patch, AddOperationPatch)
    assert patch.path == "/models/cat-model/temperature"
    assert patch.value == 0.5


@pytest.mark.asyncio
async def test_a_field_write_to_a_growthbook_model_stays_settings_only() -> None:
    import json

    from vibe.app_server._config_write import config_write_ops_to_patches
    from vibe.app_server.protocol import ConfigWriteOpWire
    from vibe.core.config.layers.growthbook import GrowthbookLayer
    from vibe.core.config.patch import AddOperationPatch
    from vibe.core.experiments.active import ExperimentName

    orchestrator = await build_default_orchestrator()
    growthbook = orchestrator.get_layer(GrowthbookLayer.NAME)
    assert isinstance(growthbook, GrowthbookLayer)
    growthbook.set_variants({
        ExperimentName.CLI_EXTRA_MODELS.value: json.dumps([
            {"name": "routed-model", "provider": "mistral", "alias": "routed-model"}
        ])
    })
    await orchestrator.reload()

    patches = config_write_ops_to_patches(
        orchestrator.config,
        [
            ConfigWriteOpWire(
                op="set", path="/models/routed-model/temperature", value=0.5
            )
        ],
        sparse_override_model_aliases=(
            await orchestrator.sparse_override_model_aliases()
        ),
    )

    # GrowthBook models exist regardless of the catalog flag: the write is
    # settings-only here too.
    assert len(patches) == 1
    patch = patches[0]
    assert isinstance(patch, AddOperationPatch)
    assert patch.path == "/models/routed-model/temperature"
    assert patch.value == 0.5


@pytest.mark.asyncio
async def test_a_field_write_to_an_admin_model_keeps_its_identity() -> None:
    from vibe.app_server._config_write import config_write_ops_to_patches
    from vibe.app_server.protocol import ConfigWriteOpWire
    from vibe.core.config.layers.admin import AdminConfigLayer
    from vibe.core.config.patch import AddOperationPatch

    orchestrator = await build_default_orchestrator()
    admin = orchestrator.get_layer(AdminConfigLayer.NAME)
    assert isinstance(admin, AdminConfigLayer)
    admin.restore({
        "models": {
            "admin-model": {
                "name": "admin-model",
                "provider": "mistral",
                "alias": "admin-model",
            }
        }
    })
    await orchestrator.reload()

    patches = config_write_ops_to_patches(
        orchestrator.config,
        [
            ConfigWriteOpWire(
                op="set", path="/models/admin-model/temperature", value=0.5
            )
        ],
        sparse_override_model_aliases=(
            await orchestrator.sparse_override_model_aliases()
        ),
    )

    # No layer redefines an admin model at launch, so a sparse entry would be
    # an orphan next launch: the write still carries the identity fields.
    assert len(patches) == 1
    patch = patches[0]
    assert isinstance(patch, AddOperationPatch)
    assert patch.path == "/models/admin-model"
    assert patch.value == {
        "name": "admin-model",
        "provider": "mistral",
        "alias": "admin-model",
        "temperature": 0.5,
    }


@pytest.mark.asyncio
async def test_every_session_config_starts_with_the_cached_catalog(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    # The Unified harness builds every session config here.
    _enable_model_catalog(config_dir)
    monkeypatch.setattr(
        "vibe.core.model_catalog.load_cached_model_catalog",
        lambda _config: _runnable_catalog("cached-model"),
    )

    session_config = await runtime_module.HarnessProcess(
        services=process_services
    )._build_session_config(SessionOptions(cwd=str(tmp_path)))

    assert "cached-model" in session_config.config_orchestrator.config.models


@pytest.mark.asyncio
async def test_a_cached_catalog_applies_a_sparse_override_from_the_first_build(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    # The fixture pins a model whose entry this write replaces; unpinned, or
    # the pin's fallback warning would fail the assertion for another reason.
    _enable_model_catalog(config_dir)
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["active_model"] = ""
    config["models"] = {"cached-model": {"temperature": 0.5}}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    monkeypatch.setattr(
        "vibe.core.model_catalog.load_cached_model_catalog",
        lambda _config: _runnable_catalog("cached-model"),
    )

    session_config = await runtime_module.HarnessProcess(
        services=process_services
    )._build_session_config(SessionOptions(cwd=str(tmp_path)))
    effective = session_config.config_orchestrator.config

    assert effective.models["cached-model"].temperature == 0.5
    assert effective.validation_warnings == ()
