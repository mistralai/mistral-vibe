"""Enter on a partial `/help` prefix completes and runs it, then records the builtin command use."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

request_methods = {"telemetry/record"}
capture_startup = False
screen_contains = ("/help: Show help message", "/copy: Copy the last agent message")

timeline: Timeline = ["/hel", "\r"]  # typos:disable-line
