"""A resumed session whose goal the closed session suspended."""

from __future__ import annotations

from e2e.app_server.events import goal, goal_updated

RESUMED_ID = "00000000-0000-4000-8000-000000000003"

client_args = ("--resume", RESUMED_ID)

handshake = {
    "session/resume": {
        "state": {"session": {"goal": goal("paused", status_reason="suspended")}}
    },
    "goal/set": {"goal": goal()},
}
on_request = {"goal/set": [goal_updated(goal(), RESUMED_ID)]}
