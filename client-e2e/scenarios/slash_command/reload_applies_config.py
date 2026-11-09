"""/reload applies the config re-read from disk: the new model window reaches the status bar."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {"config/reload": {"runtime": {"contextWindow": 128000}}}

capture_startup = False
request_methods = {"config/reload"}
# Typed first, so the golden shows the 600k window before the reload.
timeline: Timeline = ["/reload", "\r"]
screen_contains = ("Configuration reloaded", "0/128k")
