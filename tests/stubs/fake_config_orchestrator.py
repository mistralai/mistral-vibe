from __future__ import annotations

from typing import Any

from jsonpatch import apply_patch as json_apply_patch
from jsonpointer import JsonPointer

from vibe.core.config import RawConfig, VibeConfigSchema
from vibe.core.config.event_bus import EventBus
from vibe.core.config.layer import ConfigLayer
from vibe.core.config.layers.default import DefaultConfigLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.orchestrator import ConfigOrchestrator, _changed_keys_between
from vibe.core.config.patch import PatchOp, ensure_parent_paths
from vibe.core.config.types import ConfigChangeEvent, ConflictStrategy


class FakeConfigOrchestrator[C: VibeConfigSchema](ConfigOrchestrator[C]):
    """In-memory test double that holds a config verbatim, skipping the layered
    ConfigOrchestrator machinery (builder, bus, layer stack).

    Reads return exactly the config the test built. Writes to a persisted layer
    are mirrored through a real default-plus-user layer stack so sparse writes
    have production merge semantics; writes targeting the in-memory overrides
    layer stay session-local.
    """

    def __init__(self, config: C) -> None:
        self._config = config
        self._bus = EventBus()

    def copy(self) -> FakeConfigOrchestrator[C]:
        return FakeConfigOrchestrator(self._config.model_copy(deep=True))

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
    def layers(self) -> tuple[ConfigLayer[RawConfig], ...]:
        # The verbatim fake has no layer stack; every value reads as a default.
        return ()

    @property
    def writable_layer_name(self) -> str:
        return UserConfigLayer().name

    async def load_persistence_layer(self) -> RawConfig:
        return await UserConfigLayer().load()

    def replace_config(self, config: C) -> None:
        self._config = config

    async def set_field(
        self,
        path: str,
        value: Any,
        reason: str = "No reason",
        *,
        target_layer: str | None = None,
    ) -> list[BaseException]:
        if target_layer != OverridesLayer.NAME:
            orchestrator = await self._persistence_orchestrator()
            await orchestrator.set_field(path, value, reason)
        before = self._config.model_dump(mode="json")
        data = self._config.model_dump()
        _set_pointer_in_place(data, path, value)
        self._config = type(self._config).model_validate(data)
        self._publish(before, reason)
        return []

    async def apply_patch(
        self,
        operations: list[PatchOp],
        reason: str = "No reason",
        *,
        on_conflict: ConflictStrategy = ConflictStrategy.CANCEL,
    ) -> list[BaseException]:
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
        data = ensure_parent_paths(self._config.model_dump(), operations)
        data = json_apply_patch(
            data, [op.to_json_patch() for op in operations], in_place=False
        )
        self._config = type(self._config).model_validate(data)
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
