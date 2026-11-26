from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from vibe.core.config import ConfigLayer, RawConfig, build_default_orchestrator
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.vibe_schema import DEFAULT_MODELS
from vibe.core.experiments.active import ExperimentName
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import (
    ExperimentManager,
    config_variants_from_response,
)
from vibe.core.experiments.models import EvalResponse, ExperimentAttributes


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


def _manager_with_variant(variant: str) -> ExperimentManager:
    manager = ExperimentManager(client=_StubClient(None))
    manager.hydrate(_response_forcing(variant))
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


_SESSION_API_ENV = "VIBE_EXPERIMENTAL_LOCAL_RUNTIME_SESSION_API"
_RETIRED_SESSION_API_FLAG = "vibe_cli_local_runtime_session_api_migration"


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
        layer.set_variants({_RETIRED_SESSION_API_FLAG: flag})
    await orchestrator.reload()
    return orchestrator.config.experimental_local_runtime_session_api


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("flag", "env", "expected"),
    [
        (None, None, True),
        (False, None, True),
        ("off", None, True),
        (None, "false", False),
        (True, "false", False),
        (None, "true", True),
    ],
)
async def test_the_session_api_switch_is_on_unless_the_environment_turns_it_off(
    monkeypatch: pytest.MonkeyPatch,
    flag: object | None,
    env: str | None,
    expected: bool,
) -> None:
    """*Prepare*: The retired migration flag and the environment variable, each
        set or not.
    *Do*: Resolve the configuration.
    *Assert*: The switch is on by default, ignores the retired flag, and
        follows the environment variable whenever it is set.
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


# Flags Vibe used to read for its models: GrowthBook may still serve them.
_RETIRED_MODEL_FLAGS: dict[str, object] = {
    "vibe_cli_extra_models": {
        "models": [{"name": "glm-5-3", "provider": "mistral", "alias": "glm-5-3"}]
    },
    "vibe_cli_default_routing_model": {
        "active_model": "glm-5-2",
        "model_config": {
            "name": "zai-glm-5-2",
            "provider": "mistral",
            "alias": "glm-5-2",
        },
    },
    "vibe_cli_model_catalog": True,
}


@pytest.mark.asyncio
async def test_the_retired_model_flags_map_nothing() -> None:
    layer = GrowthbookLayer()
    layer.set_variants(_RETIRED_MODEL_FLAGS)

    data = await layer.load()

    assert data.model_dump() == {}


@pytest.mark.asyncio
async def test_the_retired_model_flags_keep_the_built_in_models(
    config_dir: Path,
) -> None:
    (config_dir / "config.toml").write_text("", encoding="utf-8")
    orchestrator = await build_default_orchestrator()
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(_RETIRED_MODEL_FLAGS)

    await orchestrator.reload()

    config = orchestrator.config
    assert list(config.available_models()) == [model.alias for model in DEFAULT_MODELS]
    assert config.get_active_model().alias == DEFAULT_MODELS[0].alias
    assert config.origin_of("enable_model_catalog") != GrowthbookLayer.NAME
