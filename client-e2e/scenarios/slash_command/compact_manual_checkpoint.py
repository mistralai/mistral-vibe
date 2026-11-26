"""A manual `/compact` keeps its own status row; the server's manual checkpoint adds none."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    compaction_settled,
    compaction_started,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline
from scenarios.slash_command.compact import handshake

__all__ = ["handshake"]

screen_contains = ("Compaction completed.",)
screen_excludes = ("Compacting context", "Context compacted")
timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Hi there."),
    turn_completed(),
    "/compact\r",
    compaction_started("manual"),
    compaction_settled("manual"),
]
