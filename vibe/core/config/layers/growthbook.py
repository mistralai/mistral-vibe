from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from typing import Final

from vibe.core.config.fingerprint import create_dict_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.types import EMPTY_CONFIG_SNAPSHOT, LayerConfigSnapshot
from vibe.core.experiments.active import ExperimentName

type GrowthbookConfigMapper = Callable[[str], str | bool | None]


def _map_system_prompt_variant(variant: str) -> str | None:
    # Imported lazily: this runs only once GrowthBook variants have been fetched
    # (post first paint), so pulling vibe.core.prompts (~15ms) at module import
    # would needlessly weigh down the pre-paint config-stack import.
    from vibe.core.prompts import load_system_prompt

    try:
        load_system_prompt(variant)
    except ValueError:
        return None
    return variant


def _map_default_routing_model(variant: str) -> str | None:
    try:
        payload = json.loads(variant)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    active_model = payload.get("active_model")
    return active_model if isinstance(active_model, str) and active_model else None


def _map_routed_model_config(variant: str) -> str | None:
    try:
        payload = json.loads(variant)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    model_config = payload.get("model_config")
    if not isinstance(model_config, dict):
        return None
    return json.dumps(model_config)


def _map_routed_extra_models(variant: str) -> str | None:
    """Extract the extra-models list from the exposure experiment payload.

    Accepts either a bare JSON array of model definitions or an object with a
    ``models`` array. Returns a JSON-array string of model-definition objects,
    or ``None`` when the payload carries no models.
    """
    try:
        payload = json.loads(variant)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(payload, dict):
        payload = payload.get("models")
    if not isinstance(payload, list):
        return None
    models = [model for model in payload if isinstance(model, dict)]
    if not models:
        return None
    return json.dumps(models)


GROWTHBOOK_CONFIG_MAPPINGS: Final[
    dict[ExperimentName, tuple[tuple[str, GrowthbookConfigMapper], ...]]
] = {
    ExperimentName.SYSTEM_PROMPT: (("system_prompt_id", _map_system_prompt_variant),),
    ExperimentName.CLI_MODEL_ROUTING: (
        ("routed_default_model", _map_default_routing_model),
        ("routed_model_config", _map_routed_model_config),
    ),
    ExperimentName.CLI_EXTRA_MODELS: (
        ("routed_extra_models", _map_routed_extra_models),
    ),
    ExperimentName.MANAGED_SHELL_TOOLS: (
        (
            "managed_shell_tools_enabled",
            lambda variant: True if variant == "managed" else None,
        ),
    ),
}


class GrowthbookLayer(ConfigLayer[RawConfig]):
    NAME = "growthbook"

    def __init__(self, *, name: str = NAME) -> None:
        super().__init__(name=name)
        self._variants: dict[str, str] = {}

    def set_variants(self, variants: Mapping[str, str]) -> None:
        """Receive config-scoped variants, not telemetry assignments."""
        self._variants = dict(variants)

    async def _check_trust(self) -> bool:
        return True

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        if not self._variants:
            return EMPTY_CONFIG_SNAPSHOT

        data: dict[str, str | bool] = {}
        for experiment_name, field_mappers in GROWTHBOOK_CONFIG_MAPPINGS.items():
            variant = self._variants.get(experiment_name.value)
            if variant is None:
                continue
            for config_field, map_variant in field_mappers:
                mapped_value = map_variant(variant)
                if mapped_value is not None:
                    data[config_field] = mapped_value

        if not data:
            return EMPTY_CONFIG_SNAPSHOT

        fingerprint = create_dict_fingerprint(data)

        return LayerConfigSnapshot(data=data, fingerprint=fingerprint)

    async def _save_to_store(self, _next_config: RawConfig) -> str:
        raise NotImplementedError("GrowthbookLayer is read-only")
