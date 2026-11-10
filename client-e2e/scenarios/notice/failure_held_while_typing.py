"""A failure landing while the user types survives keys until it has been on screen long enough to read (VIBE-5543)."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, advance

handshake = {
    "session/resume": {"error": {"code": "internal_error", "message": "Resume failed"}}
}
request_methods = {"session/resume"}

# Enter fails the resume, an immediate key keeps the notice, a key after the hold clears it.
timeline: Timeline = ["/resume", "\r", "\x1b[A", "\r", "a", advance(1500), "b"]

screen_excludes = ("Failed to resume session",)
