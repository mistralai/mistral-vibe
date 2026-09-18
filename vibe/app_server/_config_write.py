from __future__ import annotations

from typing import Any

from vibe.app_server._session_model import ACTIVE_MODEL_PATH
from vibe.app_server.protocol import ConfigWriteOpWire
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.layers.project import ProjectConfigLayer
from vibe.core.config.models import ModelConfig
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.patch import (
    AddOperationPatch,
    PatchOp,
    RemoveOperationPatch,
    escape_json_pointer_token,
)
from vibe.core.config.vibe_schema import VibeConfigSchema

# Only these fields are required for a valid persisted model entry; all other
# ModelConfig fields have defaults. Writing only required fields + the changed
# field avoids baking in values from higher-priority layers (admin/GrowthBook)
# into the user's writable config.
_REQUIRED_MODEL_FIELDS = {"name", "provider", "alias"}


def _model_after_write(config: VibeConfigSchema, model_alias: str | None) -> str:
    """The model this write leaves active, which is where thinking belongs."""
    if model_alias:
        return model_alias
    if model_alias == "":
        return config.resolve_default_model_alias()
    return config.get_active_model().alias


def model_config_write_ops(
    config: VibeConfigSchema, *, model_alias: str | None, reasoning_effort: str | None
) -> list[ConfigWriteOpWire]:
    """The writes a model pick makes, from the pick itself.

    Thinking is stored under whichever model is active after this write -- the
    one being picked, or the current one when only thinking changes -- so the
    two travel together and only this function needs to know the paths.

    An empty alias is how this configuration says "follow the default", and
    writing it is the only way to unpin a session, so it is passed through
    rather than looked up.

    Raises ``ValueError`` for a model this configuration does not offer.
    """
    if model_alias and model_alias not in config.models:
        available = ", ".join(sorted(config.models))
        raise ValueError(f"Unknown model: {model_alias}. Available: {available}")
    ops: list[ConfigWriteOpWire] = []
    if model_alias is not None:
        ops.append(
            ConfigWriteOpWire(op="set", path=ACTIVE_MODEL_PATH, value=model_alias)
        )
    if reasoning_effort is not None:
        target = _model_after_write(config, model_alias)
        ops.append(
            ConfigWriteOpWire(
                op="set",
                path=f"/models/{escape_json_pointer_token(target)}/thinking",
                value=reasoning_effort,
            )
        )
    return ops


def config_write_targets(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> list[str]:
    """Save targets offered by /config, default first.

    The default write target leads, then the trusted project layer, then the
    session-only override layer. The project layer is only offered once a
    project config file has actually been discovered: an undiscovered project
    layer reports itself trusted, and offering it would add a third save target
    for a scope the user never opted into.
    """
    targets = [orchestrator.writable_layer_name]
    for layer in orchestrator.layers:
        if layer.name in targets:
            continue
        if isinstance(layer, ProjectConfigLayer):
            if layer.is_file_discovered and layer.is_trusted is not False:
                targets.append(layer.name)
        elif layer.name == OverridesLayer.NAME:
            targets.append(layer.name)
    return targets


def config_write_ops_to_patches(
    config: VibeConfigSchema,
    ops: list[ConfigWriteOpWire],
    *,
    durable_model_aliases: set[str],
) -> list[PatchOp]:
    model = config.get_active_model()
    model_path = f"/models/{escape_json_pointer_token(model.alias)}"
    model_prefix = f"{model_path}/"
    model_fields = type(model).model_fields

    # Materialize the active model when no durable layer can reconstruct it on
    # restart; a sparse field-only override of such a model would be missing its
    # required identity fields and fail schema validation next launch. Multiple
    # field writes targeting the same alias in one batch fold into a single
    # upsert so earlier changes are not silently overwritten.
    pending: dict[str | None, dict[str, Any]] = {}
    operations: list[PatchOp] = []

    for op in ops:
        if op.op == "remove":
            operations.append(
                RemoveOperationPatch(path=op.path, target_layer_name=op.target_layer)
            )
            continue

        field = op.path.removeprefix(model_prefix)
        if (
            op.path.startswith(model_prefix)
            and field in model_fields
            and model.alias not in durable_model_aliases
        ):
            pending.setdefault(op.target_layer, {})[field] = op.value
            continue

        operations.append(
            AddOperationPatch(
                path=op.path, value=op.value, target_layer_name=op.target_layer
            )
        )

    for target_layer, overrides in pending.items():
        operations.append(
            AddOperationPatch(
                path=model_path,
                value=_minimal_model_payload(model, overrides),
                target_layer_name=target_layer,
            )
        )

    return operations


def _minimal_model_payload(
    model: ModelConfig, overrides: dict[str, Any]
) -> dict[str, Any]:
    base = {field: getattr(model, field) for field in _REQUIRED_MODEL_FIELDS}
    return {**base, **overrides}
