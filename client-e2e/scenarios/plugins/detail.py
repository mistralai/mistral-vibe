"""A plugin's detail leads with its description, then its facts under dim labels, then its components by kind in color."""

from __future__ import annotations

from e2e.app_server.plugins import METHODS, catalog, entry
from e2e.app_server.scenario import Timeline, take_snapshot

_VIBE = entry(
    "vibe",
    "bc8a8e60fb36cc89b5fe19578a8459ae",
    description="Skills and components shipped with the Vibe CLI.",
    components=[
        ("skill", "vibe:create-plugin"),
        ("skill", "vibe:import"),
        ("tool", "vibe:run"),
    ],
)

handshake = {"plugin_catalog/read": catalog([_VIBE])}
request_methods = METHODS

timeline: Timeline = ["/plugins\r", "\r", take_snapshot("detail")]

screen_contains = (
    "Author",
    "Components",
    "Skills      vibe:create-plugin, vibe:import",
)
