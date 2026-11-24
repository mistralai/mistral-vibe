"""`vibe --model ALIAS --thinking LEVEL` starts the interactive session on them, for this session only."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--model", "devstral-small", "--thinking", "high")
request_methods = {"session/start"}
timeline: Timeline = []
