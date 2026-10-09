"""/plugins with an empty catalogue reports it instead of opening the browser."""

from __future__ import annotations

from e2e.app_server.plugins import METHODS, catalog
from e2e.app_server.scenario import Timeline

handshake = {"plugin_catalog/read": catalog([])}
request_methods = METHODS

timeline: Timeline = ["/plugins\r"]

screen_contains = {"rust": ("No plugins are installed for this session.",)}
screen_excludes = {"rust": ("Plugins opened...",)}
