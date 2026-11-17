"""`/raw` with an unknown argument prints its usage and leaves the display formatted."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.raw._answer import ANSWERED

screen_contains = ("Usage: /raw [on|off]",)
screen_excludes = ("## Setup", "Raw mode on")

timeline: Timeline = [*ANSWERED, "/raw maybe\r"]
