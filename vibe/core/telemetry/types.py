from __future__ import annotations

from enum import StrEnum
from typing import Literal, TypedDict

from pydantic import BaseModel


class AttachmentKind(StrEnum):
    IMAGE = "image"


class ClientMetadata(BaseModel):
    name: str
    version: str


class TerminalEmulator(StrEnum):
    VSCODE = "vscode"
    VSCODE_INSIDERS = "vscode_insiders"
    CURSOR = "cursor"
    JETBRAINS = "jetbrains"
    ITERM2 = "iterm2"
    WEZTERM = "wezterm"
    GHOSTTY = "ghostty"
    ALACRITTY = "alacritty"
    KITTY = "kitty"
    HYPER = "hyper"
    WINDOWS_TERMINAL = "windows_terminal"
    UNKNOWN = "unknown"


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
    experiments: dict[str, str] | None = None


class TelemetryRequestMetadata(TelemetryBaseMetadata):
    call_type: TelemetryCallType
    call_source: str = "vibe_code"
    message_id: str | None = None


TeleportFailureStage = Literal[
    "no_history", "git_check", "push", "workflow_start", "cancelled"
]


class TeleportFailureDetails(TypedDict, total=False):
    failure_kind: str
    http_status_code: int


class TeleportCompletedPayload(TypedDict):
    push_required: bool
    nb_session_messages: int


class TeleportFailedPayload(TeleportCompletedPayload, TeleportFailureDetails):
    stage: TeleportFailureStage
    error_class: str
