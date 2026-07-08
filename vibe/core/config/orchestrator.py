from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from jsonpatch import JsonPatchException, apply_patch
from jsonpointer import JsonPointerException
from pydantic import ValidationError

from vibe.core.config.builder import ConfigBuilder
from vibe.core.config.event_bus import EventBus
from vibe.core.config.layer import ConfigLayer, LayerNotLoadedError, RawConfig
from vibe.core.config.patch import AddOperationPatch, ConfigPatch, PatchOp
from vibe.core.config.schema import ConfigSchema
from vibe.core.config.types import ConfigChangeCallback, ConflictStrategy


class ConfigPatchValidationError(Exception):
    """Raised when the merged-config preflight rejects a patch."""

    def __init__(self) -> None:
        super().__init__(
            "Config patch failed preflight validation against the merged config; "
            "fix the patch payload and retry"
        )


class DefaultLayerResolutionError(Exception):
    """Raised when a patch needs implicit routing but no valid default is available."""


type DefaultLayerResolver = Callable[[], ConfigLayer[RawConfig]]


class ConfigOrchestrator[S: ConfigSchema]:
    """Single entry point for config management."""

    def __init__(
        self,
        builder: ConfigBuilder[S],
        config: S,
        default_layer_resolver: DefaultLayerResolver,
        bus: EventBus | None = None,
    ) -> None:
        self._builder = builder
        self._config = config
        self._default_layer_resolver = default_layer_resolver
        self._bus = bus if bus is not None else EventBus()

    @classmethod
    async def create(
        cls,
        *,
        schema: type[S],
        layers: list[ConfigLayer[RawConfig]],
        default_layer_resolver: DefaultLayerResolver,
        bus: EventBus | None = None,
    ) -> ConfigOrchestrator[S]:
        """Build an orchestrator from a schema and an ordered list of layers."""
        builder = ConfigBuilder[S](schema)
        builder.add_layers(layers)
        config = await builder.build()
        instance = cls(builder, config, default_layer_resolver, bus)
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

    async def set_field(
        self,
        path: str,
        value: Any,
        reason: str = "No reason",
        *,
        target_layer: str | None = None,
    ) -> list[BaseException]:
        return await self.apply_patch(
            [AddOperationPatch(path=path, value=value, target_layer_name=target_layer)],
            reason=reason,
        )

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
        default_layer_name: str | None = None
        for op in operations:
            layer_name = op.target_layer_name
            if layer_name is None:
                if default_layer_name is None:
                    default_layer_name = self._resolve_default_layer_name()
                layer_name = default_layer_name

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

    def _resolve_default_layer_name(self) -> str:
        layer = self._default_layer_resolver()
        if layer not in self._builder.layers:
            raise DefaultLayerResolutionError(
                f"Default layer resolver returned unknown layer {layer.name!r}"
            )

        return layer.name

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
