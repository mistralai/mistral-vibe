"""A failure too wide for the loading row wraps into a block capped at three rows under it, above the picker (VIBE-5543)."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SAVED = "00000000-0000-4000-8000-000000000003"
_REASON = (
    "Session store is unavailable: the lock file could not be acquired because another process holds it. "
    * 5
)

handshake = {
    "session/lease/subscribe": {"held": []},
    f"session/delete#{_SAVED}": {
        "error": {"code": "internal_error", "message": _REASON.strip()}
    },
}
request_methods = {"session/delete"}

# Enter submits /resume, Up selects the saved session, `d` twice deletes it and the server refuses.
timeline: Timeline = ["/resume", "\r", "\x1b[A", "d", "d"]

screen_contains = (
    "Failed to delete session: Session store is unavailable",
    "…",
    "Enter select",
)
screen_excludes = ("✕",)
