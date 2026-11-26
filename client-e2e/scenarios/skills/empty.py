"""/skills with nothing installed and no Mistral sign-in still opens, on a note per tab."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, catalog, installed, registry_on

handshake = {
    **registry_on(),
    "skills/installed": installed([]),
    "skills/catalog": catalog(authenticated=False),
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\t"]

screen_contains = ("Sign in to Mistral to browse shared skills",)
