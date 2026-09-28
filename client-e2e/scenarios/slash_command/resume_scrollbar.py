"""An overflowing `/resume` list shows its scrollbar."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline


def _session(index: int) -> dict[str, object]:
    session_id = f"00000000-0000-4000-8000-{index:012d}"
    title = f"Saved session {index:02d}"
    if index == 1:
        title += " with a long title that stays inside the list instead of crossing its scrollbar"
    return {
        "id": session_id,
        "rootSessionId": session_id,
        "parentSessionId": None,
        "title": title,
        "preview": f"Continue saved session {index:02d}",
        "status": {"type": "idle"},
        "createdAt": 1_787_821_243_000 - index * 1_000,
        "updatedAt": 1_787_821_245_000 - index * 1_000,
        "cwd": "/workspace",
    }


handshake = {
    "session/list": {
        "items": [_session(index) for index in range(1, 25)],
        "nextCursor": None,
        "previousCursor": None,
        "continueSessionId": "00000000-0000-4000-8000-000000000001",
    }
}
timeline: Timeline = ["/resume\r"]
capture_startup = False
