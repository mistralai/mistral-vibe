"""The pane says so when a skill's body is empty, and expands a body's tabs to their stops."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, catalog, installed, registry_on, skill

handshake = {
    **registry_on(),
    "skills/installed": installed([
        skill("lint", prompt=""),
        skill("deploy", prompt="# deploy\n\nSteps:\n\t1. Build.\n\t2. Ship."),
    ]),
    "skills/catalog": catalog(),
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "j"]

screen_contains = ("│            1. Build.",)
