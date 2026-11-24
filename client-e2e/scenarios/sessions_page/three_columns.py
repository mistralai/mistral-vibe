"""Folders, sessions and the highlighted session's conversation, its end in view, side by side."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, advance, take_snapshot
from scenarios.sessions_page._sessions import listed, session

_DOCS = "00000000-0000-4000-8000-000000000002"


def _message(index: int, role: str, text: str) -> dict[str, object]:
    return {
        "id": f"docs-{index}",
        "sessionId": _DOCS,
        "type": "message",
        "role": role,
        "generationStatus": "completed",
        "content": [{"type": "text", "text": text}],
    }


_HISTORY = [
    _message(0, "user", "Refresh the docs for the new flag"),
    _message(1, "assistant", "First draft of the **docs** page."),
    *(_message(index, "user", f"Earlier prompt {index:02d}") for index in range(2, 40)),
    _message(40, "user", "Add an example to the README"),
    _message(41, "assistant", "Added a `vibe --resume` example under **Usage**."),
]

handshake = {
    **listed(
        session(
            1,
            "Current work",
            tokenUsage={
                "inputTokens": 95_300,
                "outputTokens": 781,
                "totalTokens": 96_081,
            },
        ),
        session(2, "Docs refresh", "/projects/website", isUnseen=True),
        session(3, "Website plan", "/projects/website"),
        session(4, "Failed run", "/projects/tools", status={"type": "failed"}),
    ),
    f"session/read#{_DOCS}": {
        "state": {"eventId": 1, "session": {"id": _DOCS}, "history": _HISTORY}
    },
}
request_methods = {"session/read"}
screen_contains = ("Added a vibe --resume example", "/projects/website")
screen_excludes = ("Refresh the docs for the new flag",)

# Down highlights the docs session; once the highlight rests its conversation is read.
timeline: Timeline = [
    "/sessions",
    "\r",
    "\x1b[B",
    advance(200),
    take_snapshot("preview"),
]
