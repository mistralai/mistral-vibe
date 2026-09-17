"""A user-invocable skill remains a prompt and records skill telemetry."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

handshake = {
    "runtime/read": {
        "runtime": {
            "skills": [
                {
                    "name": "review-pr",
                    "description": "Review the current PR.",
                    "prompt": "",
                    "userInvocable": True,
                    "source": "local",
                }
            ]
        }
    }
}

_typed = "/Review-PR concise"
_submitted = "/review-pr concise"

timeline: Timeline = [
    f"{_typed}\r",
    turn_started(),
    user_msg(_submitted),
    assistant_msg("Review started."),
    turn_completed(),
]
