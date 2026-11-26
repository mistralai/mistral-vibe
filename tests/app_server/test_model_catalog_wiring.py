"""Model catalog configuration shared by the Unified runtime and host resources."""

from __future__ import annotations

import copy
from pathlib import Path
import tomllib
from typing import Any, cast
from unittest.mock import Mock

from pydantic import JsonValue
import pytest
import tomli_w

from tests.stubs.process_services import FakeModelCatalogServer
from vibe.app_server._cached_layers import apply_held_model_catalog
from vibe.app_server._process_services import ProcessServices
import vibe.app_server._runtime as runtime_module
from vibe.app_server.protocol import SessionOptions
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.layers.model_catalog import apply_model_catalog
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.patch import PatchOp
from vibe.core.config.vibe_schema import VibeConfigSchema
from vibe.core.model_catalog import ModelCatalogResponse, parse_model_catalog_response


async def _orchestrator_with_catalog(catalog: ModelCatalogResponse) -> Any:
    orchestrator = await build_default_orchestrator({"enable_model_catalog": True})
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
    config["enable_model_catalog"] = True
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


async def _hold(
    process_services: ProcessServices,
    model_catalog_server: FakeModelCatalogServer,
    catalog: ModelCatalogResponse,
) -> None:
    """Have the process hold *catalog* for the test account, as a session's
    fetch leaves it.
    """
    from vibe.app_server._model_catalog_service import ModelCatalogKey
    from vibe.core.config import VibeConfigSchema

    model_catalog_server.serve(catalog)
    key = await ModelCatalogKey.of(
        VibeConfigSchema.model_validate({"enable_model_catalog": True}), None
    )
    assert key is not None
    assert await process_services.model_catalog.ensure(key) == catalog


@pytest.mark.asyncio
async def test_the_held_catalog_reaches_the_session_config(
    config_dir: Path,
    process_services: ProcessServices,
    model_catalog_server: FakeModelCatalogServer,
) -> None:
    _enable_model_catalog(config_dir)
    await _hold(process_services, model_catalog_server, _runnable_catalog("held"))
    orchestrator = await build_default_orchestrator()

    await apply_held_model_catalog(orchestrator, process_services.model_catalog)

    assert "held" in orchestrator.config.models


@pytest.mark.asyncio
async def test_the_held_catalog_is_ignored_with_the_setting_off(
    process_services: ProcessServices, model_catalog_server: FakeModelCatalogServer
) -> None:
    await _hold(process_services, model_catalog_server, _runnable_catalog("held"))
    load = Mock(wraps=process_services.model_catalog.load)
    orchestrator = await build_default_orchestrator({"enable_model_catalog": False})

    await apply_held_model_catalog(orchestrator, cast(Any, Mock(load=load)))

    assert "held" not in orchestrator.config.models
    assert load.call_args.args == (None,)


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


_ADMIN_GATEWAY = {
    "providers": [{"name": "gateway", "api_base": "https://gateway.example.com"}],
    "models": {
        "admin-model": {
            "name": "azure_ai/admin-model",
            "provider": "gateway",
            "alias": "admin-model",
        }
    },
}


async def _orchestrator_with_admin(
    admin_data: dict[str, Any],
) -> ConfigOrchestrator[VibeConfigSchema]:
    from vibe.core.config.layers.admin import AdminConfigLayer

    orchestrator = await build_default_orchestrator()
    admin = orchestrator.get_layer(AdminConfigLayer.NAME)
    assert isinstance(admin, AdminConfigLayer)
    admin.restore(admin_data)
    await orchestrator.reload()
    return orchestrator


async def _write_through_picker(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], path: str, value: JsonValue
) -> list[PatchOp]:
    from vibe.app_server._config_write import config_write_ops_to_patches
    from vibe.app_server.protocol import ConfigWriteOpWire

    patches = config_write_ops_to_patches(
        orchestrator.config,
        [ConfigWriteOpWire(op="set", path=path, value=value)],
        sparse_override_model_aliases=(
            await orchestrator.sparse_override_model_aliases()
        ),
    )
    assert await orchestrator.apply_patch(patches, reason="test") == []
    return patches


@pytest.mark.asyncio
async def test_a_field_write_to_an_admin_model_is_settings_only(
    config_dir: Path,
) -> None:
    from vibe.core.config.patch import AddOperationPatch

    orchestrator = await _orchestrator_with_admin(_ADMIN_GATEWAY)

    patches = await _write_through_picker(
        orchestrator, "/models/admin-model/thinking", "high"
    )

    # The admin layer defines the model again at every launch: copied identity
    # would keep the model running once the admin removes it.
    (patch,) = patches
    assert isinstance(patch, AddOperationPatch)
    assert patch.path == "/models/admin-model/thinking"
    assert patch.value == "high"
    assert orchestrator.config.models["admin-model"].thinking == "high"
    saved = tomllib.loads((config_dir / "config.toml").read_text())
    assert [m for m in saved["models"] if m["alias"] == "admin-model"] == [
        {"alias": "admin-model", "thinking": "high"}
    ]


@pytest.mark.asyncio
async def test_an_admin_model_the_admin_removed_is_gone_for_its_pin(
    config_dir: Path,
) -> None:
    orchestrator = await _orchestrator_with_admin(_ADMIN_GATEWAY)
    await _write_through_picker(orchestrator, "/active_model", "admin-model")
    await _write_through_picker(orchestrator, "/models/admin-model/thinking", "high")

    # The admin removes the model and keeps its provider.
    relaunched = await _orchestrator_with_admin({
        "providers": _ADMIN_GATEWAY["providers"]
    })

    config = relaunched.config
    assert "admin-model" not in config.models
    assert config.get_active_model().alias == "mistral-large-4-0"
    assert config.missing_model_warnings == (
        "Model 'admin-model' is not available — running 'Mistral Large 4'. "
        "Pick another model to stop this notice.",
    )


@pytest.mark.asyncio
async def test_an_admin_model_the_admin_removed_leaves_its_settings_ignored(
    config_dir: Path,
) -> None:
    orchestrator = await _orchestrator_with_admin(_ADMIN_GATEWAY)
    await _write_through_picker(orchestrator, "/models/admin-model/thinking", "high")

    relaunched = await _orchestrator_with_admin({
        "providers": _ADMIN_GATEWAY["providers"]
    })

    assert "admin-model" not in relaunched.config.models
    assert relaunched.config.validation_warnings == (
        "Settings for model 'admin-model' are ignored: no such model is available.",
    )


@pytest.mark.asyncio
async def test_an_admin_set_field_wins_over_the_users_settings_only_write(
    config_dir: Path,
) -> None:
    admin_data = copy.deepcopy(_ADMIN_GATEWAY)
    admin_data["models"]["admin-model"]["temperature"] = 0.2
    orchestrator = await _orchestrator_with_admin(admin_data)

    await _write_through_picker(orchestrator, "/models/admin-model/thinking", "high")
    await _write_through_picker(orchestrator, "/models/admin-model/temperature", 0.9)

    model = orchestrator.config.models["admin-model"]
    assert model.thinking == "high"
    assert model.temperature == 0.2


@pytest.mark.asyncio
async def test_every_session_config_starts_with_the_held_catalog(
    tmp_path: Path,
    config_dir: Path,
    process_services: ProcessServices,
    model_catalog_server: FakeModelCatalogServer,
) -> None:
    # The Unified harness builds every session config here.
    _enable_model_catalog(config_dir)
    await _hold(
        process_services, model_catalog_server, _runnable_catalog("cached-model")
    )

    session_config = await runtime_module.HarnessProcess(
        services=process_services
    )._build_session_config(SessionOptions(cwd=str(tmp_path)))

    assert "cached-model" in session_config.config_orchestrator.config.models


@pytest.mark.asyncio
async def test_a_cached_catalog_applies_a_sparse_override_from_the_first_build(
    tmp_path: Path,
    config_dir: Path,
    process_services: ProcessServices,
    model_catalog_server: FakeModelCatalogServer,
) -> None:
    # The fixture pins a model whose entry this write replaces; unpinned, or
    # the pin's fallback warning would fail the assertion for another reason.
    _enable_model_catalog(config_dir)
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["active_model"] = ""
    config["models"] = {"cached-model": {"temperature": 0.5}}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    await _hold(
        process_services, model_catalog_server, _runnable_catalog("cached-model")
    )

    session_config = await runtime_module.HarnessProcess(
        services=process_services
    )._build_session_config(SessionOptions(cwd=str(tmp_path)))
    effective = session_config.config_orchestrator.config

    assert effective.models["cached-model"].temperature == 0.5
    assert effective.validation_warnings == ()
