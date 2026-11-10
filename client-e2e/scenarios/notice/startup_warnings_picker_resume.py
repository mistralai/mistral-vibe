"""Resuming a session from the picker drops the startup warnings box of the session it leaves (VIBE-5543)."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

handshake = {
    "runtime/read": {
        "runtime": {
            "issues": [
                {"file": ".vibe/hooks.toml", "message": "Failed to parse: bad toml"}
            ]
        }
    }
}
request_methods = {"session/resume"}

# Enter submits /resume, Up selects the saved session, the next Enter resumes it.
timeline: Timeline = ["/resume", "\r", "\x1b[A", "\r"]

screen_contains = ("Resumed session",)
screen_excludes = ("Warnings", "Failed to parse: bad toml")
