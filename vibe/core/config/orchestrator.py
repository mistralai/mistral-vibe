from __future__ import annotations

from typing import Any

from vibe.core.config.builder import ConfigBuilder
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.schema import ConfigSchema


class ConfigOrchestrator[S: ConfigSchema]:
    """Single entry point for config management."""

    def __init__(self, builder: ConfigBuilder[S], config: S) -> None:
        self._builder = builder
        self._config = config

    @classmethod
    async def create(
        cls, *, schema: type[S], layers: list[ConfigLayer[RawConfig]]
    ) -> ConfigOrchestrator[S]:
        """Build an orchestrator from a schema and an ordered list of layers."""
        builder = ConfigBuilder[S](schema)
        builder.add_layers(layers)
        config = await builder.build()
        instance = cls(builder, config)
        return instance

    @property
    def config(self) -> S:
        return self._config

    def get_layer(self, name: str) -> ConfigLayer[RawConfig]:
        for layer in self._builder.layers:
            if layer.name == name:
                return layer
        raise KeyError(f"No layer named {name!r}")

    async def reload(self) -> None:
        """Force-reload all layers and atomically replace the config snapshot."""
        self._config = await self._builder.build(force_load=True)

    async def apply_patch(self, patch: Any) -> None:
        raise NotImplementedError("apply_patch() is not implemented (M2)")

    async def subscribe(self, callback: Any) -> None:
        raise NotImplementedError("subscribe() is not implemented (M3)")
