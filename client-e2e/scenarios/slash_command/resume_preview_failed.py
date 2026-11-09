"""A failed session preview keeps the picker open so other sessions stay reachable."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"
_SAVED = "00000000-0000-4000-8000-000000000003"

handshake = {
    f"session/read#{_SAVED}": {
        "error": {"code": "internal_error", "message": "Preview failed"}
    }
}
request_methods = {"session/read"}

# Up fails on the saved session's preview, Down previews the current one again.
timeline: Timeline = ["/resume", "\r", "\x1b[A", "\x1b[B", _ESCAPE]

capture_startup = False
capture_steps = {1, 2, 3, 4}
