from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibe.core.config import AnyVibeConfig
    from vibe.core.tools.connectors.connector_registry import ConnectorRegistry


def compute_connector_counts(
    config: AnyVibeConfig, connector_registry: ConnectorRegistry | None
) -> tuple[int, int]:
    if connector_registry is None:
        return (0, 0)
    aliases = connector_registry.get_connector_names()
    if not aliases:
        return (0, 0)
    by_name = config.connectors_by_name()
    connected = sum(
        (cfg := by_name.get(alias)) is not None
        and not cfg.disabled
        and connector_registry.is_connected(alias)
        for alias in aliases
    )
    return (connected, len(aliases))
