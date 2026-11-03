"""A narrow terminal turns both gauges into one-cell levels next to their percentages."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize
from scenarios.footer.usage_gauge import handshake as handshake

timeline: Timeline = [resize(40, 36)]
screen_contains = ("usage ▃ 40% · ctx   0%",)
screen_excludes = ("0/600k", "██")
