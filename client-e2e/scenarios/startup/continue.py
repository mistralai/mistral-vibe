"""`--continue` asks the server for this terminal's last session in the folder and resumes it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--continue",)
request_methods = {"session/continue"}
timeline: Timeline = []
