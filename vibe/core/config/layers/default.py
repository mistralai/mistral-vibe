from __future__ import annotations

from vibe.core.config.fingerprint import create_dict_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.schema import ConfigSchema
from vibe.core.config.types import LayerConfigSnapshot


class DefaultConfigLayer(ConfigLayer[RawConfig]):
    """Lowest-priority layer exposing schema defaults as mergeable config data."""

    NAME = "default"

    def __init__(self, *, schema: type[ConfigSchema], name: str = NAME) -> None:
        super().__init__(name=name)
        self._schema = schema

    async def _check_trust(self) -> bool:
        return True

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        data = self._schema.model_construct().model_dump(mode="json")
        fingerprint = create_dict_fingerprint(data)
        return LayerConfigSnapshot(data=data, fingerprint=fingerprint)

    async def _save_to_store(self, _next_config: RawConfig) -> str:
        raise NotImplementedError("DefaultConfigLayer is read-only")
