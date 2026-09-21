"""`--resume` with no id opens the saved-session picker at startup."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--resume",)
timeline: Timeline = []
