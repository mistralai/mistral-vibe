from __future__ import annotations

from pathlib import Path
from typing import Any

from vibe.core.agents._migration import migrate_agent_profile_files
from vibe.core.config._migration import migrate_config_layers
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.layers.default import DefaultConfigLayer
from vibe.core.config.layers.environment import EnvironmentLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.layers.project import ProjectConfigLayer
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.vibe_schema import VibeConfigSchema
from vibe.core.paths import dedup_paths
from vibe.core.utils import configure_ssl_context


async def build_default_orchestrator(
    data: dict[str, Any] | None = None,
) -> ConfigOrchestrator[VibeConfigSchema]:
    """Build the CLI ConfigOrchestrator with the standard layer stack.

    Priority order (lowest to highest): schema defaults, discovered config,
    selected TOML, VIBE_* env vars, runtime overrides, agent profile overrides.
    The selected TOML is the project config when one is discovered and trusted,
    otherwise the user config.
    """
    user_layer = UserConfigLayer()
    project_layer = ProjectConfigLayer()

    toml_layer: ConfigLayer[RawConfig]
    if await project_layer.resolve_trust() and project_layer.is_file_discovered:
        toml_layer = project_layer
    else:
        toml_layer = user_layer

    def default_layer_resolver() -> ConfigLayer[RawConfig]:
        return toml_layer

    layers = [
        # TODO clean up code and integrate those layers
        DefaultConfigLayer(schema=VibeConfigSchema),
        # DiscoveredConfigLayer(),
        toml_layer,
        EnvironmentLayer(schema=VibeConfigSchema),
        OverridesLayer(data=data or {}),
        # AgentProfileLayer(),
    ]

    await migrate_config_layers(layers)

    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=layers,
        default_layer_resolver=default_layer_resolver,
    )
    configure_ssl_context(
        enable_system_trust_store=orchestrator.config.enable_system_trust_store
    )
    migrate_agent_profile_files(_agent_profile_search_paths(orchestrator.config))
    return orchestrator


def _agent_profile_search_paths(config: VibeConfigSchema) -> list[Path]:
    mgr = get_harness_files_manager()
    return dedup_paths([
        *(p for p in config.agent_paths if p.is_dir()),
        *mgr.project_agents_dirs,
        *mgr.user_agents_dirs,
    ])
