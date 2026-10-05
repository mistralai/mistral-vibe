"""Toggling back still saves the touched field, without reporting a transition."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {"config/write": {"runtime": {"config": {"theme": "ansi-dark"}}}}
request_methods = {"telemetry/record"}
screen_excludes = {
    "rust": ("Voice mode enabled.", "Voice mode disabled.", "no changes saved")
}
timeline: Timeline = ["/voice\r", " ", "\r", "\x1b"]
