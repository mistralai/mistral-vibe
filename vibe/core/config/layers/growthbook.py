from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from typing import Any, Final

from vibe.core.agents.models import ApprovalPolicyName
from vibe.core.config._catalog_filter import CATALOG_FILTER_KEY, parse_catalog_filter
from vibe.core.config.fingerprint import create_dict_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.types import EMPTY_CONFIG_SNAPSHOT, LayerConfigSnapshot
from vibe.core.experiments.active import ExperimentName

type GrowthbookConfigMapper = Callable[[object], str | bool | None]


_ON_TOKENS = frozenset({"on", "true"})  # accept boolean and string authoring

# An experiment never moves a cohort into auto-approve, which skips every
# permission prompt.
_DEFAULT_AGENT_ROLLOUT_MODES: Final = frozenset({
    ApprovalPolicyName.ASK,
    ApprovalPolicyName.ACCEPT_EDITS,
    ApprovalPolicyName.SMART_APPROVE,
})


def _map_on(value: object) -> bool | None:
    """Map a boolean rollout flag to True, or None when off/unset."""
    if value is True:
        return True
    if isinstance(value, str) and value.strip().lower() in _ON_TOKENS:
        return True
    return None


def _map_default_agent(value: object) -> str | None:
    if isinstance(value, str) and value in _DEFAULT_AGENT_ROLLOUT_MODES:
        return value
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


def _map_system_prompt_behavior(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _as_json_value(value: object) -> object:
    """Parse a JSON-string value; pass typed (json-feature) values through."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


GROWTHBOOK_CONFIG_MAPPINGS: Final[
    dict[ExperimentName, tuple[tuple[str, GrowthbookConfigMapper], ...]]
] = {
    ExperimentName.SYSTEM_PROMPT: (("system_prompt_id", _map_system_prompt_variant),),
    ExperimentName.SYSTEM_PROMPT_BEHAVIOR: (
        ("system_prompt_behavior", _map_system_prompt_behavior),
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
    ExperimentName.DEFAULT_AGENT: (("default_agent", _map_default_agent),),
    ExperimentName.REGISTRY_SKILLS: (("experimental_enable_registry_skills", _map_on),),
    ExperimentName.MISTRAL_MCP_CONNECTOR: (
        ("experimental_enable_mistral_mcp_connector", _map_on),
    ),
    ExperimentName.STORAGE_USAGE: (("experimental_enable_storage_usage", _map_on),),
}


class GrowthbookLayer(ConfigLayer[RawConfig]):
    NAME = "growthbook"

    def __init__(self, *, name: str = NAME) -> None:
        super().__init__(name=name)
        self._variants: dict[str, object] = {}
        self._catalog_filter: dict[str, Any] | None = None

    def set_variants(self, variants: Mapping[str, object]) -> None:
        """Receive config-scoped variants, not telemetry assignments."""
        self._variants = dict(variants)
        # Parsed once per variants, not on every config rebuild, so a dropped
        # part is logged once.
        catalog_filter = self._variants.get(ExperimentName.MODEL_CATALOG_FILTER.value)
        self._catalog_filter = (
            None
            if catalog_filter is None
            else parse_catalog_filter(_as_json_value(catalog_filter))
        )

    @property
    def variants(self) -> Mapping[str, object]:
        return self._variants

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

        if self._catalog_filter:
            data[CATALOG_FILTER_KEY] = self._catalog_filter

        if not data:
            return EMPTY_CONFIG_SNAPSHOT

        fingerprint = create_dict_fingerprint(data)

        return LayerConfigSnapshot(data=data, fingerprint=fingerprint)

    async def _save_to_store(self, _next_config: RawConfig) -> str:
        raise NotImplementedError("GrowthbookLayer is read-only")
