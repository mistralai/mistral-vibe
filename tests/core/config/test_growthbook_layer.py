from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.config import ConfigLayer, RawConfig, build_default_orchestrator
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.experiments.active import ExperimentName
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import ExperimentManager
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
async def test_growthbook_layer_wins_over_selected_toml(config_dir: Path) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text('system_prompt_id = "lean"\n', encoding="utf-8")
    orchestrator = await build_default_orchestrator(require_api_key=False)
    layer = _require_growthbook_layer(orchestrator.get_layer(GrowthbookLayer.NAME))
    layer.set_variants(_manager_with_variant("tests").config_variants())

    await orchestrator.reload()

    assert orchestrator.config.system_prompt_id == "tests"


@pytest.mark.asyncio
async def test_forced_growthbook_variant_without_tracks_wins_over_selected_toml(
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

    assert orchestrator.config.system_prompt_id == "tests"


@pytest.mark.asyncio
async def test_forced_growthbook_variant_not_in_experiment_wins_over_selected_toml(
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

    assert orchestrator.config.system_prompt_id == "tests"


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
