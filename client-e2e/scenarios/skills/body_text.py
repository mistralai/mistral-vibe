"""A skill's page says so when its body is empty, and renders a body's markdown with its tabs expanded."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from e2e.app_server.skills import METHODS, catalog, installed, registry_on, skill

handshake = {
    **registry_on(),
    "skills/installed": installed([
        skill("lint", prompt=""),
        skill(
            "deploy",
            prompt="# deploy\n\nSteps:\n\n\tcode block\n\n1. **Build**.\n2. Ship.",
        ),
    ]),
    "skills/catalog": catalog(),
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "j", "\r", take_snapshot(), "\x1b", "k", "\r"]

screen_contains = ("1. Build.", "code block")
screen_excludes = ("**Build**",)
