"""A paused goal's detail panel offers `p resume`; `p` resumes it."""

from __future__ import annotations

from e2e.app_server.events import goal, goal_updated
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._fixtures import started_with

handshake = {
    **started_with(goal("paused", status_reason="user", token_budget=None)),
    "goal/set": {"goal": goal()},
}
on_request = {"goal/set": [goal_updated(goal())]}

timeline: Timeline = ["/goal\r", "p", take_snapshot()]
