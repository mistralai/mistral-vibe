"""`/goal clear` confirms before clearing an unfinished goal; the row goes with it."""

from __future__ import annotations

from e2e.app_server.events import goal, goal_updated
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._fixtures import started_with

handshake = {**started_with(goal()), "goal/clear": {"cleared": True}}
on_request = {"goal/clear": [goal_updated(None)]}

timeline: Timeline = ["/goal clear\r", "\r", take_snapshot()]

screen_excludes = ("Goal ",)
