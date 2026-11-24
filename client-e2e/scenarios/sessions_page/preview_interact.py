"""The preview shows the session's facts, opens at the end of its conversation, scrolls with Shift+↑↓, the wheel and its scrollbar, and folds its tool calls."""

from __future__ import annotations

from e2e.app_server.events import _bash_detail
from e2e.app_server.scenario import Timeline, advance, take_snapshot
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
    _message(31, "assistant", "Two pages: flags and usage."),
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

# Inside the preview at 140 columns; the folded call sits on screen row 35.
_WHEEL_DOWN = "\x1b[<65;100;25M"
# The wheel on the scrollbar's column, above the thumb, scrolls the preview too.
_WHEEL_UP_ON_BAR = "\x1b[<64;118;25M"
_CLICK_CALL = "\x1b[<0;100;35M\x1b[<0;100;35m"
# Press the scrollbar's thumb, scrolled up, and drag it down to the end of its track.
_DRAG_THUMB_DOWN = "\x1b[<0;118;27M\x1b[<32;118;40M\x1b[<0;118;40m"

timeline: Timeline = [
    "/sessions",
    "\r",
    "\x1b[B",
    advance(200),
    take_snapshot("end"),
    "\x1b[1;2A" * 6,
    take_snapshot("scrolled_up"),
    _DRAG_THUMB_DOWN,
    take_snapshot("dragged_to_end"),
    _WHEEL_UP_ON_BAR * 6,
    take_snapshot("wheel_on_bar"),
    _WHEEL_DOWN * 6,
    "\x0f",
    take_snapshot("tools_shown"),
    "\x0f",
    _CLICK_CALL,
]

# The click unfolds the call Ctrl+O folded back, at the end of the conversation.
screen_contains = ("Model      mistral-large · high", "⏷ Ran ls docs", "└ usage.md")
