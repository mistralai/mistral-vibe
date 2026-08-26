from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
import copy
from typing import Any

from jsonpatch import JsonPatchException, apply_patch
from jsonpointer import JsonPointer, JsonPointerException
from pydantic import ValidationError

from vibe.core.config.builder import ConfigBuilder
from vibe.core.config.event_bus import EventBus
from vibe.core.config.layer import ConfigLayer, LayerNotLoadedError, RawConfig
from vibe.core.config.layers.default import DefaultConfigLayer
from vibe.core.config.layers.project import ProjectConfigLayer
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.patch import (
    AddOperationPatch,
    ConfigPatch,
    PatchOp,
    ensure_parent_paths,
    resolve_upsert_op,
)
from vibe.core.config.schema import ConfigSchema
from vibe.core.config.types import (
    ConfigChangeCallback,
    ConfigChangeEvent,
    ConflictStrategy,
)
from vibe.core.utils.concurrency import run_sync


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

# Durable layers reconstruct deterministically on restart, so a model alias
# present in any of them is safe to override sparsely. Dynamic layers
# (GrowthBook, admin) are excluded on purpose: a runtime-injected model absent
# from the durable layers must be materialized, or its sparse override would
# fail schema validation on the next launch (VIBE-4041).
_DURABLE_LAYER_TYPES = (DefaultConfigLayer, UserConfigLayer, ProjectConfigLayer)


async def _durable_model_aliases(layers: Iterable[ConfigLayer[RawConfig]]) -> set[str]:
    aliases: set[str] = set()
    for layer in layers:
        if not isinstance(layer, _DURABLE_LAYER_TYPES):
            continue
        try:
            raw = await layer.load()
        except Exception:
            continue
        models = raw.model_dump().get("models")
        if isinstance(models, dict):
            aliases.update(models)
    return aliases


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
        self._mutation_lock = asyncio.Lock()

    def copy(self) -> ConfigOrchestrator[S]:
        """Return an independent in-memory copy of this orchestrator.

        The builder and its layers are deep-copied so writes on the copy never
        touch the original. The default-layer resolver is rebound to the copied
        layers, and the copy starts with a fresh event bus so it does not
        inherit the original's subscribers.
        """
        builder = self._builder.copy()
        default_layer_name = self._default_layer_resolver().name
        layers_by_name = {layer.name: layer for layer in builder.layers}
        return type(self)(
            builder,
            copy.deepcopy(self._config),
            lambda: layers_by_name[default_layer_name],
            bus=None,
        )

    @classmethod
    async def create(
        cls,
        *,
        schema: type[S],
        layers: list[ConfigLayer[RawConfig]],
        default_layer_resolver: DefaultLayerResolver,
        bus: EventBus | None = None,
        validation_context: dict[str, Any] | None = None,
    ) -> ConfigOrchestrator[S]:
        """Build an orchestrator from a schema and an ordered list of layers."""
        builder = ConfigBuilder[S](schema, validation_context=validation_context)
        builder.add_layers(layers)
        config = await builder.build()
        instance = cls(builder, config, default_layer_resolver, bus)
        return instance

    @property
    def config(self) -> S:
        return self._config

    def rebuild(self) -> None:
        """Re-merge the layer stack synchronously and install the result."""
        self._config = run_sync(self._builder.build())

    @property
    def layers(self) -> tuple[ConfigLayer[RawConfig], ...]:
        """Active layers, lowest to highest priority. Read-only view."""
        return tuple(self._builder.layers)

    @property
    def writable_layer_name(self) -> str:
        """Name of the layer that implicit writes are routed to."""
        return self._resolve_default_layer_name()

    def get_layer(self, name: str) -> ConfigLayer[RawConfig]:
        for layer in self._builder.layers:
            if layer.name == name:
                return layer
        raise KeyError(f"No layer named {name!r}")

    def insert_layer(self, layer: ConfigLayer[RawConfig], index: int) -> None:
        """Insert a layer at *index* (0 = lowest priority). Rebuild to apply."""
        self._builder.insert_layer(layer, index)

    def remove_layer(self, index: int) -> ConfigLayer[RawConfig]:
        """Remove and return the layer at *index*. Rebuild to apply."""
        return self._builder.remove_layer(index)

    def replace_or_append_layer(self, name: str, layer: ConfigLayer[RawConfig]) -> None:
        """Replace the layer named *name* in place, or append it when absent."""
        index = next(
            (i for i, existing in enumerate(self.layers) if existing.name == name), None
        )
        if index is None:
            self.insert_layer(layer, len(self.layers))
            return
        self.remove_layer(index)
        self.insert_layer(layer, index)

    async def load_persistence_layer(self) -> RawConfig:
        return await self._default_layer_resolver().load()

    async def durable_model_aliases(self) -> set[str]:
        """Model aliases reconstructable from durable layers after a restart."""
        return await _durable_model_aliases(self.layers)

    def persisted_active_model(self) -> str:
        data = self._default_layer_resolver().cached_data
        if data is None:
            return ""
        value = getattr(data, "active_model", "")
        return value if isinstance(value, str) else ""

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
        preflight: Callable[[S], Awaitable[None]] | None = None,
    ) -> list[BaseException]:
        return await self.apply_patch(
            [AddOperationPatch(path=path, value=value, target_layer_name=target_layer)],
            reason=reason,
            preflight=preflight,
        )

    async def mutate_field(
        self,
        path: str,
        mutate: Callable[[Any], Any],
        reason: str = "No reason",
        *,
        default: Any = None,
        target_layer: str | None = None,
        preflight: Callable[[S], Awaitable[None]] | None = None,
    ) -> list[BaseException]:
        """Read, transform, preflight, and persist one field under the mutation lock."""
        async with self._mutation_lock:
            layer_name = target_layer or self._resolve_default_layer_name()
            try:
                raw = (await self.get_layer(layer_name).load()).model_dump()
            except KeyError as exc:
                return [exc]
            current = JsonPointer(path).resolve(raw, default=copy.deepcopy(default))
            value = mutate(copy.deepcopy(current))
            return await self._apply_patch_locked(
                [
                    AddOperationPatch(
                        path=path, value=value, target_layer_name=layer_name
                    )
                ],
                reason,
                on_conflict=ConflictStrategy.CANCEL,
                preflight=preflight,
            )

    async def upsert_field(
        self,
        path: str,
        *,
        key_field: str,
        value: dict[str, Any],
        reason: str = "No reason",
        target_layer: str | None = None,
    ) -> list[BaseException]:
        """Insert or replace one entry in a persisted config list section.

        *path* is a JSON Pointer to the list field (e.g. ``/providers``);
        *key_field* identifies an entry within that list (e.g. ``name``).
        When an entry with the same key already exists it is replaced in
        place, otherwise the value is appended (or the section is created
        when empty).
        """
        layer_name = target_layer or self._resolve_default_layer_name()
        raw: dict[str, Any] = (await (self.get_layer(layer_name)).load()).model_dump()
        existing = JsonPointer(path).resolve(raw, default=[])
        operation = resolve_upsert_op(
            existing, path, key_field, value, target_layer_name=layer_name
        )
        return await self.apply_patch([operation], reason=reason)

    async def apply_patch(
        self,
        operations: list[PatchOp],
        reason: str,
        *,
        on_conflict: ConflictStrategy = ConflictStrategy.CANCEL,
        preflight: Callable[[S], Awaitable[None]] | None = None,
    ) -> list[BaseException]:
        """Apply patch operations layer by layer.

        The merged-config preflight is a cheap sanity check only. Once patching
        begins, writes are not atomic across layers. Invalid patch requests
        still raise, but per-layer write failures are returned in the result.
        """
        if not operations:
            return []

        async with self._mutation_lock:
            return await self._apply_patch_locked(
                operations, reason, on_conflict=on_conflict, preflight=preflight
            )

    async def _apply_patch_locked(
        self,
        operations: list[PatchOp],
        reason: str,
        *,
        on_conflict: ConflictStrategy,
        preflight: Callable[[S], Awaitable[None]] | None,
    ) -> list[BaseException]:
        self._validate_patch_shape(operations)
        try:
            candidate = await self._preview_patch(operations)
        except (KeyError, LayerNotLoadedError) as exc:
            return [exc]
        if preflight is not None:
            await preflight(candidate)

        before = self._config.model_dump(mode="json")

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
        has_success = any(not isinstance(r, BaseException) for r in results)

        await self.reload()
        after = self._config.model_dump(mode="json")
        changed_keys = _changed_keys_between(before, after)
        if has_success and changed_keys:
            self._bus.publish(
                ConfigChangeEvent(
                    changed_keys=changed_keys, before=before, after=after, reason=reason
                )
            )

        return failures

    def _validate_patch_shape(self, operations: list[PatchOp]) -> None:
        try:
            self._builder.validate(
                apply_patch(
                    ensure_parent_paths(self._config.model_dump(), operations),
                    patch=[operation.to_json_patch() for operation in operations],
                    in_place=False,
                )
            )
        except (JsonPatchException, JsonPointerException, ValidationError) as exc:
            raise ConfigPatchValidationError() from exc

    async def _preview_patch(self, operations: list[PatchOp]) -> S:
        operations_by_layer: dict[str, list[PatchOp]] = defaultdict(list)
        default_layer_name: str | None = None
        for operation in operations:
            layer_name = operation.target_layer_name
            if layer_name is None:
                if default_layer_name is None:
                    default_layer_name = self._resolve_default_layer_name()
                layer_name = default_layer_name
            operations_by_layer[layer_name].append(operation)

        overrides: dict[str, RawConfig] = {}
        try:
            for layer_name, layer_operations in operations_by_layer.items():
                layer = self.get_layer(layer_name)
                if layer.cached_data is None or layer.fingerprint is None:
                    raise LayerNotLoadedError(layer_name)
                raw = layer.cached_data.model_dump()
                patched = apply_patch(
                    ensure_parent_paths(raw, layer_operations),
                    patch=[operation.to_json_patch() for operation in layer_operations],
                    in_place=False,
                )
                overrides[layer_name] = layer.validate_output(patched)
            return await self._builder.build(layer_overrides=overrides)
        except (JsonPatchException, JsonPointerException, ValidationError) as exc:
            raise ConfigPatchValidationError() from exc

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


_MISSING = object()


def _changed_keys_between(
    before: dict[str, Any], after: dict[str, Any]
) -> frozenset[str]:
    changed: set[str] = set()
    _collect_changed_keys(before, after, (), changed)
    return frozenset(changed)


def _collect_changed_keys(
    before: Any, after: Any, path: tuple[str, ...], changed: set[str]
) -> None:
    if before == after:
        return

    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() | after.keys()):
            _collect_changed_keys(
                before.get(key, _MISSING),
                after.get(key, _MISSING),
                (*path, key),
                changed,
            )
        return

    changed.add("/".join(path))
