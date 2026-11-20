"""A filter pasted into the search shows the description of the skill it highlights, as a typed one does."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, catalog, entry, installed, registry_on, skill

handshake = {
    **registry_on(),
    "skills/installed": installed([skill("lint")]),
    "skills/catalog": catalog([
        entry("changelog", 2, "workspace", "Changelog notes"),
        entry("translate", 4, "workspace", "Translate docs"),
    ]),
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\t", "/", "\x1b[200~trans\x1b[201~"]

screen_contains = ("Translate docs",)
screen_excludes = ("Changelog notes",)
