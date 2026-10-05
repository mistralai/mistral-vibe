"""Deterministic `/teleport` responses and events shared by terminal scenarios."""

from __future__ import annotations

from typing import Any

from e2e.app_server.remote_project import (
    METHODS as PROJECT_METHODS,
    handshake as project_handshake,
)

URL = "https://chat.mistral.ai/code/vibe-team/sessions/teleported"
START = "vibeCode/teleport/start"
PUSH_RESPOND = "vibeCode/teleport/push/respond"
CANCEL = "vibeCode/teleport/cancel"
RECOVER = "vibeCode/projects/recover"
METHODS = PROJECT_METHODS | {START, PUSH_RESPOND, CANCEL, RECOVER}


def event(kind: str, **fields: Any) -> dict[str, Any]:
    """A `vibeCode/teleport/event` bound to the request's operation id."""
    payload = {"kind": kind, "operationId": "$operationId", **fields}
    return {"method": "vibeCode/teleport/event", "params": {"event": payload}}


def complete() -> list[dict[str, Any]]:
    """The tail of a teleport that reaches Vibe Code Web."""
    return [event("starting_workflow"), event("complete", url=URL)]


def failed(message: str, code: str = "teleport_failed") -> dict[str, Any]:
    return event("failed", error={"message": message, "code": code})


def handshake(*, resolved: str | None = "linked", **kwargs: Any) -> dict[str, Any]:
    """Remote-project answers opened for teleport, plus the teleport RPCs."""
    responses = project_handshake(**kwargs)
    responses["vibeCode/projects/open"]["resolvedProjectId"] = resolved
    responses[START] = {"operationId": "$operationId"}
    responses[PUSH_RESPOND] = {}
    responses[CANCEL] = {"cancelled": True}
    return responses
