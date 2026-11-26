"""A narrow waiting line reads `Waiting…`, then drops its hints and timer, before the subagents chip shrinks."""

from __future__ import annotations

from e2e.app_server.events import (
    child_session_updated,
    model_wait_started,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline, resize, take_snapshot

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
handshake = {"session/start": {"state": {"session": {"harness": "unified"}}}}

timeline: Timeline = [
    "start a subagent\r",
    turn_started(),
    user_msg("start a subagent"),
    child_session_updated(),
    model_wait_started(),
    {"release": 5},
    take_snapshot(),
    resize(40, 60),
    take_snapshot(),
    resize(40, 30),
    take_snapshot(),
]

screen_contains = ("◷ Waiting… (", "◐ 1 agent")
