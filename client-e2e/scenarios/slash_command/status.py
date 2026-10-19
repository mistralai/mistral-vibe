from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/status\r"]

request_methods = {"providerAuth/read"}

# HOME is pinned so the `~` abbreviation of the credential source label
# stays deterministic; the fixture names an env file under it.
env = {"HOME": "/home/e2e-user"}
