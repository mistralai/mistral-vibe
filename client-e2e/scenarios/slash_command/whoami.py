from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/whoami\r"]

request_methods = {"providerAuth/read"}
screen_contains = (
    "Who am I",
    "No Mistral account information is available.",
    "Credential source (MISTRAL_API_KEY): Process environment",
)
