"""The log_level read from config at startup is the effective level the picker shows."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {"config/read": {"config": {"logLevel": "ERROR"}}}

capture_startup = False
timeline: Timeline = ["/log-level", "\r"]

screen_contains = ("Effective: ERROR  (config.toml: ERROR)",)
