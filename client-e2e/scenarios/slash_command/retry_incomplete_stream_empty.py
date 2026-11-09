"""A stream that ends before any text silently retries and shows only the recovered answer."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    turn_completed,
    turn_failed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

on_request = {
    "turn/start": [turn_started(), assistant_msg("Recovered"), turn_completed()]
}

screen_contains = ("Recovered",)
screen_excludes = ("Stream ended early", "/retry")

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    turn_failed(code="incomplete_stream", message="Stream ended early"),
]
