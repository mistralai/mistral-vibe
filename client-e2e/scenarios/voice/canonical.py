"""Server-authoritative values win over a draft shadowed by another config layer."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {"config/write": {"runtime": {"config": {"theme": "ansi-dark"}}}}
request_methods = {"telemetry/record"}
screen_contains = {"rust": ("Voice mode: On",)}
screen_excludes = {"rust": ("Voice mode disabled.",)}
timeline: Timeline = ["/voice\r", " ", "\x1b", "/voice\r"]
