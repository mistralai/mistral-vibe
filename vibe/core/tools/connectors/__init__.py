from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vibe.core.tools.connectors.connector_registry import (
        ConnectorAuthAction,
        ConnectorRegistry,
    )
    from vibe.core.tools.connectors.counts import compute_connector_counts

__all__ = ["ConnectorAuthAction", "ConnectorRegistry", "compute_connector_counts"]


def __getattr__(name: str) -> Any:
    if name in {"ConnectorAuthAction", "ConnectorRegistry"}:
        from vibe.core.tools.connectors import connector_registry

        return getattr(connector_registry, name)

    if name == "compute_connector_counts":
        from vibe.core.tools.connectors.counts import compute_connector_counts

        return compute_connector_counts

    raise AttributeError(name)
