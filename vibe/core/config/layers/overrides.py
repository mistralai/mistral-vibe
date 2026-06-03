from __future__ import annotations

import copy
from typing import Any

from vibe.core.config.layer import ConfigLayer, RawConfig


class OverridesLayer(ConfigLayer[RawConfig]):
    """Highest-priority layer wrapping an arbitrary dict passed at construction.

    Always trusted and read-only.
    Used by CLI and ACP entry points to inject runtime overrides.
    """

    def __init__(self, *, data: dict[str, Any], name: str = "overrides") -> None:
        super().__init__(name=name)
        self._data = data

    async def _check_trust(self) -> bool:
        return True

    async def _read_config(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    async def apply(self, patch: Any, *, on_conflict: str = "cancel") -> None:
        raise NotImplementedError("OverridesLayer.apply() is not implemented (M2)")
