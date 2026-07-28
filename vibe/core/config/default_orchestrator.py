from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from vibe.core.agents._migration import migrate_agent_profile_files
from vibe.core.config._migration import migrate_config_layers
from vibe.core.config.harness_files import (
    HarnessFilesManager,
    get_harness_files_manager,
)
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.layers.default import DefaultConfigLayer
from vibe.core.config.layers.environment import EnvironmentLayer
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.layers.project import ProjectConfigLayer
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.vibe_schema import VibeConfigSchema
from vibe.core.paths import dedup_paths
from vibe.utils.http import configure_ssl_context


async def build_default_orchestrator(
    data: dict[str, Any] | None = None,
    *,
    harness_files: HarnessFilesManager | None = None,
    require_api_key: bool = True,
) -> ConfigOrchestrator[VibeConfigSchema]:
    """Build the CLI ConfigOrchestrator with the standard layer stack.

    Priority order (lowest to highest): schema defaults, discovered config,
    selected TOML, GrowthBook experiments, VIBE_* env vars, runtime overrides,
    agent profile overrides. The selected TOML is the project config when one
    is discovered and trusted, otherwise the user config.
    """
    manager = harness_files or get_harness_files_manager()
    toml_layer = await _select_persistence_layer(manager)

    def default_layer_resolver() -> ConfigLayer[RawConfig]:
        return toml_layer

    layers = [
        # TODO clean up code and integrate those layers
        DefaultConfigLayer(schema=VibeConfigSchema),
        # DiscoveredConfigLayer(),
        toml_layer,
        GrowthbookLayer(),
        EnvironmentLayer(schema=VibeConfigSchema),
        OverridesLayer(data=data or {}),
        # AgentProfileLayer(),
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
    search_paths = await asyncio.to_thread(
        _agent_profile_search_paths, orchestrator.config, manager
    )
    await asyncio.to_thread(migrate_agent_profile_files, search_paths)
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


async def _select_persistence_layer(
    manager: HarnessFilesManager,
) -> ConfigLayer[RawConfig]:
    if "project" in manager.sources:
        project = ProjectConfigLayer(
            path=manager.cwd or Path.cwd(), trust_store=manager.trust_store
        )
        if await project.resolve_trust() and (
            project.is_file_discovered or "user" not in manager.sources
        ):
            return project
    if "user" in manager.sources:
        return UserConfigLayer(path=manager.user_config_file)
    return OverridesLayer(data={}, name="ephemeral")


def _agent_profile_search_paths(
    config: VibeConfigSchema, manager: HarnessFilesManager
) -> list[Path]:
    return dedup_paths([
        *(p for p in config.agent_paths if p.is_dir()),
        *manager.project_agents_dirs,
        *manager.user_agents_dirs,
    ])
