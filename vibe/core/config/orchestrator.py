from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable

from jsonpatch import JsonPatchException, apply_patch
from jsonpointer import JsonPointerException
from pydantic import ValidationError

from vibe.core.config.builder import ConfigBuilder
from vibe.core.config.event_bus import EventBus
from vibe.core.config.layer import ConfigLayer, LayerNotLoadedError, RawConfig
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.patch import ConfigPatch, PatchOp
from vibe.core.config.schema import ConfigSchema
from vibe.core.config.types import ConfigChangeCallback, ConflictStrategy

DEFAULT_PATCH_LAYER_NAME = UserConfigLayer.LAYER_NAME


class ConfigPatchValidationError(Exception):
    """Raised when the merged-config preflight rejects a patch."""

    def __init__(self) -> None:
        super().__init__(
            "Config patch failed preflight validation against the merged config; "
            "fix the patch payload and retry"
        )


class ConfigOrchestrator[S: ConfigSchema]:
    """Single entry point for config management."""

    def __init__(
        self, builder: ConfigBuilder[S], config: S, bus: EventBus | None = None
    ) -> None:
        self._builder = builder
        self._config = config
        self._bus = bus if bus is not None else EventBus()

    @classmethod
    async def create(
        cls,
        *,
        schema: type[S],
        layers: list[ConfigLayer[RawConfig]],
        bus: EventBus | None = None,
    ) -> ConfigOrchestrator[S]:
        """Build an orchestrator from a schema and an ordered list of layers."""
        builder = ConfigBuilder[S](schema)
        builder.add_layers(layers)
        config = await builder.build()
        instance = cls(builder, config, bus)
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

    async def apply_patch(
        self,
        operations: list[PatchOp],
        reason: str,
        *,
        on_conflict: ConflictStrategy = ConflictStrategy.CANCEL,
    ) -> list[BaseException]:
        """Apply patch operations layer by layer.

        The merged-config preflight is a cheap sanity check only. Once patching
        begins, writes are not atomic across layers. Invalid patch requests
        still raise, but per-layer write failures are returned in the result.
        """
        if not operations:
            return []

        # Simulate and validate final config
        try:
            self.config.model_validate(
                apply_patch(
                    self._config.model_dump(),
                    patch=[operation.to_json_patch() for operation in operations],
                    in_place=False,
                )
            )
        except (JsonPatchException, JsonPointerException, ValidationError) as exc:
            raise ConfigPatchValidationError() from exc

        operations_by_layer: dict[str, list[PatchOp]] = defaultdict(list)
        for op in operations:
            layer_name = (
                op.target_layer_name
                if op.target_layer_name is not None
                else DEFAULT_PATCH_LAYER_NAME
            )
            operations_by_layer[layer_name].append(op)

        tasks = []
        for layer_name, layer_operations in operations_by_layer.items():
            tasks.append(
                asyncio.create_task(
                    self._apply_patch_to_layer(
                        layer_name=layer_name,
                        layer_operations=list(layer_operations),
                        reason=reason,
                        on_conflict=on_conflict,
                    )
                )
            )
        results = await asyncio.gather(*tasks, return_exceptions=True)
        failures = [r for r in results if isinstance(r, BaseException)]

        await self.reload()

        return failures

    async def _apply_patch_to_layer(
        self,
        *,
        layer_name: str,
        layer_operations: list[PatchOp],
        reason: str,
        on_conflict: ConflictStrategy,
    ) -> None:
        layer = self.get_layer(layer_name)
        if layer.fingerprint is None:
            raise LayerNotLoadedError(layer_name)
        await layer.apply(
            ConfigPatch(
                *layer_operations, fingerprint=layer.fingerprint, reason=reason
            ),
            on_conflict=on_conflict,
        )

    def subscribe(
        self, callback: ConfigChangeCallback, *, keys: set[str] | None = None
    ) -> Callable[[], None]:
        """Register a listener and return a callable that unsubscribes it.

        Args:
            callback: Invoked with the event on every matching config change.
            keys: Slash-separated config paths to filter on (e.g. {"models/active"}).
                A path matches its ancestors and descendants but not partial
                segments ("model" never matches "models"). None subscribes to
                every change (wildcard).
        """
        return self._bus.subscribe(callback, keys=keys)
