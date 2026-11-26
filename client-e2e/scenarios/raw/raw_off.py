"""`/raw on` then `/raw` restore formatted Markdown, recording each toggle once."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.raw._answer import ANSWERED

request_methods = {"telemetry/record"}
screen_contains = ("Raw mode off: agent messages show formatted Markdown", "Setup")
screen_excludes = ("## Setup", "**Install**", "```bash")

timeline: Timeline = [*ANSWERED, "/raw on\r", "/raw\r"]
