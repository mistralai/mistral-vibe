from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from vibe.core.config._migration import migrate_config_layers
from vibe.core.config.harness_files import (
    HarnessFilesManager,
    get_harness_files_manager,
)
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.layers.admin import AdminConfigLayer
from vibe.core.config.layers.agent_profile import AgentProfileLayer
from vibe.core.config.layers.default import DefaultConfigLayer
from vibe.core.config.layers.environment import EnvironmentLayer
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.layers.project import ProjectConfigLayer
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.vibe_schema import VibeConfigSchema
from vibe.utils.http import configure_ssl_context


async def build_default_orchestrator(
    data: dict[str, Any] | None = None,
    *,
    harness_files: HarnessFilesManager | None = None,
    require_api_key: bool = True,
) -> ConfigOrchestrator[VibeConfigSchema]:
    """Build the CLI ConfigOrchestrator with the standard layer stack.

    Priority order (lowest to highest): schema defaults, GrowthBook experiments,
    user TOML, project TOML, VIBE_* env vars, runtime overrides, agent profile
    overrides, enforced admin config. The agent-profile slot ships empty and is
    filled in place by AgentManager; the admin layer stays on top so an enforced
    org config shadows every layer below it. Both the user and project TOML
    layers are installed together when
    their sources are enabled, so a trusted project config inherits unspecified
    values from the user config and both TOML layers override GrowthBook
    assignments. The default persistence target is the project layer when one is
    discovered and trusted, otherwise the user layer, otherwise an ephemeral
    fallback.
    """
    manager = harness_files or get_harness_files_manager()
    user_layer = (
        UserConfigLayer(path=manager.user_config_file)
        if "user" in manager.sources
        else None
    )
    project_layer = (
        ProjectConfigLayer(
            path=manager.cwd or Path.cwd(), trust_store=manager.trust_store
        )
        if manager.project_source_enabled
        else None
    )
    override_layer = OverridesLayer(data=data or {})

    def default_layer_resolver() -> ConfigLayer[RawConfig]:
        if project_layer is not None and project_layer.is_trusted:
            if project_layer.is_file_discovered or user_layer is None:
                return project_layer
        if user_layer is not None:
            return user_layer
        return override_layer

    layers = [
        DefaultConfigLayer(schema=VibeConfigSchema),
        GrowthbookLayer(),
        *([user_layer] if user_layer is not None else []),
        *([project_layer] if project_layer is not None else []),
        EnvironmentLayer(schema=VibeConfigSchema),
        override_layer,
        # Empty slot filled in place by AgentManager when a profile is selected
        AgentProfileLayer(),
        # Highest priority: org-enforced config populated at runtime.
        AdminConfigLayer(),
    ]

    await migrate_config_layers(layers)

    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=layers,
        default_layer_resolver=default_layer_resolver,
        validation_context={"require_api_key": require_api_key},
    )
    await asyncio.to_thread(
        configure_ssl_context,
        enable_system_trust_store=orchestrator.config.enable_system_trust_store,
    )
    return orchestrator


async def build_user_config_orchestrator() -> ConfigOrchestrator[VibeConfigSchema]:
    """Build a user-config-only orchestrator for configuration management."""
    manager = get_harness_files_manager()
    user_layer = UserConfigLayer(path=manager.user_config_file)
    await migrate_config_layers([user_layer])
    return await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[user_layer],
        default_layer_resolver=lambda: user_layer,
        validation_context={"require_api_key": False},
    )
