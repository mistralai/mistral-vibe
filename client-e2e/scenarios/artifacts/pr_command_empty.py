"""`/pr` with no PR in the session says so and opens nothing."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {"artifacts/list": {"artifacts": []}}
request_methods = {"artifacts/list", "artifacts/sync", "artifacts/markViewed"}
screen_contains = ("No pull requests in this session",)
screen_excludes = ("Pull Requests",)

timeline: Timeline = ["/pr\r"]
