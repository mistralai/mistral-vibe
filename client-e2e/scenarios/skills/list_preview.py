"""The list shows the highlighted skill's description above its rows, an installed one's and a shared one's alike, and never fetches a body."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, catalog, entry, installed, registry_on, skill

handshake = {
    **registry_on(),
    "skills/installed": installed([skill("lint", description="Check the style")]),
    "skills/catalog": catalog([entry("translate", 4, "workspace", "Translate docs")]),
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\t"]

screen_contains = ("Translate docs",)
screen_excludes = ("Check the style",)
