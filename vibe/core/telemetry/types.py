from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict


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
    APPLE_TERMINAL = "apple_terminal"
    ITERM2 = "iterm2"
    WEZTERM = "wezterm"
    GHOSTTY = "ghostty"
    ALACRITTY = "alacritty"
    KITTY = "kitty"
    HYPER = "hyper"
    WINDOWS_TERMINAL = "windows_terminal"
    UNKNOWN = "unknown"


AgentEntrypoint = Literal["cli", "acp", "programmatic", "unknown"]


class LaunchContext(BaseModel):
    agent_entrypoint: AgentEntrypoint
    agent_version: str
    client_name: str
    client_version: str
    terminal_emulator: TerminalEmulator | None = None

    def telemetry_fields(self) -> dict[str, Any]:
        return {
            "agent_entrypoint": self.agent_entrypoint,
            "agent_version": self.agent_version,
            "client_name": self.client_name,
            "client_version": self.client_version,
            "terminal_emulator": (
                self.terminal_emulator.value
                if self.terminal_emulator is not None
                else None
            ),
        }

    def sentry_tags(self) -> dict[str, str]:
        return {"entrypoint": self.agent_entrypoint, "client_name": self.client_name}


TelemetryCallType = Literal["main_call", "secondary_call"]


class TelemetryBaseMetadata(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    agent_entrypoint: AgentEntrypoint | None = None
    agent_version: str | None = None
    client_name: str | None = None
    client_version: str | None = None
    os: str | None = None
    os_version: str | None = None
    version: str | None = None
    terminal_emulator: TerminalEmulator | None = None
    session_id: str | None = None
    parent_session_id: str | None = None
    experiments: dict[str, str] | None = None
    user_plan: str | None = None


class TelemetryRequestMetadata(TelemetryBaseMetadata):
    call_type: TelemetryCallType
    call_source: str = "vibe_code"
    message_id: str | None = None


TeleportFailureStage = Literal[
    "no_history",
    "ineligible",
    "context_summary",
    "git_check",
    "push",
    "workflow_start",
    "cancelled",
]
TeleportContextSummaryStatus = Literal["skipped", "generated", "failed"]
ProjectSelectionSource = Literal[
    "saved_link", "selected_existing", "matched_project", "created_project", "cancelled"
]
RemoteProjectOutcome = Literal["configured", "created", "unlinked", "cancelled"]


class TeleportFailureDetails(TypedDict, total=False):
    failure_kind: str
    http_status_code: int


class ProjectPickerTelemetryPayload(TypedDict, total=False):
    project_picker_shown: bool
    project_selection_source: ProjectSelectionSource
    project_candidate_count_loaded: int
    project_multi_repo_match_count: int
    saved_project_link_cleared: bool
    project_repo_remote_changed: bool


class TeleportCompletedPayload(ProjectPickerTelemetryPayload):
    push_required: bool
    nb_session_messages: int
    context_summary: TeleportContextSummaryStatus
    context_summary_chars: int | None


class TeleportFailedPayload(TeleportCompletedPayload, TeleportFailureDetails):
    stage: TeleportFailureStage
    error_class: str


class RemoteProjectConfiguredPayload(ProjectPickerTelemetryPayload):
    outcome: RemoteProjectOutcome
