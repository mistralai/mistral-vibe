"""`1` on the resume choice resumes the suspended goal; `2` would leave it paused."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._resumed import (
    client_args as client_args,
    handshake as handshake,
    on_request as on_request,
)

timeline: Timeline = ["1", take_snapshot()]

screen_contains = ("▶ goal 4m",)
screen_excludes = ("paused when the session closed",)
