"""A filtered pick chains into /thinking, commits both, and records that the search was used."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/model\r", "/", "devstral", "\r", "\r"]
request_methods = {"telemetry/record"}
screen_excludes = ("Select Model",)
