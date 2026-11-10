from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/status\r"]

request_methods = {"providerAuth/read"}
screen_contains = (
    "Agent Statistics",
    "Provider: mistral",
    "Credential (MISTRAL_API_KEY):",
    "1. Process environment · currently in use",
    "2. ~/.vibe/.env",
)

# HOME is pinned so the `~` abbreviation of the credential source label
# stays deterministic; the fixture names an env file under it.
env = {"HOME": "/home/e2e-user"}
