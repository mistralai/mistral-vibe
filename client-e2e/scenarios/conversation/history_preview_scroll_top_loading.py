"""Scrolling a `/resume` preview to its top pages the older history in automatically."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SESSION = "00000000-0000-4000-8000-000000000003"


def _page(start: int, end: int) -> list[dict[str, object]]:
    return [
        {
            "id": f"preview-{index:02d}",
            "sessionId": _SESSION,
            "type": "message",
            "role": "assistant",
            "generationStatus": "completed",
            "content": [{"type": "text", "text": f"Preview message {index:02d}"}],
        }
        for index in range(start, end)
    ]


handshake = {
    "session/read": {
        "state": {"history": _page(20, 40), "historyBeforeCursor": "preview-20"}
    },
    "session/history/list": {"items": _page(0, 20), "nextCursor": None},
}
request_methods = {"session/read", "session/history/list"}
capture_startup = False
# Each key settles, so a page landing mid-burst cannot race the remaining keys.
settle_per_key = True
screen_contains = {"rust": ("Preview message 00",)}
screen_excludes = {"rust": ("Load more", "load more messages")}

_TO_TOP = "\x1b[1;2A" * 60

timeline: Timeline = ["/resume\r", "\x1b[A", _TO_TOP]
