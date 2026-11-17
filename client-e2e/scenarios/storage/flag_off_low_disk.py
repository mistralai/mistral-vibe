"""With the `vibe_storage_usage` flag off, a low disk at startup shows no notice and reads no storage."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.storage._fixtures import usage

handshake = {"storage/usage/read": usage(low=True)}
request_methods = {"storage/usage/read"}

timeline: Timeline = ["a", take_snapshot()]

screen_excludes = ("Low disk space", "Warnings")
