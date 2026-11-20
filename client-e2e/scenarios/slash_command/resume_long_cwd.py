"""A long cwd in the `/resume` header is elided from the start inside the border."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_ID = "00000000-0000-4000-8000-000000000003"
_CWD = (
    "/workspace/"
    + "/".join(f"nested-directory-{index}" for index in range(8))
    + "/project"
)

handshake = {
    "session/list": {
        "items": [
            {
                "id": _ID,
                "rootSessionId": _ID,
                "parentSessionId": None,
                "title": "Saved implementation",
                "preview": "Continue the implementation",
                "status": {"type": "idle"},
                "createdAt": 1_787_821_243_000,
                "updatedAt": 1_787_821_245_000,
                "cwd": _CWD,
            }
        ],
        "nextCursor": None,
        "previousCursor": None,
    }
}
timeline: Timeline = ["/resume\r"]

screen_contains = ("nested-directory-7/project",)
screen_excludes = ("/workspace",)
