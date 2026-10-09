"""A backend without plugin support answers both commands without opening the browser."""

from __future__ import annotations

from e2e.app_server.plugins import METHODS
from e2e.app_server.scenario import Timeline

handshake = {
    "plugin_catalog/read": {
        "error": {"code": "not_implemented", "message": "Plugins are not supported"}
    }
}
request_methods = METHODS

timeline: Timeline = ["/plugins\r", "/reload-plugins\r"]

screen_contains = {"rust": ("This session resolves no plugins.",)}
screen_excludes = {"rust": ("Plugins opened...",)}
