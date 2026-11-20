"""On a local skill's page, with no row to move to, the arrows scroll the body and the footer says so; Shift+Down still scrolls the conversation."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, catalog, installed, registry_on, skill

_STEPS = "\n\n".join(f"Step {step}: check the release gate." for step in range(1, 61))

handshake = {
    **registry_on(),
    "skills/installed": installed([skill("deploy", prompt=f"# deploy\n\n{_STEPS}")]),
    "skills/catalog": catalog(),
}
request_methods = METHODS

_DOWN = "\x1b[B"

timeline: Timeline = ["/skills\r", "\r", _DOWN * 6, "jjj"]

screen_contains = ("Step 5:", "↑↓/jk scroll")
screen_excludes = ("Step 1:",)
