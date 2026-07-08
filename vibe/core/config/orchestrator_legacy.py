from __future__ import annotations

from typing import TYPE_CHECKING, Any

from jsonpointer import JsonPointer

from vibe.core.config._settings import VibeConfig
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.logger import logger

if TYPE_CHECKING:
    from vibe.core.config import AnyVibeConfig
    from vibe.core.config.orchestrator_port import ConfigOrchestratorPort


class LegacyConfigOrchestrator:
    """Adapter exposing the ConfigOrchestrator write/lifecycle surface over the
    legacy VibeConfig.

    Mirrors ConfigOrchestrator's `config` / `set_field` / `reload` so callers can
    depend on a single interface regardless of the active backend. Layer-aware
    operations (apply_patch, subscribe, get_layer) are intentionally absent: the
    legacy config has no layers to honor them faithfully.
    """

    def __init__(self, config: AnyVibeConfig) -> None:
        self._config = config

    @property
    def config(self) -> AnyVibeConfig:
        return self._config

    async def set_field(
        self,
        path: str,
        value: Any,
        reason: str = "No reason",
        *,
        target_layer: str | None = None,
    ) -> list[BaseException]:
        if target_layer == OverridesLayer.NAME:
            _set_pointer_in_place(self._config, path, value)
            return []
        VibeConfig.save_updates(_pointer_to_nested_update(path, value))
        return []

    async def reload(self) -> None:
        self._config = VibeConfig.load()


async def load_config_orchestrator(
    data: dict[str, Any] | None = None,
) -> ConfigOrchestratorPort[AnyVibeConfig]:
    """Load the config and return the orchestrator the app should use.

    When the feature flag is on, build the ConfigOrchestrator layer stack.
    Otherwise wrap the legacy VibeConfig in LegacyConfigOrchestrator so callers
    always depend on the same write/lifecycle surface.
    """
    config = VibeConfig.load(**(data or {}))
    if config.enable_config_orchestrator:
        logger.info("Config orchestrator enabled via feature flag")
        return await build_default_orchestrator(data=data)
    return LegacyConfigOrchestrator(config)


def _pointer_to_nested_update(path: str, value: Any) -> dict[str, Any]:
    """Turn a JSON Pointer path + value into a nested update dict.

    `/tools/bash/allowlist` + [...] -> {"tools": {"bash": {"allowlist": [...]}}}
    """
    nested: Any = value
    for part in reversed(JsonPointer(path).parts):
        nested = {part: nested}
    return nested


def _set_pointer_in_place(root: Any, path: str, value: Any) -> None:
    """Set the value a JSON Pointer targets on an in-memory object graph.

    Walks attributes (or dict keys) to the parent, then assigns the last
    segment. Raises like `obj.a = b` would if the assignment is not possible.
    """
    parts = JsonPointer(path).parts
    target = root
    for part in parts[:-1]:
        target = target[part] if isinstance(target, dict) else getattr(target, part)
    if isinstance(target, dict):
        target[parts[-1]] = value
    else:
        setattr(target, parts[-1], value)
