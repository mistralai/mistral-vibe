"""An app server without goal methods answers `method_not_found`: goals are unavailable."""

from __future__ import annotations

from e2e.app_server.events import goal
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._fixtures import started_with

handshake = started_with(goal())

timeline: Timeline = ["/goal pause\r", take_snapshot()]

screen_contains = ("This app server does not support goals.",)
