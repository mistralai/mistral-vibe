"""`--resume <id>` resumes a specific saved session at startup."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--resume", "00000000-0000-4000-8000-000000000003")
timeline: Timeline = []
