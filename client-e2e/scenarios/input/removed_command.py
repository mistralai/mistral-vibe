"""A removed slash command shows a notice, stays in the composer and never reaches the model."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

request_methods = {
    "workspace/prompt/prepare",
    "session/turn/enqueue",
    "telemetry/record",
}
screen_contains = ("/teleport was removed in Vibe 3.1.1.",)

timeline: Timeline = ["/teleport\r", take_snapshot()]
