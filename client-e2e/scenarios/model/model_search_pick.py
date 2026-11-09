"""A filtered pick chains into /thinking and Enter commits both, like an unfiltered pick."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/model\r", "/", "devstral", "\r", "\r"]
screen_excludes = ("Select Model",)
