from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict

from vibe.utils import AgentEntrypoint
from vibe.utils.terminal import TerminalEmulator


class AttachmentKind(StrEnum):
    IMAGE = "image"


class ClientMetadata(BaseModel):
    name: str
    version: str


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


class ExperimentAssignment(BaseModel):
    experiment_id: str
    experiment_name: str
    variation_name: str
    variation_id: int | None = None
    # GrowthBook result payload for the assignment. `in_experiment` confirms a
    # genuine exposure; `hash_attribute`/`hash_value` record the unit GrowthBook
    # actually bucketed on (used to verify the randomization unit).
    in_experiment: bool | None = None
    hash_attribute: str | None = None
    hash_value: str | None = None
    feature_id: str | None = None


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
    experiment_assignments: list[ExperimentAssignment] | None = None
    # DEPRECATED: prefer ``experiment_attributes`` (planName/planType). This is a
    # display label from the account-panel path, set asynchronously and often
    # absent on early events; it does not reliably reflect the GrowthBook
    # bucketing plan. Kept for backward compatibility with existing consumers.
    user_plan: str | None = None
    # The exact attribute snapshot sent to GrowthBook for bucketing (see
    # ExperimentAttributes), gathered as one self-describing object rather than
    # flattened. Emitted on the exposure event so warehouse analysis can segment
    # on the same dimensions GrowthBook assigns on (e.g. planName, planType) and
    # verify the randomization unit (userId). Keeps GrowthBook's own attribute
    # names. Distinct from ``user_plan``, a display label from the account panel.
    experiment_attributes: dict[str, Any] | None = None


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
