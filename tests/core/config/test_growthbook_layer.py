from __future__ import annotations

import json
import logging
from pathlib import Path
import tomllib
from typing import Any

import pytest
import tomli_w

from vibe.core.config import ConfigLayer, RawConfig, build_default_orchestrator
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.models import ModelRequirement
from vibe.core.experiments.active import ExperimentName
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import (
    ExperimentManager,
    config_variants_from_response,
)
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


_LOCAL_MODEL = {"name": "my-local-model", "provider": "llamacpp", "alias": "my-local"}


async def _config_with_variants(
    config_dir: Path, variants: ExperimentManager, **user_fields: Any
) -> Any:
    config_path = config_dir / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    config.pop("models", None)
    config["active_model"] = ""
    config.update(user_fields)
    config_path.write_text(tomli_w.dumps(config), encoding="utf-8")
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(variants.config_variants())
    await orchestrator.reload()
    return orchestrator.config


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


def _response_forcing_registry_skills(variant: str) -> EvalResponse:
    return EvalResponse.model_validate({
        "features": {
            ExperimentName.REGISTRY_SKILLS.value: {
                "defaultValue": "off",
                "rules": [
                    {
                        "force": variant,
                        "tracks": [
                            {
                                "experiment": {
                                    "key": ExperimentName.REGISTRY_SKILLS.value
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


def _manager_with_registry_skills_variant(variant: str) -> ExperimentManager:
    manager = ExperimentManager(client=_StubClient(None))
    manager.hydrate(_response_forcing_registry_skills(variant))
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


def _manager_with_system_prompt_behavior(
    text: object, *, variant: str | None = None
) -> ExperimentManager:
    features: dict[str, Any] = {
        ExperimentName.SYSTEM_PROMPT_BEHAVIOR.value: {
            "defaultValue": "",
            "rules": [{"force": text}],
        }
    }
    if variant is not None:
        features[ExperimentName.SYSTEM_PROMPT.value] = {
            "defaultValue": "cli",
            "rules": [{"force": variant}],
        }
    manager = ExperimentManager(client=_StubClient(None))
    manager.hydrate(EvalResponse.model_validate({"features": features}))
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
async def test_maps_ml4_system_prompt_experiment_variant_to_config_field() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(_manager_with_variant("cli_ml4_2026-10_v2").config_variants())

    data = await layer.load()

    assert data.model_dump() == {"system_prompt_id": "cli_ml4_2026-10_v2"}


@pytest.mark.asyncio
async def test_maps_managed_shell_experiment_to_config_field() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(_manager_with_managed_shell_variant("managed").config_variants())

    data = await layer.load()

    assert data.model_dump() == {"managed_shell_tools_enabled": True}


@pytest.mark.asyncio
async def test_maps_smart_approve_available_experiment_to_config_field() -> None:
    layer = GrowthbookLayer()
    layer.set_variants({ExperimentName.SMART_APPROVE.value: "on"})

    data = await layer.load()

    assert data.model_dump() == {"smart_approve_available": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("agent", ["ask", "accept-edits", "smart-approve"])
async def test_maps_default_agent_experiment_to_config_field(agent: str) -> None:
    layer = GrowthbookLayer()
    layer.set_variants({ExperimentName.DEFAULT_AGENT.value: agent})

    data = await layer.load()

    assert data.model_dump() == {"default_agent": agent}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant", ["", "auto-approve", "plan", "explore", "lean", "nope", True]
)
async def test_default_agent_experiment_ignores_values_it_may_not_roll_out(
    variant: object,
) -> None:
    layer = GrowthbookLayer()
    layer.set_variants({ExperimentName.DEFAULT_AGENT.value: variant})

    data = await layer.load()

    assert data.model_dump() == {}


@pytest.mark.asyncio
async def test_maps_mistral_mcp_connector_experiment_to_config_field() -> None:
    layer = GrowthbookLayer()
    layer.set_variants({ExperimentName.MISTRAL_MCP_CONNECTOR.value: True})

    data = await layer.load()

    assert data.model_dump() == {"experimental_enable_mistral_mcp_connector": True}


@pytest.mark.asyncio
async def test_maps_boolean_true_variant_to_smart_approve() -> None:
    layer = GrowthbookLayer()
    layer.set_variants({ExperimentName.SMART_APPROVE.value: "true"})

    data = await layer.load()

    assert data.model_dump() == {"smart_approve_available": True}


@pytest.mark.asyncio
async def test_boolean_false_variant_leaves_smart_approve_unset() -> None:
    layer = GrowthbookLayer()
    layer.set_variants({ExperimentName.SMART_APPROVE.value: "false"})

    data = await layer.load()

    assert data.model_dump() == {}


@pytest.mark.asyncio
async def test_default_value_rollout_sets_smart_approve_end_to_end() -> None:
    response = EvalResponse.model_validate({
        "features": {
            ExperimentName.SMART_APPROVE.value: {"defaultValue": True, "rules": []},
            ExperimentName.DEFAULT_AGENT.value: {
                "defaultValue": "smart-approve",
                "rules": [],
            },
        }
    })
    layer = GrowthbookLayer()
    layer.set_variants(config_variants_from_response(response))

    data = await layer.load()

    assert data.model_dump() == {
        "smart_approve_available": True,
        "default_agent": "smart-approve",
    }


@pytest.mark.asyncio
async def test_object_default_value_applies_routing_end_to_end() -> None:
    routing = {
        "active_model": "glm-5-2",
        "model_config": {
            "name": "zai-glm-5-2",
            "provider": "mistral",
            "alias": "glm-5-2",
            "input_price": "1.4",
            "output_price": "4.4",
            "supports_images": False,
        },
    }
    response = EvalResponse.model_validate({
        "features": {
            ExperimentName.CLI_MODEL_ROUTING.value: {
                "defaultValue": routing,
                "rules": [],
            }
        }
    })
    layer = GrowthbookLayer()
    layer.set_variants(config_variants_from_response(response))

    data = (await layer.load()).model_dump()

    assert data["routed_default_model"] == "glm-5-2"
    assert "glm-5-2" in data["models"]


@pytest.mark.asyncio
async def test_maps_registry_skills_experiment_to_config_field() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(_manager_with_registry_skills_variant("on").config_variants())

    data = await layer.load()

    assert data.model_dump() == {"experimental_enable_registry_skills": True}


@pytest.mark.asyncio
async def test_ignores_registry_skills_off_variant() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(_manager_with_registry_skills_variant("off").config_variants())

    data = await layer.load()

    assert data.model_dump() == {}


@pytest.mark.asyncio
async def test_selected_toml_disables_the_growthbook_model_catalog(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text(
        "experimental_enable_model_catalog = false\n", encoding="utf-8"
    )
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants({ExperimentName.MODEL_CATALOG.value: True})

    await orchestrator.reload()

    assert orchestrator.config.experimental_enable_model_catalog is False


@pytest.mark.asyncio
async def test_growthbook_enables_the_model_catalog() -> None:
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants({ExperimentName.MODEL_CATALOG.value: True})

    await orchestrator.reload()

    assert orchestrator.config.experimental_enable_model_catalog is True


def _model_experiments(catalog_variant: object) -> dict[str, object]:
    return {
        ExperimentName.CLI_MODEL_ROUTING.value: json.dumps({"active_model": "glm-5-2"}),
        ExperimentName.CLI_EXTRA_MODELS.value: json.dumps([
            {"name": "glm-5-3", "provider": "mistral", "alias": "glm-5-3"}
        ]),
        ExperimentName.REGISTRY_SKILLS.value: True,
        ExperimentName.MODEL_CATALOG.value: catalog_variant,
    }


@pytest.mark.parametrize("variant", [True, "on"])
@pytest.mark.asyncio
async def test_catalog_flag_drops_only_the_model_experiments(variant: object) -> None:
    layer = GrowthbookLayer()
    layer.set_variants(_model_experiments(variant))

    data = await layer.load()

    assert data.model_dump() == {
        "experimental_enable_model_catalog": True,
        "experimental_enable_registry_skills": True,
    }


@pytest.mark.parametrize("variant", [False, "off"])
@pytest.mark.asyncio
async def test_catalog_flag_off_keeps_the_model_experiments(variant: object) -> None:
    layer = GrowthbookLayer()
    layer.set_variants(_model_experiments(variant))

    data = await layer.load()

    dumped = data.model_dump()
    assert dumped["routed_default_model"] == "glm-5-2"
    assert "glm-5-3" in dumped["models"]
    assert "experimental_enable_model_catalog" not in dumped


@pytest.mark.asyncio
async def test_smart_approve_off_variant_maps_nothing() -> None:
    layer = GrowthbookLayer()
    layer.set_variants({ExperimentName.SMART_APPROVE.value: "off"})

    data = await layer.load()

    assert data.model_dump() == {}


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
async def test_growthbook_default_value_equal_to_baseline_not_applied() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(_manager_with_default_value("cli").config_variants())

    data = await layer.load()

    assert data.model_dump() == {}


@pytest.mark.asyncio
async def test_growthbook_differing_default_value_is_applied() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(_manager_with_default_value("tests").config_variants())

    data = await layer.load()

    assert data.model_dump() == {"system_prompt_id": "tests"}


@pytest.mark.asyncio
async def test_default_orchestrator_applies_growthbook_layer() -> None:
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(_manager_with_variant("tests").config_variants())

    await orchestrator.reload()

    assert orchestrator.config.system_prompt_id == "tests"


@pytest.mark.asyncio
async def test_selected_toml_wins_over_growthbook_layer(config_dir: Path) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text('system_prompt_id = "lean"\n', encoding="utf-8")
    orchestrator = await build_default_orchestrator()
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
    orchestrator = await build_default_orchestrator()
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
    orchestrator = await build_default_orchestrator()
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
    orchestrator = await build_default_orchestrator()
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
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(_manager_with_default_value("tests").config_variants())

    await orchestrator.reload()

    assert orchestrator.config.system_prompt_id == "lean"


@pytest.mark.asyncio
async def test_unknown_system_prompt_variant_does_not_break_reload() -> None:
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(
        _manager_with_variant("removed_after_graduation_2025_07").config_variants()
    )

    await orchestrator.reload()

    assert orchestrator.config.system_prompt_id == "cli"


@pytest.mark.asyncio
async def test_runtime_overrides_win_over_growthbook_layer() -> None:
    orchestrator = await build_default_orchestrator({"system_prompt_id": "lean"})
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
    orchestrator = await build_default_orchestrator()
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
    config = await _config_with_variants(
        config_dir,
        _manager_with_routing({
            "active_model": _ROUTED_TEST_ALIAS,
            "model_config": _ROUTING_MODEL_CONFIG,
        }),
        active_model="my-local",
        models=[_LOCAL_MODEL],
    )

    assert config.active_model == "my-local"
    assert config.get_active_model().alias == "my-local"
    assert _ROUTED_TEST_ALIAS in config.models


@pytest.mark.asyncio
async def test_routing_experiment_honors_manual_selection_of_routed_model(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text(f'active_model = "{_ROUTED_TEST_ALIAS}"\n', encoding="utf-8")
    orchestrator = await build_default_orchestrator()
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


@pytest.mark.parametrize(
    ("experiment", "payload"),
    [
        (
            ExperimentName.CLI_MODEL_ROUTING,
            {"active_model": "incomplete", "model_config": {"alias": "incomplete"}},
        ),
        (ExperimentName.CLI_EXTRA_MODELS, [{"alias": "incomplete"}]),
    ],
)
@pytest.mark.asyncio
async def test_incomplete_growthbook_model_does_not_break_reload(
    experiment: ExperimentName, payload: object
) -> None:
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    default_alias = orchestrator.config.get_active_model().alias
    layer.set_variants({experiment.value: payload})

    await orchestrator.reload()

    assert "incomplete" not in orchestrator.config.models
    assert orchestrator.config.get_active_model().alias == default_alias


@pytest.mark.parametrize(
    ("experiment", "payload"),
    [
        (
            ExperimentName.CLI_MODEL_ROUTING,
            {
                "active_model": "",
                "model_config": {"name": "empty", "provider": "mistral", "alias": ""},
            },
        ),
        (
            ExperimentName.CLI_EXTRA_MODELS,
            [{"name": "empty", "provider": "mistral", "alias": ""}],
        ),
    ],
)
@pytest.mark.asyncio
async def test_empty_alias_growthbook_model_does_not_break_reload(
    experiment: ExperimentName, payload: object
) -> None:
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    default_alias = orchestrator.config.get_active_model().alias
    layer.set_variants({experiment.value: payload})

    await orchestrator.reload()

    assert "" not in orchestrator.config.models
    assert orchestrator.config.get_active_model().alias == default_alias


@pytest.mark.asyncio
async def test_extra_models_experiment_adds_to_dropdown_without_default(
    config_dir: Path,
) -> None:
    (config_dir / "config.toml").write_text("", encoding="utf-8")
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
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
    assert config.routed_default_model == ""
    assert config.get_active_model().alias == _ROUTED_TEST_ALIAS


_LOCKED = {"kind": "higher_plan"}


@pytest.mark.asyncio
async def test_extra_models_experiment_lists_a_locked_model_without_default(
    config_dir: Path,
) -> None:
    config = await _config_with_variants(
        config_dir,
        _manager_with_extra_models({
            "models": [{**_ROUTING_MODEL_CONFIG, "requires": _LOCKED}]
        }),
        models=[_LOCAL_MODEL],
    )

    assert config.available_models()[_ROUTED_TEST_ALIAS].requires == ModelRequirement(
        kind="higher_plan"
    )
    assert config.resolve_default_model_alias() == "my-local"


@pytest.mark.asyncio
async def test_routing_experiment_never_defaults_to_a_locked_model(
    config_dir: Path,
) -> None:
    config = await _config_with_variants(
        config_dir,
        _manager_with_routing({
            "active_model": _ROUTED_TEST_ALIAS,
            "model_config": {**_ROUTING_MODEL_CONFIG, "requires": _LOCKED},
        }),
        models=[_LOCAL_MODEL],
    )

    assert config.models[_ROUTED_TEST_ALIAS].requires is not None
    assert config.resolve_default_model_alias() == "my-local"
    assert config.get_active_model().alias == "my-local"


@pytest.mark.parametrize(
    "requires", [{"kind": "unknown_kind"}, "yes", {}, [], False, True]
)
@pytest.mark.asyncio
async def test_extra_models_experiment_hides_a_model_it_cannot_upsell(
    config_dir: Path, requires: object, caplog: pytest.LogCaptureFixture
) -> None:
    (config_dir / "config.toml").write_text("", encoding="utf-8")
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(
        _manager_with_extra_models({
            "models": [{**_ROUTING_MODEL_CONFIG, "requires": requires}]
        }).config_variants()
    )

    with caplog.at_level(logging.INFO):
        await orchestrator.reload()

    assert _ROUTED_TEST_ALIAS not in orchestrator.config.models
    assert (
        f"GrowthBook model '{_ROUTED_TEST_ALIAS}' skipped: requires {requires!r}"
        in caplog.messages
    )


@pytest.mark.asyncio
async def test_an_invalid_locked_model_is_not_blamed_on_its_requirement(
    config_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (config_dir / "config.toml").write_text("", encoding="utf-8")
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    invalid = {**_ROUTING_MODEL_CONFIG, "temperature": "hot", "requires": _LOCKED}
    layer.set_variants(
        _manager_with_extra_models({"models": [invalid]}).config_variants()
    )

    with caplog.at_level(logging.INFO):
        await orchestrator.reload()

    assert _ROUTED_TEST_ALIAS not in orchestrator.config.models
    assert not any("skipped: requires" in message for message in caplog.messages)


@pytest.mark.asyncio
async def test_extra_models_experiment_ignores_the_requires_upgrade_flag(
    config_dir: Path,
) -> None:
    (config_dir / "config.toml").write_text("", encoding="utf-8")
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(
        _manager_with_extra_models({
            "models": [{**_ROUTING_MODEL_CONFIG, "requires_upgrade": True}]
        }).config_variants()
    )

    await orchestrator.reload()

    assert orchestrator.config.models[_ROUTED_TEST_ALIAS].requires is None


@pytest.mark.parametrize(
    "user_requires", [None, _LOCKED, {"kind": "unknown_kind"}], ids=str
)
@pytest.mark.asyncio
async def test_a_user_config_neither_sets_nor_lifts_a_lock(
    config_dir: Path, user_requires: object
) -> None:
    locked = {**_ROUTING_MODEL_CONFIG, "requires": _LOCKED}
    free = {**_ROUTING_MODEL_CONFIG, "alias": "free-model", "name": "free-model"}
    user_entry: dict[str, Any] = {"temperature": 0.5}
    if user_requires is not None:
        user_entry["requires"] = user_requires
    (config_dir / "config.toml").write_text(
        tomli_w.dumps({
            "models": {_ROUTED_TEST_ALIAS: user_entry, "free-model": user_entry}
        }),
        encoding="utf-8",
    )
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(
        _manager_with_extra_models({"models": [locked, free]}).config_variants()
    )

    await orchestrator.reload()

    models = orchestrator.config.models
    assert models[_ROUTED_TEST_ALIAS].requires == ModelRequirement(kind="higher_plan")
    assert models[_ROUTED_TEST_ALIAS].temperature == 0.5
    assert models["free-model"].requires is None


@pytest.mark.asyncio
async def test_copied_orchestrator_keeps_growthbook_variant_after_reload() -> None:
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(_manager_with_variant("tests").config_variants())
    await orchestrator.reload()

    copied = orchestrator.copy()

    failures = await copied.set_field(
        "/include_commit_signature",
        False,
        reason="exercise copied orchestrator reload",
        target_layer=OverridesLayer.NAME,
    )

    assert failures == []
    assert copied.config.system_prompt_id == "tests"


@pytest.mark.asyncio
async def test_routing_accepts_json_string_variant() -> None:
    # A GrowthBook "string"-typed feature serves its value as JSON text; the
    # routing mappers must accept that as well as a native object.
    layer = GrowthbookLayer()
    layer.set_variants({
        ExperimentName.CLI_MODEL_ROUTING.value: '{"active_model": "routed-x"}'
    })

    data = await layer.load()

    assert data.model_dump()["routed_default_model"] == "routed-x"


_SESSION_API_ENV = "VIBE_EXPERIMENTAL_LOCAL_RUNTIME_SESSION_API"


async def _session_api_switch(
    monkeypatch: pytest.MonkeyPatch, *, flag: object | None, env: str | None
) -> bool:
    if env is None:
        monkeypatch.delenv(_SESSION_API_ENV, raising=False)
    else:
        monkeypatch.setenv(_SESSION_API_ENV, env)
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    if flag is not None:
        layer.set_variants({ExperimentName.LOCAL_RUNTIME_SESSION_API.value: flag})
    await orchestrator.reload()
    return orchestrator.config.experimental_local_runtime_session_api


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flag", "env", "expected"),
    [
        (None, None, False),
        (True, None, True),
        ("on", None, True),
        (False, None, False),
        (True, "false", False),
        (False, "true", True),
        (None, "true", True),
    ],
)
async def test_the_session_api_switch_follows_its_flag_unless_the_environment_sets_it(
    monkeypatch: pytest.MonkeyPatch,
    flag: object | None,
    env: str | None,
    expected: bool,
) -> None:
    """*Prepare*: The migration flag and its environment variable, each set or not.
    *Do*: Resolve the configuration.
    *Assert*: The switch is off by default, follows the flag, and follows the
        environment variable whenever it is set.
    """
    # Do
    switch = await _session_api_switch(monkeypatch, flag=flag, env=env)

    # Assert
    assert switch is expected


@pytest.mark.asyncio
async def test_maps_storage_usage_experiment_to_config_field() -> None:
    layer = GrowthbookLayer()
    layer.set_variants({ExperimentName.STORAGE_USAGE.value: True})

    data = await layer.load()

    assert data.model_dump() == {"experimental_enable_storage_usage": True}


@pytest.mark.asyncio
async def test_ignores_storage_usage_off_variant() -> None:
    layer = GrowthbookLayer()
    layer.set_variants({ExperimentName.STORAGE_USAGE.value: False})

    data = await layer.load()

    assert data.model_dump() == {}


@pytest.mark.asyncio
async def test_maps_system_prompt_behavior_experiment_to_config_field() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(
        _manager_with_system_prompt_behavior(
            "  You are a remote prompt.\n", variant="cli_ml4_2026-10_v2"
        ).config_variants()
    )

    data = await layer.load()

    assert data.model_dump() == {
        "system_prompt_id": "cli_ml4_2026-10_v2",
        "system_prompt_behavior": "You are a remote prompt.",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   \n", 42, {"prompt": "x"}])
async def test_ignores_blank_or_non_string_system_prompt_behavior(text: object) -> None:
    layer = GrowthbookLayer()
    layer.set_variants(_manager_with_system_prompt_behavior(text).config_variants())

    data = await layer.load()

    assert data.model_dump() == {}


@pytest.mark.asyncio
async def test_default_orchestrator_applies_growthbook_behavior() -> None:
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(
        _manager_with_system_prompt_behavior(
            "You are a remote prompt."
        ).config_variants()
    )

    await orchestrator.reload()

    assert orchestrator.config.system_prompt_behavior == "You are a remote prompt."
    assert (
        orchestrator.config.origin_of("system_prompt_behavior") == GrowthbookLayer.NAME
    )


_LARGE_4_EXTRA = {
    "name": "mistral-large-4-0",
    "display_name": "Mistral Large 4",
    "provider": "mistral",
    "alias": "mistral-large-4-0-3",
    "temperature": "1",
    "auto_compact_threshold": 400000,
    "thinking_levels": ["low", "medium", "high"],
    "supports_images": True,
    "thinking": "high",
}
_FLASH_EXTRA = {
    "name": "zai-glm-5-3-flash-vibe-free",
    "provider": "mistral",
    "alias": "zai-glm-5-3-flash",
}


async def _config_with_extra_models(
    config_dir: Path, extra: list[dict[str, Any]], **user_fields: Any
) -> Any:
    return await _config_with_variants(
        config_dir, _manager_with_extra_models({"models": extra}), **user_fields
    )


@pytest.mark.asyncio
async def test_growthbook_models_replace_the_built_in_models(config_dir: Path) -> None:
    config = await _config_with_extra_models(config_dir, [_LARGE_4_EXTRA])

    assert list(config.available_models()) == ["mistral-large-4-0-3"]
    assert config.get_active_model().alias == "mistral-large-4-0-3"
    assert config.validation_warnings == ()


@pytest.mark.asyncio
async def test_growthbook_large_4_under_the_built_in_alias_is_one_row(
    config_dir: Path,
) -> None:
    flag_model = {**_LARGE_4_EXTRA, "alias": "mistral-large-4-0"}

    config = await _config_with_extra_models(config_dir, [flag_model])

    assert list(config.available_models()) == ["mistral-large-4-0"]
    assert config.models["mistral-large-4-0"].input_price == 0.0


@pytest.mark.asyncio
async def test_a_pin_on_the_old_large_4_alias_runs_the_growthbook_model(
    config_dir: Path,
) -> None:
    config = await _config_with_extra_models(
        config_dir, [_FLASH_EXTRA, _LARGE_4_EXTRA], active_model="mistral-large-4-0-3"
    )

    assert config.get_active_model().alias == "mistral-large-4-0-3"
    assert config.validation_warnings == ()


@pytest.mark.asyncio
async def test_a_pin_on_a_built_in_model_falls_back_while_growthbook_lists_models(
    config_dir: Path,
) -> None:
    config = await _config_with_extra_models(
        config_dir, [_FLASH_EXTRA], active_model="mistral-medium-3.5"
    )

    assert config.get_active_model().alias == "zai-glm-5-3-flash"
    assert any(
        "Model 'mistral-medium-3.5' is not available" in warning
        for warning in config.validation_warnings
    )


@pytest.mark.asyncio
async def test_growthbook_models_leave_user_models_available(config_dir: Path) -> None:
    config = await _config_with_extra_models(
        config_dir, [_LARGE_4_EXTRA], models=[_LOCAL_MODEL]
    )

    assert set(config.available_models()) == {"mistral-large-4-0-3", "my-local"}


@pytest.mark.asyncio
async def test_a_routed_default_alone_replaces_the_built_in_models(
    config_dir: Path,
) -> None:
    config = await _config_with_variants(
        config_dir,
        _manager_with_routing({
            "active_model": _ROUTED_TEST_ALIAS,
            "model_config": _ROUTING_MODEL_CONFIG,
        }),
    )

    assert list(config.available_models()) == [_ROUTED_TEST_ALIAS]
