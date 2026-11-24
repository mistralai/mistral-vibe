"""A failed preview read says so and keeps the Sessions page usable: the list and its keys stay."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, advance, take_snapshot

_SAVED = "00000000-0000-4000-8000-000000000003"

handshake = {
    f"session/read#{_SAVED}": {
        "error": {"code": "internal_error", "message": "Preview failed"}
    }
}
request_methods = {"session/read"}
screen_contains = (
    "Saved implementation",
    "Couldn't load the conversation",
    "→/Enter open",
    "Esc close",
)

# Up highlights the saved session; once the highlight rests its read fails and the preview says so.
timeline: Timeline = ["/resume", "\r", "\x1b[A", advance(200), take_snapshot()]
