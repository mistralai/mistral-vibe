"""A lease held by this client's own app-server process does not lock the row."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SAVED = "00000000-0000-4000-8000-000000000003"

handshake = {
    "initialize": {"processId": 4242},
    "session/lease/subscribe": {
        "held": [{"sessionId": _SAVED, "leaseHolder": {"processId": 4242}}]
    },
}
request_methods = {"session/resume"}

# Enter submits /resume (the picker highlights the current session), Up
# selects the row our own app-server holds a lease on, and its Enter resumes
# instead of refusing: the holder is this client's own server process.
timeline: Timeline = ["/resume", "\r", "\x1b[A", "\r"]

capture_startup = False
capture_steps = {2, 3}
screen_contains = ("Resumed session",)
screen_excludes = ("open in another Vibe process",)
