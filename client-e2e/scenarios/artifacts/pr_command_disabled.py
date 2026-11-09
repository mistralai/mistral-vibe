"""Without `enable_pr_monitoring` the palette does not offer `/pr`."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {"runtime/read": {"runtime": {"config": {"enablePrMonitoring": False}}}}
screen_contains = ("/plugins",)
screen_excludes = ("pull requests and merge requests",)

timeline: Timeline = ["/p"]
