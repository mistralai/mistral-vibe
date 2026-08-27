from __future__ import annotations

from collections.abc import Awaitable, Callable
import copy
from typing import Any

from jsonpatch import apply_patch as json_apply_patch
from jsonpointer import JsonPointer

from vibe.core.config import RawConfig, VibeConfigSchema
from vibe.core.config.builder import ConfigBuilder
from vibe.core.config.event_bus import EventBus
from vibe.core.config.layer import ConfigLayer
from vibe.core.config.layers.default import DefaultConfigLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.layers.project import ProjectConfigLayer
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.orchestrator import (
    ConfigOrchestrator,
    _changed_keys_between,
    _durable_model_aliases,
)
from vibe.core.config.patch import PatchOp, ensure_parent_paths
from vibe.core.config.types import ConfigChangeEvent, ConflictStrategy
from vibe.core.utils.concurrency import run_sync


class FakeConfigOrchestrator[C: VibeConfigSchema](ConfigOrchestrator[C]):
    """In-memory test double that holds a config verbatim, skipping the layered
    ConfigOrchestrator machinery (builder, bus, layer stack).

    Reads return exactly the config the test built. Writes to a persisted layer
    are mirrored through a real default-plus-user layer stack so sparse writes
    have production merge semantics; writes targeting the in-memory overrides
    layer stay session-local.

    The verbatim config is treated as the base layer; additional in-memory
    layers (e.g. the agent-profile layer) may be inserted and are folded onto it
    by ``rebuild`` using production merge semantics.
    """

    def __init__(self, config: C) -> None:
        self._base_config = config
        self._config = config
        self._extra_layers: list[ConfigLayer[RawConfig]] = []
        self._bus = EventBus()

    def copy(self) -> FakeConfigOrchestrator[C]:
        clone = FakeConfigOrchestrator(self._base_config.model_copy(deep=True))
        clone._extra_layers = [copy.deepcopy(layer) for layer in self._extra_layers]
        clone.rebuild()
        return clone

    @property
    def layers(self) -> tuple[ConfigLayer[RawConfig], ...]:
        return tuple(self._extra_layers)

    def insert_layer(self, layer: ConfigLayer[RawConfig], index: int) -> None:
        self._extra_layers.insert(index, layer)

    def remove_layer(self, index: int) -> ConfigLayer[RawConfig]:
        return self._extra_layers.pop(index)

    def rebuild(self) -> None:
        builder = ConfigBuilder(type(self._base_config))
        builder.add_layer(
            OverridesLayer(
                data=self._base_config.model_dump(mode="json"), name="fake-base"
            )
        )
        builder.add_layers(list(self._extra_layers))
        result = run_sync(builder.build())
        # The verbatim base is already validated (e.g. active_model fixed up), so
        # re-validation won't reproduce its warnings; carry them forward to match
        # the real orchestrator, which re-merges raw layer snapshots.
        for warning in self._base_config.validation_warnings:
            if warning not in result.validation_warnings:
                result._validation_warnings.append(warning)
        self._config = result

    def _publish(self, before: dict[str, Any], reason: str) -> None:
        after = self._config.model_dump(mode="json")
        if changed := _changed_keys_between(before, after):
            self._bus.publish(
                ConfigChangeEvent(
                    changed_keys=changed, before=before, after=after, reason=reason
                )
            )

    @property
    def config(self) -> C:
        return self._config

    @property
    def writable_layer_name(self) -> str:
        return UserConfigLayer().name

    async def load_persistence_layer(self) -> RawConfig:
        return await UserConfigLayer().load()

    async def durable_model_aliases(self) -> set[str]:
        # Mirror the durable stack a real restart rebuilds: schema defaults plus
        # the on-disk user and project layers the fake persists writes to.
        return await _durable_model_aliases((
            DefaultConfigLayer(schema=type(self._config)),
            UserConfigLayer(),
            ProjectConfigLayer(),
        ))

    def persisted_active_model(self) -> str:
        # The verbatim fake has no layer stack, so the held config's value is
        # exactly what the test declared as the user's pin.
        return self._base_config.active_model

    async def set_field(
        self,
        path: str,
        value: Any,
        reason: str = "No reason",
        *,
        target_layer: str | None = None,
        preflight: Callable[[C], Awaitable[None]] | None = None,
    ) -> list[BaseException]:
        data = self._base_config.model_dump()
        _set_pointer_in_place(data, path, value)
        candidate = self.copy()
        candidate._base_config = type(self._base_config).model_validate(data)
        candidate.rebuild()
        if preflight is not None:
            await preflight(candidate.config)
        if target_layer != OverridesLayer.NAME:
            orchestrator = await self._persistence_orchestrator()
            await orchestrator.set_field(path, value, reason)
        before = self._config.model_dump(mode="json")
        self._base_config = type(self._base_config).model_validate(data)
        self.rebuild()
        self._publish(before, reason)
        return []

    async def mutate_field(
        self,
        path: str,
        mutate: Callable[[Any], Any],
        reason: str = "No reason",
        *,
        default: Any = None,
        target_layer: str | None = None,
        preflight: Callable[[C], Awaitable[None]] | None = None,
    ) -> list[BaseException]:
        raw = (await self.load_persistence_layer()).model_dump()
        current = JsonPointer(path).resolve(raw, default=copy.deepcopy(default))
        return await self.set_field(
            path,
            mutate(copy.deepcopy(current)),
            reason,
            target_layer=target_layer,
            preflight=preflight,
        )

    async def apply_patch(
        self,
        operations: list[PatchOp],
        reason: str = "No reason",
        *,
        on_conflict: ConflictStrategy = ConflictStrategy.CANCEL,
        preflight: Callable[[C], Awaitable[None]] | None = None,
    ) -> list[BaseException]:
        data = ensure_parent_paths(self._base_config.model_dump(), operations)
        data = json_apply_patch(
            data, [op.to_json_patch() for op in operations], in_place=False
        )
        candidate = self.copy()
        candidate._base_config = type(self._base_config).model_validate(data)
        candidate.rebuild()
        if preflight is not None:
            await preflight(candidate.config)
        before = self._config.model_dump(mode="json")
        persistent_operations = [
            operation
            for operation in operations
            if operation.target_layer_name != OverridesLayer.NAME
        ]
        if persistent_operations:
            orchestrator = await self._persistence_orchestrator()
            failures = await orchestrator.apply_patch(
                persistent_operations, reason, on_conflict=on_conflict
            )
            if failures:
                return failures
        self._base_config = type(self._base_config).model_validate(data)
        self.rebuild()
        self._publish(before, reason)
        return []

    async def reload(self) -> None:
        return None

    async def _persistence_orchestrator(self) -> ConfigOrchestrator[C]:
        layer = UserConfigLayer()
        return await ConfigOrchestrator.create(
            schema=type(self._config),
            layers=[DefaultConfigLayer(schema=type(self._config)), layer],
            default_layer_resolver=lambda: layer,
        )


def _set_pointer_in_place(root: dict[str, Any], path: str, value: Any) -> None:
    parts = JsonPointer(path).parts
    target: Any = root
    for part in parts[:-1]:
        if not isinstance(target.get(part), dict):
            target[part] = {}
        target = target[part]
    target[parts[-1]] = value
