"""`--resume <id>` on a session still waiting for background work shows the waiting loader."""

from __future__ import annotations

from e2e.app_server.events import model_wait_started, user_msg
from e2e.app_server.scenario import AppServerEvent, Timeline, take_snapshot

_SAVED = "00000000-0000-4000-8000-000000000003"
_TURN = "saved-turn"


def _history_entry(event: AppServerEvent) -> dict[str, object]:
    """Repoint a builder's entry at the resumed session's still running turn."""
    entry = event["params"]["entry"]
    entry["sessionId"] = _SAVED
    entry["turnId"] = _TURN
    return entry


_HISTORY = [
    _history_entry(user_msg("start two subagents", entry_id="saved-user")),
    _history_entry(model_wait_started()),
]

handshake = {
    "session/resume": {
        "state": {
            "session": {"status": {"type": "running", "activeTurnId": _TURN}},
            "history": _HISTORY,
        }
    }
}
client_args = ("--resume", _SAVED)
timeline: Timeline = [take_snapshot()]

screen_contains = (
    "◷ Waiting for background work…",
    "Esc/Ctrl+C to interrupt",
    "send a message to steer",
)
