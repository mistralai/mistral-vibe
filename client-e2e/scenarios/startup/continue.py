"""`--continue` resumes the most recent saved session at startup."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--continue",)
timeline: Timeline = []
