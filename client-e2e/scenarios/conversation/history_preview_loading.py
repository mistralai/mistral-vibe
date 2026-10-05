"""Preview history prepares eagerly, and every session switch starts at the bottom."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SESSION = "00000000-0000-4000-8000-000000000003"

_HISTORY = [
    {
        "id": f"preview-{index}",
        "sessionId": _SESSION,
        "type": "message",
        "role": "assistant",
        "generationStatus": "completed",
        "content": [{"type": "text", "text": f"Preview message {index:02d}"}],
    }
    for index in range(80)
]
handshake = {
    "session/read": {"state": {"history": _HISTORY}},
    "session/resume": {"state": {"history": _HISTORY}},
}
request_methods = {"session/read", "session/history/list"}
capture_startup = False
screen_contains = {"rust": ("Preview message 79", "Resumed session")}
screen_excludes = {"rust": ("Preview message 00", "Load more", "load more messages")}

timeline: Timeline = [
    "/resume\r",
    "\x1b[A",
    "\x1b[1;2A" * 80,
    "\x1b[B",
    "\x1b[A",
    "\x1b[1;2A" * 80,
    "\r",
]
