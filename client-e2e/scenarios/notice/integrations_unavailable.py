"""A connector that failed to load mid-turn is named in a toast; the turn still completes."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    server_warning,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_MESSAGE = (
    "Couldn't load connectors Buildkite: Failed to load connectors (HTTP 503). "
    "Their tools are unavailable in this session."
)

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    server_warning(_MESSAGE, code="integrations_unavailable"),
    assistant_msg("Hello."),
    turn_completed(),
]

screen_contains = ("Couldn't load connectors Buildkite", "Hello.")
