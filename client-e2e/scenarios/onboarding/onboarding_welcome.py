"""`--setup` opens the onboarding wizard: welcome screen first frame."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

client_args = ("--setup",)
request_methods = {"setup/status"}
timeline: Timeline = [take_snapshot()]
