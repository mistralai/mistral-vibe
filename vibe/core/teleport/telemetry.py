from __future__ import annotations

from dataclasses import dataclass

from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import TeleportFailureStage
from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.types import (
    TeleportAuthRequiredEvent,
    TeleportCheckingGitEvent,
    TeleportCompleteEvent,
    TeleportFetchingUrlEvent,
    TeleportPushingEvent,
    TeleportPushRequiredEvent,
    TeleportStartingWorkflowEvent,
    TeleportWaitingForGitHubEvent,
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
        github_auth_required=False,
        nb_session_messages=nb_session_messages,
    )


@dataclass
class TeleportTelemetryTracker:
    telemetry_client: TelemetryClient
    nb_session_messages: int
    stage: TeleportFailureStage
    push_required: bool = False
    github_auth_required: bool = False
    success: bool = False
    error_class: str | None = None

    def record_event(self, event: TeleportYieldEvent) -> None:
        match event:
            case TeleportCheckingGitEvent():
                self.stage = "git_check"
            case TeleportPushRequiredEvent():
                self.push_required = True
                self.stage = "cancelled"
            case TeleportPushingEvent():
                self.stage = "push"
            case TeleportStartingWorkflowEvent():
                self.stage = "workflow_start"
            case TeleportWaitingForGitHubEvent():
                self.stage = "github_auth"
            case TeleportAuthRequiredEvent():
                self.github_auth_required = True
                self.stage = "github_auth"
            case TeleportFetchingUrlEvent():
                self.stage = "fetch_url"
            case TeleportCompleteEvent():
                self.success = True

    def record_service_error(self, error: ServiceTeleportError) -> None:
        self.error_class = type(error).__name__

    def record_cancelled(self) -> None:
        self.stage = "cancelled"
        self.error_class = "CancelledError"

    def record_unexpected_error(self, error: Exception) -> None:
        self.error_class = type(error).__name__

    def send_success(self) -> None:
        self.telemetry_client.send_teleport_completed(
            push_required=self.push_required,
            github_auth_required=self.github_auth_required,
            nb_session_messages=self.nb_session_messages,
        )

    def send_failure_if_needed(self) -> None:
        if self.success or self.error_class is None:
            return
        self.telemetry_client.send_teleport_failed(
            stage=self.stage,
            error_class=self.error_class,
            push_required=self.push_required,
            github_auth_required=self.github_auth_required,
            nb_session_messages=self.nb_session_messages,
        )
