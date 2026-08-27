from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vibe.core.config import ConfigLayer, RawConfig, build_default_orchestrator
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.experiments.active import ExperimentName
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.experiments.models import EvalResponse, ExperimentAttributes

_ROUTED_TEST_ALIAS = "target-testing-model-alias"
_ROUTING_MODEL_CONFIG = {
    "name": "target-testing-model-name",
    "provider": "mistral",
    "alias": _ROUTED_TEST_ALIAS,
    "input_price": "0.0",
    "output_price": "0.0",
    "supports_images": False,
}


class _StubClient(RemoteEvalClient):
    def __init__(self, response: EvalResponse | None) -> None:
        self._response = response

    async def evaluate(self, attributes: ExperimentAttributes) -> EvalResponse | None:
        return self._response

    async def aclose(self) -> None:
        pass


def _response_forcing(variant: str) -> EvalResponse:
    return EvalResponse.model_validate({
        "features": {
            ExperimentName.SYSTEM_PROMPT.value: {
                "defaultValue": "cli",
                "rules": [
                    {
                        "force": variant,
                        "tracks": [
                            {
                                "experiment": {
                                    "key": ExperimentName.SYSTEM_PROMPT.value
                                },
                                "result": {
                                    "key": "1",
                                    "variationId": 1,
                                    "inExperiment": True,
                                },
                            }
                        ],
                    }
                ],
            }
        }
    })


def _response_forcing_managed_shell(variant: str) -> EvalResponse:
    return EvalResponse.model_validate({
        "features": {
            ExperimentName.MANAGED_SHELL_TOOLS.value: {
                "defaultValue": "legacy",
                "rules": [
                    {
                        "force": variant,
                        "tracks": [
                            {
                                "experiment": {
                                    "key": ExperimentName.MANAGED_SHELL_TOOLS.value
                                },
                                "result": {
                                    "key": "1",
                                    "variationId": 1,
                                    "inExperiment": True,
                                },
                            }
                        ],
                    }
                ],
            }
        }
    })


def _response_forcing_without_tracks(variant: str) -> EvalResponse:
    return EvalResponse.model_validate({
        "features": {
            ExperimentName.SYSTEM_PROMPT.value: {
                "defaultValue": "cli",
                "rules": [{"force": variant, "tracks": []}],
            }
        }
    })


def _response_forcing_not_in_experiment(variant: str) -> EvalResponse:
    return EvalResponse.model_validate({
        "features": {
            ExperimentName.SYSTEM_PROMPT.value: {
                "defaultValue": "cli",
                "rules": [
                    {
                        "force": variant,
                        "tracks": [
                            {
                                "experiment": {
                                    "key": ExperimentName.SYSTEM_PROMPT.value
                                },
                                "result": {
                                    "key": "1",
                                    "variationId": 1,
                                    "inExperiment": False,
                                },
                            }
                        ],
                    }
                ],
            }
        }
    })


def _response_with_default_value(variant: str) -> EvalResponse:
    return EvalResponse.model_validate({
        "features": {
            ExperimentName.SYSTEM_PROMPT.value: {"defaultValue": variant, "rules": []}
        }
    })


def _routing_response(payload: dict[str, Any], *, in_experiment: bool) -> EvalResponse:
    return EvalResponse.model_validate({
        "features": {
            ExperimentName.CLI_MODEL_ROUTING.value: {
                "defaultValue": {},
                "rules": [
                    {
                        "force": payload,
                        "tracks": [
                            {
                                "experiment": {
                                    "key": ExperimentName.CLI_MODEL_ROUTING.value
                                },
                                "result": {
                                    "key": "1",
                                    "variationId": 1,
                                    "value": payload,
                                    "inExperiment": in_experiment,
                                },
                            }
                        ],
                    }
                ],
            }
        }
    })


def _manager_with_variant(variant: str) -> ExperimentManager:
    manager = ExperimentManager(client=_StubClient(None))
    manager.hydrate(_response_forcing(variant))
    return manager


def _manager_with_routing(
    payload: dict[str, Any], *, in_experiment: bool = True
) -> ExperimentManager:
    manager = ExperimentManager(client=_StubClient(None))
    manager.hydrate(_routing_response(payload, in_experiment=in_experiment))
    return manager


def _extra_models_response(
    payload: dict[str, Any] | list[Any], *, in_experiment: bool
) -> EvalResponse:
    return EvalResponse.model_validate({
        "features": {
            ExperimentName.CLI_EXTRA_MODELS.value: {
                "defaultValue": {},
                "rules": [
                    {
                        "force": payload,
                        "tracks": [
                            {
                                "experiment": {
                                    "key": ExperimentName.CLI_EXTRA_MODELS.value
                                },
                                "result": {
                                    "key": "1",
                                    "variationId": 1,
                                    "value": payload,
                                    "inExperiment": in_experiment,
                                },
                            }
                        ],
                    }
                ],
            }
        }
    })


def _manager_with_extra_models(
    payload: dict[str, Any] | list[Any], *, in_experiment: bool = True
) -> ExperimentManager:
    manager = ExperimentManager(client=_StubClient(None))
    manager.hydrate(_extra_models_response(payload, in_experiment=in_experiment))
    return manager


def _manager_with_managed_shell_variant(variant: str) -> ExperimentManager:
    manager = ExperimentManager(client=_StubClient(None))
    manager.hydrate(_response_forcing_managed_shell(variant))
    return manager


def _manager_with_forced_variant_without_tracks(variant: str) -> ExperimentManager:
    manager = ExperimentManager(client=_StubClient(None))
    manager.hydrate(_response_forcing_without_tracks(variant))
    return manager


def _manager_with_forced_variant_not_in_experiment(variant: str) -> ExperimentManager:
    manager = ExperimentManager(client=_StubClient(None))
    manager.hydrate(_response_forcing_not_in_experiment(variant))
    return manager


def _manager_with_default_value(variant: str) -> ExperimentManager:
    manager = ExperimentManager(client=_StubClient(None))
    manager.hydrate(_response_with_default_value(variant))
    return manager


def _manager_without_variant() -> ExperimentManager:
    manager = ExperimentManager(client=_StubClient(None))
    manager.hydrate(EvalResponse.model_validate({"features": {}}))
    return manager


def _require_growthbook_layer(layer: ConfigLayer[RawConfig]) -> GrowthbookLayer:
    assert isinstance(layer, GrowthbookLayer)
    return layer


@pytest.mark.asyncio
async def test_returns_empty_before_experiment_manager_is_set() -> None:
    layer = GrowthbookLayer()

    data = await layer.load()

    assert data.model_dump() == {}


@pytest.mark.asyncio
async def test_returns_empty_when_experiment_manager_has_no_variant() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(_manager_without_variant().config_variants())

    data = await layer.load()

    assert data.model_dump() == {}


@pytest.mark.asyncio
async def test_maps_system_prompt_experiment_to_config_field() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(_manager_with_variant("tests").config_variants())

    data = await layer.load()

    assert data.model_dump() == {"system_prompt_id": "tests"}


@pytest.mark.asyncio
async def test_maps_managed_shell_experiment_to_config_field() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(_manager_with_managed_shell_variant("managed").config_variants())

    data = await layer.load()

    assert data.model_dump() == {"managed_shell_tools_enabled": True}


@pytest.mark.asyncio
async def test_maps_forced_system_prompt_without_tracks_to_config_field() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(
        _manager_with_forced_variant_without_tracks("tests").config_variants()
    )

    data = await layer.load()

    assert data.model_dump() == {"system_prompt_id": "tests"}


@pytest.mark.asyncio
async def test_maps_forced_system_prompt_not_in_experiment_to_config_field() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(
        _manager_with_forced_variant_not_in_experiment("tests").config_variants()
    )

    data = await layer.load()

    assert data.model_dump() == {"system_prompt_id": "tests"}


@pytest.mark.asyncio
async def test_ignores_unknown_system_prompt_variant() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(
        _manager_with_variant("removed_after_graduation_2025_07").config_variants()
    )

    data = await layer.load()

    assert data.model_dump() == {}


@pytest.mark.asyncio
async def test_ignores_growthbook_default_value_without_forced_rule() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(_manager_with_default_value("cli").config_variants())

    data = await layer.load()

    assert data.model_dump() == {}


@pytest.mark.asyncio
async def test_default_orchestrator_applies_growthbook_layer() -> None:
    orchestrator = await build_default_orchestrator(require_api_key=False)
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(_manager_with_variant("tests").config_variants())

    await orchestrator.reload()

    assert orchestrator.config.system_prompt_id == "tests"


@pytest.mark.asyncio
async def test_selected_toml_wins_over_growthbook_layer(config_dir: Path) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text('system_prompt_id = "lean"\n', encoding="utf-8")
    orchestrator = await build_default_orchestrator(require_api_key=False)
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(_manager_with_variant("tests").config_variants())

    await orchestrator.reload()

    assert orchestrator.config.system_prompt_id == "lean"


@pytest.mark.asyncio
async def test_selected_toml_disables_growthbook_managed_shell(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text("managed_shell_tools_enabled = false\n", encoding="utf-8")
    orchestrator = await build_default_orchestrator(require_api_key=False)
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(_manager_with_managed_shell_variant("managed").config_variants())

    await orchestrator.reload()

    assert orchestrator.config.managed_shell_tools_enabled is False


@pytest.mark.asyncio
async def test_forced_growthbook_variant_without_tracks_loses_to_selected_toml(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text('system_prompt_id = "lean"\n', encoding="utf-8")
    orchestrator = await build_default_orchestrator(require_api_key=False)
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(
        _manager_with_forced_variant_without_tracks("tests").config_variants()
    )

    await orchestrator.reload()

    assert orchestrator.config.system_prompt_id == "lean"


@pytest.mark.asyncio
async def test_forced_growthbook_variant_not_in_experiment_loses_to_selected_toml(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text('system_prompt_id = "lean"\n', encoding="utf-8")
    orchestrator = await build_default_orchestrator(require_api_key=False)
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(
        _manager_with_forced_variant_not_in_experiment("tests").config_variants()
    )

    await orchestrator.reload()

    assert orchestrator.config.system_prompt_id == "lean"


@pytest.mark.asyncio
async def test_growthbook_default_value_does_not_override_selected_toml(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text('system_prompt_id = "lean"\n', encoding="utf-8")
    orchestrator = await build_default_orchestrator(require_api_key=False)
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(_manager_with_default_value("cli").config_variants())

    await orchestrator.reload()

    assert orchestrator.config.system_prompt_id == "lean"


@pytest.mark.asyncio
async def test_unknown_system_prompt_variant_does_not_break_reload() -> None:
    orchestrator = await build_default_orchestrator(require_api_key=False)
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(
        _manager_with_variant("removed_after_graduation_2025_07").config_variants()
    )

    await orchestrator.reload()

    assert orchestrator.config.system_prompt_id == "cli"


@pytest.mark.asyncio
async def test_runtime_overrides_win_over_growthbook_layer() -> None:
    orchestrator = await build_default_orchestrator(
        {"system_prompt_id": "lean"}, require_api_key=False
    )
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(_manager_with_variant("tests").config_variants())

    await orchestrator.reload()

    assert orchestrator.config.system_prompt_id == "lean"


@pytest.mark.asyncio
async def test_maps_routing_experiment_to_routed_default_model() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(
        _manager_with_routing({
            "active_model": _ROUTED_TEST_ALIAS,
            "fallbacks": {"multimodal": "mistral-medium-3.5"},
        }).config_variants()
    )

    data = await layer.load()

    assert data.model_dump() == {"routed_default_model": _ROUTED_TEST_ALIAS}


@pytest.mark.asyncio
async def test_maps_routing_payload_model_config() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(
        _manager_with_routing({
            "active_model": _ROUTED_TEST_ALIAS,
            "fallbacks": {"multimodal": "mistral-medium-3.5"},
            "model_config": _ROUTING_MODEL_CONFIG,
        }).config_variants()
    )

    data = await layer.load()

    # The model definition is carried as a flat JSON string, like every other
    # growthbook-mapped config value.
    dumped = data.model_dump()
    assert dumped["routed_default_model"] == _ROUTED_TEST_ALIAS
    assert json.loads(dumped["routed_model_config"]) == _ROUTING_MODEL_CONFIG


@pytest.mark.asyncio
async def test_maps_forced_routing_payload_without_experiment() -> None:
    # A 100%-forced rollout (not experiment-bucketed) still routes.
    layer = GrowthbookLayer()
    layer.set_variants(
        _manager_with_routing(
            {"active_model": _ROUTED_TEST_ALIAS}, in_experiment=False
        ).config_variants()
    )

    data = await layer.load()

    assert data.model_dump() == {"routed_default_model": _ROUTED_TEST_ALIAS}


@pytest.mark.asyncio
async def test_ignores_routing_payload_without_active_model() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(
        _manager_with_routing({"fallbacks": {"multimodal": "x"}}).config_variants()
    )

    data = await layer.load()

    assert data.model_dump() == {}


@pytest.mark.asyncio
async def test_routing_experiment_sets_default_for_unpinned_user(
    config_dir: Path,
) -> None:
    (config_dir / "config.toml").write_text("", encoding="utf-8")
    orchestrator = await build_default_orchestrator(require_api_key=False)
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(
        _manager_with_routing({
            "active_model": _ROUTED_TEST_ALIAS,
            "model_config": _ROUTING_MODEL_CONFIG,
        }).config_variants()
    )

    await orchestrator.reload()

    config = orchestrator.config
    assert config.active_model == ""  # unpinned
    assert config.resolve_default_model_alias() == _ROUTED_TEST_ALIAS
    active = config.get_active_model()
    assert active.alias == _ROUTED_TEST_ALIAS
    assert active.name == "target-testing-model-name"
    assert active.input_price == 0.0  # string price coerced to float


@pytest.mark.asyncio
async def test_routing_experiment_does_not_override_pinned_model(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text('active_model = "devstral-small"\n', encoding="utf-8")
    orchestrator = await build_default_orchestrator(require_api_key=False)
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(
        _manager_with_routing({
            "active_model": _ROUTED_TEST_ALIAS,
            "model_config": _ROUTING_MODEL_CONFIG,
        }).config_variants()
    )

    await orchestrator.reload()

    config = orchestrator.config
    assert config.active_model == "devstral-small"
    assert config.get_active_model().alias == "devstral-small"
    assert _ROUTED_TEST_ALIAS in config.models


@pytest.mark.asyncio
async def test_routing_experiment_honors_manual_selection_of_routed_model(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text(f'active_model = "{_ROUTED_TEST_ALIAS}"\n', encoding="utf-8")
    orchestrator = await build_default_orchestrator(require_api_key=False)
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(
        _manager_with_routing({
            "active_model": _ROUTED_TEST_ALIAS,
            "model_config": _ROUTING_MODEL_CONFIG,
        }).config_variants()
    )

    await orchestrator.reload()

    config = orchestrator.config
    assert config.active_model == _ROUTED_TEST_ALIAS
    assert _ROUTED_TEST_ALIAS in config.models
    assert config.get_active_model().alias == _ROUTED_TEST_ALIAS


@pytest.mark.asyncio
async def test_maps_extra_models_experiment_to_routed_extra_models() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(
        _manager_with_extra_models({
            "models": [_ROUTING_MODEL_CONFIG]
        }).config_variants()
    )

    data = await layer.load()

    dumped = data.model_dump()
    assert json.loads(dumped["routed_extra_models"]) == [_ROUTING_MODEL_CONFIG]
    # Add-only: the exposure experiment never sets the routed default.
    assert "routed_default_model" not in dumped


@pytest.mark.asyncio
async def test_maps_extra_models_from_bare_list_payload() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(
        _manager_with_extra_models([_ROUTING_MODEL_CONFIG]).config_variants()
    )

    data = await layer.load()

    assert json.loads(data.model_dump()["routed_extra_models"]) == [
        _ROUTING_MODEL_CONFIG
    ]


@pytest.mark.asyncio
async def test_ignores_extra_models_payload_without_models() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(_manager_with_extra_models({"other": "value"}).config_variants())

    data = await layer.load()

    assert data.model_dump() == {}


@pytest.mark.asyncio
async def test_extra_models_experiment_adds_to_dropdown_without_default(
    config_dir: Path,
) -> None:
    (config_dir / "config.toml").write_text("", encoding="utf-8")
    orchestrator = await build_default_orchestrator(require_api_key=False)
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    default_before = orchestrator.config.resolve_default_model_alias()
    layer.set_variants(
        _manager_with_extra_models({
            "models": [_ROUTING_MODEL_CONFIG]
        }).config_variants()
    )

    await orchestrator.reload()

    config = orchestrator.config
    assert config.active_model == ""  # unpinned
    assert _ROUTED_TEST_ALIAS in config.models
    assert _ROUTED_TEST_ALIAS in config.available_models()
    # The default is untouched by the exposure experiment.
    assert config.resolve_default_model_alias() == default_before
    assert config.get_active_model().alias == default_before


@pytest.mark.asyncio
async def test_copied_orchestrator_keeps_growthbook_variant_after_reload() -> None:
    orchestrator = await build_default_orchestrator(require_api_key=False)
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(_manager_with_variant("tests").config_variants())
    await orchestrator.reload()

    copied = orchestrator.copy()

    failures = await copied.set_field(
        "/include_model_info",
        False,
        reason="exercise copied orchestrator reload",
        target_layer=OverridesLayer.NAME,
    )

    assert failures == []
    assert copied.config.system_prompt_id == "tests"
