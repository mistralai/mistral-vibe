"""Narrower panes drop Version at 80 columns, then Source at 60, and never draw a row past the box."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize, take_snapshot
from e2e.app_server.skills import METHODS, installed, skill

handshake = {
    "skills/installed": installed([
        skill("summarize-pull-request-review-discussion", scope="project"),
        skill("deploy", source="registry", version=12, alias="stable"),
    ])
}
request_methods = METHODS

timeline: Timeline = [
    "/skills\r",
    resize(40, 80),
    take_snapshot(),
    resize(40, 60),
    take_snapshot(),
]

screen_contains = ("summarize-pull-request-review-discussion",)
