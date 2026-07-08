from __future__ import annotations

import copy
from typing import Any

from vibe.core.config.fingerprint import create_dict_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.types import LayerConfigSnapshot


class OverridesLayer(ConfigLayer[RawConfig]):
    """Highest-priority layer wrapping an arbitrary dict passed at construction.

    Always trusted and read-only.
    Used by CLI and ACP entry points to inject runtime overrides.
    """

    NAME = "overrides"

    def __init__(self, *, data: dict[str, Any], name: str = NAME) -> None:
        super().__init__(name=name)
        self._data = data

    async def _check_trust(self) -> bool:
        return True

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        data = copy.deepcopy(self._data)
        fingerprint = create_dict_fingerprint(data)
        return LayerConfigSnapshot(data=data, fingerprint=fingerprint)

    async def _save_to_store(self, _next_config: RawConfig) -> str:
        raise NotImplementedError(
            "OverridesLayer patch persistence is not implemented yet"
        )
