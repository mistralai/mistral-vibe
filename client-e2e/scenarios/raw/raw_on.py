"""`/raw` shows agent messages as their Markdown source and confirms it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.raw._answer import ANSWERED

screen_contains = (
    "Raw mode on: agent messages show their Markdown source",
    "## Setup",
    "- **Install** the `vibe` package",
    "```bash",
    "| `-p` | Run headless |",
)

timeline: Timeline = [
    *ANSWERED,
    take_snapshot("formatted"),
    "/raw\r",
    take_snapshot("raw"),
]
