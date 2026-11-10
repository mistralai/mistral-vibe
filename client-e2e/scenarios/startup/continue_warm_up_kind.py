"""`--continue` opens its warm-up session as ephemeral, so only the resumed session counts."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

client_args = ("--continue",)
request_methods = {"session/start", "session/continue"}
timeline: Timeline = [take_snapshot()]
