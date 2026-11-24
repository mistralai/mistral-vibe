"""A long failure on the Sessions page stays on its one footer row, ellipsized (VIBE-5543)."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_REASON = (
    "Session store is unavailable: the lock file could not be acquired because another process holds it. "
    * 5
)

handshake = {
    "session/lease/subscribe": {"held": []},
    "session/archive": {
        "error": {"code": "internal_error", "message": _REASON.strip()}
    },
}
request_methods = {"session/archive"}

# Enter submits /resume, Up selects the saved session, `a` archives it and the server refuses.
timeline: Timeline = ["/resume", "\r", "\x1b[A", "a"]

screen_contains = ("Session store is unavailable", "…")
screen_excludes = ("✕",)
