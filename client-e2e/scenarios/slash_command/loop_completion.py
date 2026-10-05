"""Loop autocomplete shows the natural-language schedule and prompt structure."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/loo"]
screen_contains = {"rust": ("/loop [schedule] [prompt]",)}
