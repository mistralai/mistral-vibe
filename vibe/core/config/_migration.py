from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from vibe.core.config.layer import (
    ConfigLayer,
    EmptyLayerError,
    LayerNotLoadedError,
    RawConfig,
    UntrustedLayerError,
)
from vibe.core.config.layers._base import BaseTomlConfigLayer
from vibe.core.config.patch import ConfigPatch, ReplaceOperationPatch

# One-shot id: syncs an existing bash allowlist up to the current default
# read-only commands once, so users keep the ability to remove any of them.
BASH_READ_ONLY_MIGRATION = "bash_read_only_defaults_v1"
MODEL_RENAME_MIGRATION = "model_rename_devstral_2_to_mistral_medium_3_5_v1"

_LEGACY_MODEL_ALIAS = "devstral-2"
_CURRENT_MODEL_ALIAS = "mistral-medium-3.5"
_OFFICIAL_MODEL_NAME = "mistral-vibe-cli-latest"

# Old tool name -> new tool name. The new tools replaced these in-place, so
# existing user configs keyed by the old names need their settings moved over.
RENAMED_TOOLS: dict[str, str] = {"read": "read_file", "search_replace": "edit"}

# Options on the old tool that have no equivalent on the new one; dropped on migrate.
DROPPED_TOOL_OPTIONS: dict[str, tuple[str, ...]] = {
    "edit": ("max_content_size", "create_backup")
}


async def migrate_config_layers(layers: Iterable[ConfigLayer[RawConfig]]) -> None:
    for layer in layers:
        if not isinstance(layer, BaseTomlConfigLayer):
            continue

        try:
            data = (await layer.load()).model_dump()
        except (EmptyLayerError, UntrustedLayerError):
            continue

        if not migrate_config(data):
            continue

        fingerprint = layer.fingerprint
        if fingerprint is None:
            raise LayerNotLoadedError(layer.name)

        await layer.apply(
            ConfigPatch(
                ReplaceOperationPatch(path="", value=data),
                fingerprint=fingerprint,
                reason="config migration",
            )
        )


def migrate_config(data: dict[str, Any]) -> bool:
    """Apply every config migration in order, mutating ``data`` in place.

    Returns whether anything changed, so the caller can decide to persist.
    """
    changed = False
    changed |= _migrate_bash_allowlist(data)
    changed |= _migrate_bash_read_only(data)
    changed |= _migrate_model_renames(data)
    changed |= _migrate_renamed_tools(data)
    return changed


def _migrate_bash_allowlist(data: dict[str, Any]) -> bool:
    """Add 'find' to the bash allowlist and strip any trailing wildcards."""
    bash_tools = data.get("tools", {}).get("bash", {})
    allowlist = bash_tools.get("allowlist")
    if allowlist is None:
        return False

    changed = False
    if "find" not in allowlist:
        allowlist.append("find")
        allowlist.sort()
        changed = True

    if any(p.endswith(" *") for p in allowlist):
        stripped = [p[:-2] if p.endswith(" *") else p for p in allowlist]
        bash_tools["allowlist"] = sorted(set(stripped))
        changed = True

    return changed


def _migrate_bash_read_only(data: dict[str, Any]) -> bool:
    """Add the default read-only commands to the bash allowlist once."""
    bash_tools = data.get("tools", {}).get("bash", {})
    allowlist = bash_tools.get("allowlist")
    if allowlist is None:
        return False

    applied: list[str] = data.get("applied_migrations", [])
    if BASH_READ_ONLY_MIGRATION in applied:
        return False

    from vibe.core.tools.builtins.bash import default_read_only_commands

    bash_tools["allowlist"] = sorted(set(allowlist) | set(default_read_only_commands()))
    data["applied_migrations"] = [*applied, BASH_READ_ONLY_MIGRATION]
    return True


def _migrate_model_renames(data: dict[str, Any]) -> bool:
    """Migrate the legacy official model alias without changing custom bindings."""
    models = data.get("models", [])
    if isinstance(models, dict):
        model_entries = models.values()
        target_occupied = _CURRENT_MODEL_ALIAS in models
    elif isinstance(models, list):
        model_entries = models
        target_occupied = False
    else:
        model_entries = ()
        target_occupied = False

    legacy_model: dict[str, Any] | None = None
    changed = False
    for model in model_entries:
        if not isinstance(model, dict):
            continue

        alias = model.get("alias")
        if alias == _LEGACY_MODEL_ALIAS and legacy_model is None:
            legacy_model = model
        if alias != _CURRENT_MODEL_ALIAS:
            continue

        target_occupied = True
        if model.get("name") == _OFFICIAL_MODEL_NAME and "supports_images" not in model:
            model["supports_images"] = True
            changed = True

    applied: list[str] = data.get("applied_migrations", [])
    if MODEL_RENAME_MIGRATION in applied:
        return changed

    active_is_legacy = data.get("active_model") == _LEGACY_MODEL_ALIAS
    if legacy_model is None and not active_is_legacy:
        return changed

    if legacy_model is None:
        data["active_model"] = _CURRENT_MODEL_ALIAS
    else:
        _rename_official_model_binding(
            data, models, legacy_model, target_occupied=target_occupied
        )

    data["applied_migrations"] = [*applied, MODEL_RENAME_MIGRATION]
    return True


def _rename_official_model_binding(
    data: dict[str, Any],
    models: object,
    model: dict[str, Any],
    *,
    target_occupied: bool,
) -> None:
    if model.get("name") != _OFFICIAL_MODEL_NAME or target_occupied:
        return

    if isinstance(models, dict):
        if _LEGACY_MODEL_ALIAS not in models:
            return
        models[_CURRENT_MODEL_ALIAS] = models.pop(_LEGACY_MODEL_ALIAS)

    model.update(
        alias=_CURRENT_MODEL_ALIAS,
        temperature=1.0,
        input_price=1.5,
        output_price=7.5,
        thinking="high",
    )
    if "supports_images" not in model:
        model["supports_images"] = True
    if data.get("active_model") == _LEGACY_MODEL_ALIAS:
        data["active_model"] = _CURRENT_MODEL_ALIAS


def _migrate_renamed_tools(data: dict[str, Any]) -> bool:
    """Move config from old tool names to new ones, and rename them in lists."""
    changed = False

    tools = data.get("tools")
    if isinstance(tools, dict):
        for old, new in RENAMED_TOOLS.items():
            if old not in tools:
                continue
            old_config = tools.pop(old)
            changed = True
            # Prefer an already-present new key; don't clobber it.
            if new not in tools:
                if isinstance(old_config, dict):
                    for dropped in DROPPED_TOOL_OPTIONS.get(new, ()):
                        old_config.pop(dropped, None)
                tools[new] = old_config

    for list_key in ("enabled_tools", "disabled_tools"):
        names = data.get(list_key)
        if not isinstance(names, list):
            continue
        renamed = [RENAMED_TOOLS.get(name, name) for name in names]
        if renamed != names:
            data[list_key] = renamed
            changed = True

    return changed
