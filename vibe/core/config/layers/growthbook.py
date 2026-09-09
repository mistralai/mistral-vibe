from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from typing import Any, Final

from pydantic import ValidationError

from vibe.core.config.fingerprint import create_dict_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.models import ModelConfig
from vibe.core.config.types import EMPTY_CONFIG_SNAPSHOT, LayerConfigSnapshot
from vibe.core.experiments.active import ExperimentName

type GrowthbookConfigMapper = Callable[[object], str | bool | None]


_ON_TOKENS = frozenset({"on", "true"})  # accept boolean and string authoring


def _map_on(value: object) -> bool | None:
    """Map a boolean rollout flag to True, or None when off/unset."""
    if value is True:
        return True
    if isinstance(value, str) and value.strip().lower() in _ON_TOKENS:
        return True
    return None


def _map_system_prompt_variant(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    # Lazy import: keeps vibe.core.prompts off the pre-paint config-stack import.
    from vibe.core.prompts import load_system_prompt

    try:
        load_system_prompt(value)
    except ValueError:
        return None
    return value


def _as_json_value(value: object) -> object:
    """Parse a JSON-string value; pass typed (json-feature) values through."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _map_default_routing_model(value: object) -> str | None:
    payload = _as_json_value(value)
    if not isinstance(payload, dict):
        return None
    active_model = payload.get("active_model")
    return active_model if isinstance(active_model, str) and active_model else None


def _map_routed_model_config(value: object) -> str | None:
    payload = _as_json_value(value)
    if not isinstance(payload, dict):
        return None
    model_config = payload.get("model_config")
    if not isinstance(model_config, dict):
        return None
    return json.dumps(model_config)


def _map_routed_extra_models(value: object) -> str | None:
    """Extract the extra-models list from the exposure payload.

    Accepts a bare list of model definitions or an object with a ``models``
    array. Returns a JSON-array string, or None when there are no models.
    """
    payload = _as_json_value(value)
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
            lambda value: (
                True
                if value is True
                or (
                    isinstance(value, str)
                    and value.strip().lower() in {"managed", "true"}
                )
                else None
            ),
        ),
    ),
    ExperimentName.SMART_APPROVE: (("smart_approve_available", _map_on),),
    ExperimentName.SMART_APPROVE_DEFAULT: (("smart_approve_default", _map_on),),
    ExperimentName.REGISTRY_SKILLS: (("experimental_enable_registry_skills", _map_on),),
}


class GrowthbookLayer(ConfigLayer[RawConfig]):
    NAME = "growthbook"

    def __init__(self, *, name: str = NAME) -> None:
        super().__init__(name=name)
        self._variants: dict[str, object] = {}

    def set_variants(self, variants: Mapping[str, object]) -> None:
        """Receive config-scoped variants, not telemetry assignments."""
        self._variants = dict(variants)

    async def _check_trust(self) -> bool:
        return True

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        if not self._variants:
            return EMPTY_CONFIG_SNAPSHOT

        data: dict[str, Any] = {}
        for experiment_name, field_mappers in GROWTHBOOK_CONFIG_MAPPINGS.items():
            variant = self._variants.get(experiment_name.value)
            if variant is None:
                continue
            for config_field, map_variant in field_mappers:
                mapped_value = map_variant(variant)
                if mapped_value is not None:
                    data[config_field] = mapped_value

        models = _routed_models(data)
        if models:
            # Expose routed models through the canonical deep-merged field as
            # well as the compatibility routing fields. Higher-priority user
            # layers can then override them sparsely, and config provenance can
            # address their fields like every other model.
            data["models"] = models

        if not data:
            return EMPTY_CONFIG_SNAPSHOT

        fingerprint = create_dict_fingerprint(data)

        return LayerConfigSnapshot(data=data, fingerprint=fingerprint)

    async def _save_to_store(self, _next_config: RawConfig) -> str:
        raise NotImplementedError("GrowthbookLayer is read-only")


def _routed_models(data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    models: dict[str, dict[str, Any]] = {}
    for value in (data.get("routed_model_config"), data.get("routed_extra_models")):
        if not isinstance(value, str):
            continue
        decoded = json.loads(value)
        entries = decoded if isinstance(decoded, list) else [decoded]
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                model = ModelConfig.model_validate(entry)
            except ValidationError:
                continue
            if not model.alias:
                continue
            models[model.alias] = entry
    return models
