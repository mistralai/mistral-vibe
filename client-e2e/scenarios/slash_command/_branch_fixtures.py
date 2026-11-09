"""Shared `/branch` scenario fixtures: a detached `session/fork` answer."""

from __future__ import annotations

import json
from typing import Any

from e2e.app_server.config import FIXTURE_PATH

FORKED_SESSION_ID = "22222222-0000-4000-8000-000000000002"
REQUEST_METHODS = {"session/fork", "telemetry/record"}


def fork_handshake(harness: str | None) -> dict[str, Any]:
    """Fork the live session under a new id; `harness` tags source and fork, `None` omits it like legacy."""
    state = json.loads(FIXTURE_PATH.read_text())["handshake"]["session/start"]["state"]
    source_id = state["session"]["id"]
    state["session"]["id"] = FORKED_SESSION_ID
    state["session"]["parentSessionId"] = source_id
    handshake: dict[str, Any] = {
        "session/fork": {
            "sourceSessionId": source_id,
            "state": state,
            "lastEventId": state["eventId"],
        }
    }
    if harness is not None:
        state["session"]["harness"] = harness
        handshake["session/start"] = {"state": {"session": {"harness": harness}}}
    return handshake
