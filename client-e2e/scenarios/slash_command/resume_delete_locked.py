"""Pressing `d` on a locked session row refuses the delete with an inline warning notice."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SAVED = "00000000-0000-4000-8000-000000000003"

handshake = {
    "session/lease/subscribe": {
        "held": [{"sessionId": _SAVED, "leaseHolder": {"processId": 4242}}]
    }
}
request_methods = {"session/lease/subscribe"}

# Enter submits /resume, Up selects the locked row, and `d` refuses locally
# with the busy inline notice, without a session/delete request.
timeline: Timeline = ["/resume", "\r", "\x1b[A", "d"]

screen_contains = ("open in another Vibe process",)
screen_excludes = ("⚠",)
