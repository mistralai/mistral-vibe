"""Deleting the only listed session closes `/resume` and confirms the deletion after it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SAVED = "00000000-0000-4000-8000-000000000003"

handshake = {
    "session/list": {
        "items": [
            {
                "id": _SAVED,
                "rootSessionId": _SAVED,
                "parentSessionId": None,
                "title": "Saved implementation",
                "preview": "Continue the implementation",
                "status": {"type": "idle"},
                "createdAt": 1_787_821_243_000,
                "updatedAt": 1_787_821_245_000,
                "cwd": "/workspace",
            }
        ],
        "nextCursor": None,
        "previousCursor": None,
        "continueSessionId": None,
    }
}

request_methods = {"session/delete"}

# The current session is not listed, so the picker opens on the saved one.
timeline: Timeline = ["/resume", "\r", "d", "d"]

capture_startup = False
capture_steps = {1, 3}
settle_per_key = True
screen_contains = (
    "Deleted session 00000000",
    "No saved sessions left for this directory.",
)
