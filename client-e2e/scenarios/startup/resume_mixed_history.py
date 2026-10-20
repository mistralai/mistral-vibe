"""`--resume <id>` renders the saved prompt, the assistant reply, its tool call and the tool output."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, read_file, user_msg
from e2e.app_server.scenario import AppServerEvent, Timeline

_SAVED = "00000000-0000-4000-8000-000000000003"
_CTRL_O = "\x0f"


def _history_entry(event: AppServerEvent) -> dict[str, object]:
    """Repoint a builder's entry at the resumed session's static history."""
    entry = event["params"]["entry"]
    entry["sessionId"] = _SAVED
    entry["turnId"] = None
    return entry


_HISTORY = [
    _history_entry(user_msg("Hello, how are you?", entry_id="saved-user")),
    _history_entry(assistant_msg("I'm doing well, thank you!", entry_id="saved-reply")),
    _history_entry(
        read_file("test.txt", "   1→File content here", 1, entry_id="saved-read")
    ),
]

handshake = {"session/resume": {"state": {"history": _HISTORY}}}
client_args = ("--resume", _SAVED)

# Ctrl+O unfolds the resumed tool call so its output is on screen.
timeline: Timeline = [_CTRL_O]

screen_contains = (
    "Hello, how are you?",
    "I'm doing well, thank you!",
    "Read 1 line from test.txt",
    "File content here",
    "Resumed session 00000000",
)
