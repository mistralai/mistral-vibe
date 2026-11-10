"""Model catalog configuration shared by the Unified runtime and host resources."""

from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Any, cast
from unittest.mock import Mock

import pytest
import tomli_w

from tests.stubs.process_services import FakeModelCatalogServer
from vibe.app_server._cached_layers import apply_held_model_catalog
from vibe.app_server._process_services import ProcessServices
import vibe.app_server._runtime as runtime_module
from vibe.app_server.protocol import SessionOptions
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.layers.model_catalog import apply_model_catalog
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
        VibeConfigSchema.model_validate({"experimental_enable_model_catalog": True}),
        None,
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
async def test_the_held_catalog_is_ignored_with_the_flag_off(
    process_services: ProcessServices, model_catalog_server: FakeModelCatalogServer
) -> None:
    await _hold(process_services, model_catalog_server, _runnable_catalog("held"))
    load = Mock(wraps=process_services.model_catalog.load)
    orchestrator = await build_default_orchestrator()

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


def _cached_eval(*, catalog_on: bool) -> Any:
    from tests.stubs.experiment_responses import routing_eval_response
    from vibe.core.experiments.models import EvalResponse

    features = routing_eval_response("glm-5-3").model_dump()["features"]
    features["vibe_cli_model_catalog"] = {"defaultValue": catalog_on, "rules": []}
    return EvalResponse.model_validate({"features": features})


@pytest.mark.asyncio
@pytest.mark.parametrize("catalog_on", [True, False])
async def test_a_cached_flag_decides_the_cached_catalog(
    catalog_on: bool,
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    model_catalog_server: FakeModelCatalogServer,
) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config.update({"active_model": "", "enable_telemetry": True})
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
    monkeypatch.setattr(
        "vibe.app_server._cached_layers.load_cached_eval_response",
        lambda _config, _credentials=None: _cached_eval(catalog_on=catalog_on),
    )
    await _hold(
        process_services, model_catalog_server, _runnable_catalog("zai-glm-5-3")
    )

    session_config = await runtime_module.HarnessProcess(
        services=process_services
    )._build_session_config(SessionOptions(cwd=str(tmp_path)))
    effective = session_config.config_orchestrator.config

    assert ("zai-glm-5-3" in effective.models) is catalog_on
    assert ("glm-5-3" in effective.models) is not catalog_on
