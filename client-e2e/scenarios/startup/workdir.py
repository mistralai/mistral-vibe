"""`--workdir` changes the process working directory before startup."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--workdir", "sub")
screen_contains = ("notes.md",)
timeline: Timeline = ["@notes"]
