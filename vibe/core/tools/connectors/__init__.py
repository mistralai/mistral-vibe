from __future__ import annotations

import os

from vibe.core.tools.connectors.connector_registry import ConnectorRegistry

CONNECTORS_ENV_VAR = "EXPERIMENTAL_ENABLE_CONNECTORS"


def connectors_enabled() -> bool:
    return os.getenv(CONNECTORS_ENV_VAR) == "1"


__all__ = ["CONNECTORS_ENV_VAR", "ConnectorRegistry", "connectors_enabled"]
