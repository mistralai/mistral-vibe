"""A click on a link in the preview opens it, as in the conversation."""

from __future__ import annotations

from e2e.app_server.events import _bash_detail
from e2e.app_server.scenario import Action, Timeline, advance
from scenarios.sessions_page._sessions import listed, session

_DOCS = "00000000-0000-4000-8000-000000000002"


def _message(index: int, role: str, text: str) -> dict[str, object]:
    return {
        "id": f"docs-{index}",
        "sessionId": _DOCS,
        "turnId": "turn-1",
        "type": "message",
        "role": role,
        "generationStatus": "completed",
        "content": [{"type": "text", "text": text}],
    }


_BASH = {
    "id": "docs-bash",
    "sessionId": _DOCS,
    "turnId": "turn-1",
    "type": "effect",
    "generationStatus": "completed",
    "title": "bash",
    "detail": _bash_detail("ls docs"),
    "state": {
        "status": "completed",
        "outputText": "flags.md\nusage.md",
        "output": {
            "stdout": "flags.md\nusage.md",
            "stderr": "",
            "output": "flags.md\nusage.md",
            "truncated": False,
        },
        "display": {
            "success": True,
            "verb": "Ran",
            "message": "ls docs",
            "suffix": "",
            "warnings": [],
        },
    },
}

_HISTORY = [
    _message(0, "user", "Refresh the docs for the new flag"),
    *(
        _message(index, "assistant", f"Draft section {index:02d}")
        for index in range(1, 30)
    ),
    _message(30, "user", "List the docs folder"),
    _BASH,
    _message(
        31, "assistant", "Two pages, see [the docs](https://docs.mistral.ai/vibe)."
    ),
]

handshake = {
    **listed(
        session(1, "Current work"),
        session(
            2,
            "Docs refresh",
            "/projects/website",
            model="mistral-large",
            reasoningEffort="high",
            tokenUsage={
                "inputTokens": 95_300,
                "outputTokens": 781,
                "totalTokens": 96_081,
            },
        ),
    ),
    f"session/read#{_DOCS}": {
        "state": {"eventId": 1, "session": {"id": _DOCS}, "history": _HISTORY}
    },
}
request_methods = {"session/read"}

# On "the docs" in the reply, on screen row 36 (1-based SGR 37).
_CLICK_LINK = "\x1b[<0;98;37M\x1b[<0;98;37m"

timeline: Timeline = ["/sessions", "\r", "\x1b[B", advance(200), _CLICK_LINK]
expected_actions = [Action("open_url", "https://docs.mistral.ai/vibe")]
