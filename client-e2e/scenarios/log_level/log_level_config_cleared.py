"""Clearing the config badge removes log_level from config.toml and says so."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"

handshake = {"config/read": {"config": {"logLevel": "ERROR"}}}

capture_startup = False
# The picker opens on ERROR; focus its config badge, toggle it off, apply.
timeline: Timeline = ["/log-level", "\r", "\x1b[C", "\r", _ESCAPE]

screen_contains = ("config.toml cleared  (effective: WARNING)",)
