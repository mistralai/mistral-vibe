from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import BaseModel


class ClientMetadata(BaseModel):
    name: str
    version: str


AgentEntrypoint = Literal["cli", "acp", "programmatic", "unknown"]


class EntrypointMetadata(BaseModel):
    agent_entrypoint: AgentEntrypoint
    agent_version: str
    client_name: str
    client_version: str


TelemetryCallType = Literal["main_call", "secondary_call"]


class TelemetryBaseMetadata(BaseModel):
    agent_entrypoint: AgentEntrypoint | None = None
    agent_version: str | None = None
    client_name: str | None = None
    client_version: str | None = None
    session_id: str | None = None
    parent_session_id: str | None = None


class TelemetryRequestMetadata(TelemetryBaseMetadata):
    call_type: TelemetryCallType
    call_source: str = "vibe_code"
    message_id: str | None = None


TeleportFailureStage = Literal[
    "no_history",
    "remote_session",
    "git_check",
    "push",
    "workflow_start",
    "github_auth",
    "fetch_url",
    "cancelled",
]


class TeleportCompletedPayload(TypedDict):
    push_required: bool
    github_auth_required: bool
    nb_session_messages: int


class TeleportFailedPayload(TeleportCompletedPayload):
    stage: TeleportFailureStage
    error_class: str
