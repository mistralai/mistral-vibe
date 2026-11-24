"""Opening a long session shows its latest messages, not its first ones."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.sessions_page._sessions import listed, session

_OTHER = "00000000-0000-4000-8000-000000000003"

_HISTORY = [
    {
        "id": f"history-{index}",
        "sessionId": _OTHER,
        "type": "message",
        "role": "assistant",
        "generationStatus": "completed",
        "content": [{"type": "text", "text": f"History message {index:02d}"}],
    }
    for index in range(80)
]

handshake = {
    **listed(session(1, "Current work"), session(3, "Long session")),
    "session/resume": {"state": {"history": _HISTORY}},
}
request_methods = {"session/resume"}
screen_contains = ("History message 79", "Resumed session")
screen_excludes = ("History message 00", "Load more", "load more messages")

timeline: Timeline = ["/sessions", "\r", "\x1b[B", "\r", take_snapshot()]
