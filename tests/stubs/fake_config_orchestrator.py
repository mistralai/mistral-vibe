from __future__ import annotations

from typing import Any

from jsonpatch import apply_patch as json_apply_patch
from jsonpointer import JsonPointer

from vibe.core.config import VibeConfigSchema
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.patch import PatchOp, ensure_parent_paths
from vibe.core.config.types import ConflictStrategy


class FakeConfigOrchestrator[C: VibeConfigSchema](ConfigOrchestrator[C]):
    """In-memory test double that holds a config verbatim, skipping the layered
    ConfigOrchestrator machinery (builder, bus, layer stack).

    Reads return exactly the config the test built (no default/user/project layer
    merge). ``set_field`` writes to a persisted layer are mirrored to the on-disk
    TOML through a real ConfigOrchestrator so tests that read the config back from
    disk observe the change; writes targeting the in-memory overrides layer stay
    session-local. ``apply_patch`` is applied in-memory only.
    """

    def __init__(self, config: C) -> None:
        self._config = config

    def copy(self) -> FakeConfigOrchestrator[C]:
        return FakeConfigOrchestrator(self._config.model_copy(deep=True))

    @property
    def config(self) -> C:
        return self._config

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
            # Persist through a real orchestrator's set_field, but over a single
            # user layer only: no migrations mutating the file and no default
            # layer to union list-valued fields against.
            layer = UserConfigLayer()
            orchestrator = await ConfigOrchestrator.create(
                schema=type(self._config),
                layers=[layer],
                default_layer_resolver=lambda: layer,
            )
            await orchestrator.set_field(path, value, reason)
        data = self._config.model_dump()
        _set_pointer_in_place(data, path, value)
        self._config = type(self._config).model_validate(data)
        return []

    async def apply_patch(
        self,
        operations: list[PatchOp],
        reason: str = "No reason",
        *,
        on_conflict: ConflictStrategy = ConflictStrategy.CANCEL,
    ) -> list[BaseException]:
        data = ensure_parent_paths(self._config.model_dump(), operations)
        data = json_apply_patch(
            data, [op.to_json_patch() for op in operations], in_place=False
        )
        self._config = type(self._config).model_validate(data)
        return []

    async def reload(self) -> None:
        return None


def _set_pointer_in_place(root: dict[str, Any], path: str, value: Any) -> None:
    parts = JsonPointer(path).parts
    target: Any = root
    for part in parts[:-1]:
        if not isinstance(target.get(part), dict):
            target[part] = {}
        target = target[part]
    target[parts[-1]] = value
