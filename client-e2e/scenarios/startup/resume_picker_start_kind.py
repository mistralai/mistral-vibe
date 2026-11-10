"""`--resume` with the picker starts a normal session: it is the user's own until they pick another."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

client_args = ("--resume",)
request_methods = {"session/start"}
timeline: Timeline = [take_snapshot()]
