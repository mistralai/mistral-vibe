"""A failed `--resume <id>` keeps the fresh session and settles the resume status into an error row."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

client_args = ("--resume", "00000000-0000-4000-8000-000000000003")
handshake = {
    "session/resume": {
        "error": {"code": "session_locked", "message": "Session is open elsewhere"}
    }
}
timeline: Timeline = []

screen_contains = ("✕ Error: Failed to resume session: Session is open elsewhere",)
screen_excludes = ("Resuming session", "Resumed session")
