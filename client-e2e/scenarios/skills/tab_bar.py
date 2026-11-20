"""The tabs read as tabs: the open one a filled pill, the other plain, each count dimmed apart from its name."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from e2e.app_server.skills import METHODS, catalog, entry, installed, registry_on, skill

handshake = {
    **registry_on(),
    "skills/installed": installed([skill("lint"), skill("deploy")]),
    "skills/catalog": catalog([entry("translate", 4, "workspace", "Translate docs")]),
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", take_snapshot("mine"), "\t", take_snapshot("shared")]
