from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final

from vibe.core.config.fingerprint import create_dict_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.types import EMPTY_CONFIG_SNAPSHOT, LayerConfigSnapshot
from vibe.core.experiments.active import ExperimentName
from vibe.core.prompts import load_system_prompt

type GrowthbookConfigMapper = Callable[[str], str | None]


def _map_system_prompt_variant(variant: str) -> str | None:
    try:
        load_system_prompt(variant)
    except ValueError:
        return None
    return variant


GROWTHBOOK_CONFIG_MAPPINGS: Final[
    dict[ExperimentName, tuple[str, GrowthbookConfigMapper]]
] = {ExperimentName.SYSTEM_PROMPT: ("system_prompt_id", _map_system_prompt_variant)}


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

        data: dict[str, str] = {}
        for experiment_name, (
            config_field,
            map_variant,
        ) in GROWTHBOOK_CONFIG_MAPPINGS.items():
            variant = self._variants.get(experiment_name.value)
            if variant is None:
                continue
            mapped_value = map_variant(variant)
            if mapped_value is not None:
                data[config_field] = mapped_value

        if not data:
            return EMPTY_CONFIG_SNAPSHOT

        fingerprint = create_dict_fingerprint(data)

        return LayerConfigSnapshot(data=data, fingerprint=fingerprint)

    async def _save_to_store(self, _next_config: RawConfig) -> str:
        raise NotImplementedError("GrowthbookLayer is read-only")
