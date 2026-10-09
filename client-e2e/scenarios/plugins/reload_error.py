"""A failing plugin/reload reports the server's message instead of a diff."""

from __future__ import annotations

from e2e.app_server.plugins import METHODS, catalog, sample_plugins
from e2e.app_server.scenario import Timeline

handshake = {
    "plugin_catalog/read": catalog(sample_plugins()),
    "plugin/reload": {
        "error": {"code": "internal_error", "message": "Plugin rescan failed"}
    },
}
request_methods = METHODS

timeline: Timeline = ["/reload-plugins\r"]

screen_contains = {"rust": ("Failed to reload plugins: Plugin rescan failed",)}
screen_excludes = {"rust": ("Plugins reloaded",)}
