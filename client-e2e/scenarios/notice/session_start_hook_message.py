"""A session_start hook's system_message is its own ✓ row; its failure stays in the startup box."""

from __future__ import annotations

from e2e.app_server.events import server_warning
from e2e.app_server.scenario import Timeline

on_request = {
    "session/start": [
        server_warning("session_start hook 'broken' failed: exited with code 7"),
        server_warning("[load-context] Context loaded", code="hook_message"),
    ]
}

timeline: Timeline = ["a"]

screen_contains = (
    "Warnings",
    "session_start hook 'broken' failed: exited with code 7",
    "[load-context] Context loaded",
)
