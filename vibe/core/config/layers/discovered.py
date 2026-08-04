from __future__ import annotations

import copy
from typing import Any

from vibe.core.config.fingerprint import create_dict_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.types import LayerConfigSnapshot


class DiscoveredConfigLayer(ConfigLayer[RawConfig]):
    """In-memory config populated from runtime discovery, e.g. tools."""

    NAME = "discovered"

    def __init__(self, *, data: dict[str, Any] | None = None, name: str = NAME) -> None:
        super().__init__(name=name)
        self._data = copy.deepcopy(data or {})

    async def _check_trust(self) -> bool:
        return True

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        data = copy.deepcopy(self._data)
        fingerprint = create_dict_fingerprint(data)
        return LayerConfigSnapshot(data=data, fingerprint=fingerprint)

    async def _save_to_store(self, next_config: RawConfig) -> str:
        self._data = copy.deepcopy(next_config.model_dump())
        return create_dict_fingerprint(self._data)
