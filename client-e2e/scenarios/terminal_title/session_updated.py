"""Session title patches preserve null titles and sanitize terminal controls."""

from __future__ import annotations

from e2e.app_server.events import (
    SESSION_ID,
    assistant_msg,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    {
        "method": "session/updated",
        "params": {
            "sessionId": SESSION_ID,
            "emittedAt": 1787593260000,
            "patch": [
                {
                    "op": "replace",
                    "path": "/title",
                    "value": "  Session\x1b]2;unsafe\x07\n café  ",
                }
            ],
        },
    },
    assistant_msg("Hello."),
    turn_completed(),
]

expected_titles = ("Vibe", "Session]2;unsafe café")
