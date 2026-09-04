from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import JsonValue

from vibe.app_server.protocol import ConfigWriteOpWire
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.patch import AddOperationPatch, RemoveOperationPatch
from vibe.core.config.vibe_schema import VibeConfigSchema

ACTIVE_MODEL_PATH = "/active_model"


def stored_session_active_model(config: Mapping[str, JsonValue] | None) -> str | None:
    """Return a concrete model alias from a session config snapshot."""
    if config is None:
        return None
    active_model = config.get("active_model")
    return active_model if isinstance(active_model, str) and active_model else None


def active_model_override_write_requested(ops: Sequence[ConfigWriteOpWire]) -> bool:
    return any(
        op.path == ACTIVE_MODEL_PATH and op.target_layer in {None, OverridesLayer.NAME}
        for op in ops
    )


def with_session_active_model_write(
    ops: Sequence[ConfigWriteOpWire],
) -> list[ConfigWriteOpWire]:
    """Mirror implicit active-model writes into the session override layer."""
    mirrored = list(ops)
    mirrored.extend(
        op.model_copy(update={"target_layer": OverridesLayer.NAME})
        for op in ops
        if op.path == ACTIVE_MODEL_PATH and op.target_layer is None
    )
    return mirrored


def override_active_model(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> str | None:
    layer = next(
        (layer for layer in orchestrator.layers if layer.name == OverridesLayer.NAME),
        None,
    )
    if layer is None:
        return None
    data = layer.cached_data
    if data is None:
        return None
    active_model = getattr(data, "active_model", None)
    return active_model if isinstance(active_model, str) and active_model else None


def active_model_is_pinned(orchestrator: ConfigOrchestrator[VibeConfigSchema]) -> bool:
    return bool(
        orchestrator.config.active_model
        and (
            orchestrator.persisted_active_model() or override_active_model(orchestrator)
        )
    )


async def set_session_active_model_override(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    active_model: str,
    *,
    reason: str,
) -> list[BaseException]:
    if override_active_model(orchestrator) == active_model:
        return []
    return await orchestrator.apply_patch(
        [
            AddOperationPatch(
                path=ACTIVE_MODEL_PATH,
                value=active_model,
                target_layer_name=OverridesLayer.NAME,
            )
        ],
        reason=reason,
    )


async def clear_session_active_model_override(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], *, reason: str
) -> list[BaseException]:
    if override_active_model(orchestrator) is None:
        return []
    return await orchestrator.apply_patch(
        [
            RemoveOperationPatch(
                path=ACTIVE_MODEL_PATH, target_layer_name=OverridesLayer.NAME
            )
        ],
        reason=reason,
    )


def config_active_model(metadata: Mapping[str, Any]) -> str | None:
    raw_config = metadata.get("config")
    if not isinstance(raw_config, dict):
        return None
    return stored_session_active_model(raw_config)
