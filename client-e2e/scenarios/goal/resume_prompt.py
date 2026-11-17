"""Resuming a session whose goal was suspended offers, above the input, to resume it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.goal._resumed import (
    client_args as client_args,
    handshake as handshake,
    on_request as on_request,
)

timeline: Timeline = [take_snapshot()]

screen_contains = (
    "‖ Goal Make the parity suite pass · paused when the session closed · 1. Resume  2. Leave paused",
)
