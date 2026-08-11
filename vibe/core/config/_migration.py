from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config.layer import (
    ConfigLayer,
    EmptyLayerError,
    LayerNotLoadedError,
    RawConfig,
    UntrustedLayerError,
)
from vibe.core.config.layers._base import BaseTomlConfigLayer
from vibe.core.config.patch import ConfigPatch, ReplaceOperationPatch
from vibe.core.utils import name_matches

# One-shot id: syncs an existing bash allowlist up to the current default
# read-only commands once, so users keep the ability to remove any of them.
BASH_READ_ONLY_MIGRATION = "bash_read_only_defaults_v1"

# Old tool name -> new tool name. The new tools replaced these in-place, so
# existing user configs keyed by the old names need their settings moved over.
RENAMED_TOOLS: dict[str, str] = {"read": "read_file", "search_replace": "edit"}

RENAMED_AGENTS: dict[str, str] = {"default": "ask"}

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
    changed |= _migrate_devstral_small_thinking(data)
    changed |= _migrate_renamed_tools(data)
    changed |= _migrate_renamed_agents(data)
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
    """Rename devstral-2 to mistral-medium-3.5 and update its config."""
    changed = False
    models = data.get("models", [])
    model_entries: Iterable[dict[str, Any]]
    if isinstance(models, dict):
        model_entries = [model for model in models.values() if isinstance(model, dict)]
    elif isinstance(models, list):
        model_entries = [model for model in models if isinstance(model, dict)]
    else:
        model_entries = []

    for model in model_entries:
        if (
            model.get("name") == "mistral-vibe-cli-latest"
            and model.get("alias") == "devstral-2"
        ):
            model["alias"] = "mistral-medium-3.5"
            model["temperature"] = 1.0
            model["input_price"] = 1.5
            model["output_price"] = 7.5
            model["thinking"] = "high"
            changed = True

            if isinstance(models, dict):
                _rekey_model_entry(
                    models, old_alias="devstral-2", new_alias="mistral-medium-3.5"
                )

        if (
            model.get("name") == "mistral-vibe-cli-latest"
            and model.get("alias") == "mistral-medium-3.5"
            and "supports_images" not in model
        ):
            model["supports_images"] = True
            changed = True

    if data.get("active_model") == "devstral-2":
        data["active_model"] = "mistral-medium-3.5"
        changed = True

    return changed


def _migrate_devstral_small_thinking(data: dict[str, Any]) -> bool:
    """Force devstral-small to thinking='off'; it has no reasoning support.

    Stale configs that carried a non-'off' thinking level made the backend send
    a ``reasoning_effort`` the model rejects with an API error.
    """
    models = data.get("models", [])
    if isinstance(models, dict):
        model_entries = models.values()
    elif isinstance(models, list):
        model_entries = models
    else:
        return False

    changed = False
    for model in model_entries:
        if not isinstance(model, dict):
            continue
        if model.get("name") == "devstral-small-latest" and (
            model.get("thinking") != "off"
        ):
            model["thinking"] = "off"
            changed = True
    return changed


def _rekey_model_entry(
    models: dict[str, Any], *, old_alias: str, new_alias: str
) -> None:
    if old_alias not in models or old_alias == new_alias:
        return
    models[new_alias] = models.pop(old_alias)


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


def _migrate_renamed_agents(data: dict[str, Any]) -> bool:
    changed = False

    default_agent = data.get("default_agent")
    if isinstance(default_agent, str) and default_agent in RENAMED_AGENTS:
        data["default_agent"] = RENAMED_AGENTS[default_agent]
        changed = True

    for list_key in ("enabled_agents", "disabled_agents", "installed_agents"):
        names = data.get(list_key)
        if not isinstance(names, list):
            continue
        renamed = [
            RENAMED_AGENTS.get(name, name) if isinstance(name, str) else name
            for name in names
        ]
        if renamed != names:
            data[list_key] = renamed
            changed = True

    if "default_agent" not in data:
        ask = RENAMED_AGENTS["default"]
        if _agent_filters_exclude(
            data, BuiltinAgentName.ACCEPT_EDITS
        ) and not _agent_filters_exclude(data, ask):
            data["default_agent"] = ask
            changed = True

    return changed


def _agent_filters_exclude(data: dict[str, Any], agent: str) -> bool:
    enabled = data.get("enabled_agents")
    if isinstance(enabled, list) and enabled:
        return not name_matches(agent, enabled)
    disabled = data.get("disabled_agents")
    if isinstance(disabled, list):
        return name_matches(agent, disabled)
    return False
