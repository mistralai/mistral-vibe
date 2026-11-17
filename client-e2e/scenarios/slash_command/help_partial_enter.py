"""Enter on a partial `/help` prefix completes and runs it, then records the builtin command use."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

request_methods = {"telemetry/record"}
# The help list outgrows the screen, so only its tail is checked: `/copy` has scrolled away.
screen_contains = (
    "/help: Show help message",
    "/whoami: Display the Mistral signed-in user",
)

timeline: Timeline = ["/hel", "\r"]  # typos:disable-line
