"""/plugins lists plugins and dropped files, opens a detail, filters, and backs out with Esc."""

from __future__ import annotations

from e2e.app_server.plugins import METHODS, catalog, dropped, sample_plugins
from e2e.app_server.scenario import Timeline

handshake = {
    "plugin_catalog/read": catalog(
        sample_plugins(),
        [
            dropped(
                "/opt/vibe/plugins/broken/plugin.json", "missing required field 'name'"
            )
        ],
    )
}
request_methods = METHODS

_ESCAPE = "\x1b[27u"

timeline: Timeline = [
    "/plugins\r",
    "j",
    "\r",
    _ESCAPE,
    "/",
    "doc",
    _ESCAPE,
    _ESCAPE,
    _ESCAPE,
]

screen_contains = {"rust": ("Plugins opened...", "Plugins closed.")}
