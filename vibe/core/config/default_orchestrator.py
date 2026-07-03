from __future__ import annotations

from typing import Any

from vibe.core.config._migration import migrate_config_layers
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.layers.environment import EnvironmentLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.layers.project import ProjectConfigLayer
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.vibe_schema import VibeConfigSchema


async def build_default_orchestrator(
    data: dict[str, Any] | None = None,
) -> ConfigOrchestrator[VibeConfigSchema]:
    """Build the CLI ConfigOrchestrator with the standard layer stack.

    Priority order (lowest to highest): schema defaults, selected TOML,
    VIBE_* env vars, runtime overrides. The selected TOML is the project config
    when one is discovered and trusted, otherwise the user config.
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
        toml_layer,
        EnvironmentLayer(schema=VibeConfigSchema),
        OverridesLayer(data=data or {}),
    ]

    await migrate_config_layers(layers)

    return await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=layers,
        default_layer_resolver=default_layer_resolver,
    )
