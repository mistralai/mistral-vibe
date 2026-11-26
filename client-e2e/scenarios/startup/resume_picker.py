"""`--resume` with no id opens the Sessions page once the session is ready."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

client_args = ("--resume",)
timeline: Timeline = [take_snapshot()]
