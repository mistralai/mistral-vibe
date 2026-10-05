"""Scrolling to the top of a resumed session pages all older history in automatically."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SESSION = "00000000-0000-4000-8000-000000000003"


def _page(start: int, end: int) -> list[dict[str, object]]:
    return [
        {
            "id": f"saved-{index:02d}",
            "sessionId": _SESSION,
            "type": "message",
            "role": "assistant",
            "generationStatus": "completed",
            "content": [{"type": "text", "text": f"Saved message {index:02d}"}],
        }
        for index in range(start, end)
    ]


handshake = {
    "session/resume": {
        "state": {"history": _page(40, 60), "historyBeforeCursor": "saved-40"}
    },
    "session/history/list": [
        {"items": _page(20, 40), "nextCursor": "saved-20"},
        {"items": _page(0, 20), "nextCursor": None},
    ],
}
client_args = ("-c",)
request_methods = {"session/resume", "session/history/list"}
capture_startup = False
# Each key settles, so a page landing mid-burst cannot race the remaining keys.
settle_per_key = True
screen_contains = {"rust": ("Saved message 00", "Saved message 01")}
screen_excludes = {"rust": ("Load more", "load more messages")}

_TO_TOP = "\x1b[1;2A" * 60

timeline: Timeline = [_TO_TOP]
