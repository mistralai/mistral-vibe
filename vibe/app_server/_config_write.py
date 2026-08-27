from __future__ import annotations

from typing import Any

from vibe.app_server.protocol import ConfigWriteOpWire
from vibe.core.config.models import ModelConfig
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
