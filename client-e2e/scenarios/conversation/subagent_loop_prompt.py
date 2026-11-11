"""A subagent's fired loop prompt renders like the main agent's: folded title, unfolded by Ctrl+O."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    child_entry,
    child_history_state,
    child_session_updated,
    subagent,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "start a watcher"
_TASK = "schedule a ping every minute"
_LOOP_PROMPT = "Watcher ping\nReply with the word PONG and the current time."
_CHILD_SESSION_ID = "child-session"

env = {"TZ": "UTC"}

handshake = {
    f"session/read#{_CHILD_SESSION_ID}": child_history_state(
        _CHILD_SESSION_ID,
        [
            child_entry(
                {
                    "id": "child-loop-prompt",
                    "type": "message",
                    "role": "user",
                    "source": "turn_start",
                    "content": [{"type": "text", "text": _LOOP_PROMPT}],
                    "userDisplayContent": {
                        "version": "1",
                        "host": "vibe",
                        "content": [
                            {
                                "type": "vibe.scheduled_loop",
                                "loopId": "1a2b3c4d",
                                "firedAt": 1_787_593_260_000,
                            }
                        ],
                    },
                    "generationStatus": "completed",
                    "createdAt": 1_787_593_260_000,
                    "updatedAt": 1_787_593_260_000,
                },
                _CHILD_SESSION_ID,
            ),
            child_entry(
                assistant_msg("PONG at 17:41 UTC")["params"]["entry"], _CHILD_SESSION_ID
            ),
        ],
    )
}

request_methods = frozenset({"session/read"})

screen_contains = ("⏷ Watcher ping", "└ loop 1a2b3c4d - 2026-08-24 17:41:00 UTC")

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent(_TASK),
    child_session_updated(),
    turn_completed(),
    {"release_after": 6},
    "\x1b[B",  # Down — focus the list on the Main row
    "\x1b[B",  # Down — highlight the child row
    "\r",  # Enter — open the read-only child view with its history
    "\x0f",  # Ctrl+O — unfold the loop prompt
]
