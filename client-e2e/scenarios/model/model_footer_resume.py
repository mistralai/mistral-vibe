"""Resuming a session pinned to another model renames the model in the footer."""

from __future__ import annotations

from e2e.app_server.events import runtime_updated
from e2e.app_server.scenario import Timeline
from scenarios.model._pinned import DEVSTRAL

_SAVED = "00000000-0000-4000-8000-000000000003"
_RESUMED = runtime_updated(session_id=_SAVED)
_RESUMED["params"]["runtime"]["config"]["activeModel"] = DEVSTRAL

client_args = ("--resume",)
request_methods = {"session/resume"}
# The server follows `session/resume` with the resumed session's runtime.
on_request = {"session/resume": [_RESUMED]}
timeline: Timeline = ["\r"]

screen_contains = ("Resumed session 00000000", "Devstral Small 2[off] · ")
screen_excludes = ("mistral-medium-3.5[medium] · ",)
