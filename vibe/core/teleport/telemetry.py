from __future__ import annotations

from dataclasses import dataclass

from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import (
    ProjectPickerTelemetryPayload,
    TeleportContextSummaryStatus,
    TeleportFailureDetails,
    TeleportFailureStage,
)
from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.types import (
    TeleportCheckingGitEvent,
    TeleportCompleteEvent,
    TeleportPushingEvent,
    TeleportPushRequiredEvent,
    TeleportStartingWorkflowEvent,
    TeleportSummarizingContextEvent,
    TeleportYieldEvent,
)


def send_teleport_early_failure_telemetry(
    telemetry_client: TelemetryClient,
    *,
    stage: TeleportFailureStage,
    error_class: str,
    nb_session_messages: int,
) -> None:
    telemetry_client.send_teleport_failed(
        stage=stage,
        error_class=error_class,
        push_required=False,
        nb_session_messages=nb_session_messages,
        context_summary="skipped",
    )


@dataclass
class TeleportTelemetryTracker:
    telemetry_client: TelemetryClient
    nb_session_messages: int
    stage: TeleportFailureStage
    project_picker: ProjectPickerTelemetryPayload | None = None
    push_required: bool = False
    success: bool = False
    error_class: str | None = None
    error_details: TeleportFailureDetails | None = None
    context_summary: TeleportContextSummaryStatus = "skipped"
    context_summary_chars: int | None = None

    def record_event(self, event: TeleportYieldEvent) -> None:
        match event:
            case TeleportSummarizingContextEvent():
                self.stage = "context_summary"
            case TeleportCheckingGitEvent():
                self.stage = "git_check"
            case TeleportPushRequiredEvent():
                self.push_required = True
                self.stage = "cancelled"
            case TeleportPushingEvent():
                self.stage = "push"
            case TeleportStartingWorkflowEvent():
                self.stage = "workflow_start"
            case TeleportCompleteEvent():
                self.success = True

    def record_service_error(self, error: ServiceTeleportError) -> None:
        self.error_class = type(error).__name__
        self.error_details = error.telemetry_details
        if (
            self.project_picker is not None
            and self.project_picker.get("project_selection_source") == "saved_link"
            and error.telemetry_details.get("http_status_code") in {403, 404}
        ):
            self.project_picker["saved_project_link_cleared"] = True

    def record_context_summary_generated(self, summary: str) -> None:
        self.context_summary = "generated"
        self.context_summary_chars = len(summary)

    def record_context_summary_failed(self) -> None:
        self.context_summary = "failed"
        self.context_summary_chars = None
        self.stage = "context_summary"

    def record_cancelled(self) -> None:
        self.stage = "cancelled"
        self.error_class = "CancelledError"

    def record_unexpected_error(self, error: Exception) -> None:
        self.error_class = type(error).__name__

    def send_success(self) -> None:
        self.telemetry_client.send_teleport_completed(
            push_required=self.push_required,
            nb_session_messages=self.nb_session_messages,
            context_summary=self.context_summary,
            context_summary_chars=self.context_summary_chars,
            project_picker=self.project_picker,
        )

    def send_failure_if_needed(self) -> None:
        if self.success or self.error_class is None:
            return
        self.telemetry_client.send_teleport_failed(
            stage=self.stage,
            error_class=self.error_class,
            push_required=self.push_required,
            nb_session_messages=self.nb_session_messages,
            context_summary=self.context_summary,
            context_summary_chars=self.context_summary_chars,
            error_details=self.error_details,
            project_picker=self.project_picker,
        )
