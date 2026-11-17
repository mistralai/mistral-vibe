"""In vim Normal mode, `1` answers the resume choice instead of starting a count."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._fixtures import ESCAPE
from scenarios.goal._resumed import (
    client_args as client_args,
    handshake as _resumed,
    on_request as on_request,
)

handshake = {
    **_resumed,
    "runtime/read": {"runtime": {"config": {"vimKeybindings": True}}},
}

timeline: Timeline = [ESCAPE, "1", take_snapshot()]

screen_contains = ("12.5K / 50K · 4m",)
screen_excludes = ("paused when the session closed",)
