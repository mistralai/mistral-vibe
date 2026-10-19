"""`/reload` reloads the configuration without re-pinning the session's plugins."""

from __future__ import annotations

from e2e.app_server.plugins import METHODS, catalog, sample_plugins
from e2e.app_server.scenario import Timeline

handshake = {"plugin_catalog/read": catalog(sample_plugins())}
request_methods = METHODS | {"config/reload"}
screen_contains = ("Configuration reloaded",)
screen_excludes = ("Plugins reloaded",)
timeline: Timeline = ["/reload\r"]
