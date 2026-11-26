from __future__ import annotations

from typing import Any

from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.types import LayerConfigSnapshot


class FakeRawLayer(ConfigLayer[RawConfig]):
    """A trusted layer carrying raw data, for exercising the merge directly."""

    def __init__(self, *, name: str, data: dict[str, Any]) -> None:
        super().__init__(name=name)
        self._data = data

    async def _check_trust(self) -> bool:
        return True

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        return LayerConfigSnapshot(data=dict(self._data), fingerprint="fp")

    async def _save_to_store(self, _next_config: RawConfig) -> str:
        raise NotImplementedError
