from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from vibe.core.telemetry.types import ProjectPickerTelemetryPayload
from vibe.core.vibe_code_project import (
    ProjectPickerContext,
    VibeCodeProjectPickerService,
    VibeCodeProjectPickerState,
)

if TYPE_CHECKING:
    from vibe.core.teleport.git import GitRepoInfo, GitRepository


@dataclass
class VibeCodeProjectPickerUiState:
    service: VibeCodeProjectPickerService | None = None
    picker_state: VibeCodeProjectPickerState | None = None
    context: ProjectPickerContext | None = None
    git_info: GitRepoInfo | None = None
    teleport_pending: bool = False
    teleport_prompt: str | None = None
    teleport_project_picker: ProjectPickerTelemetryPayload | None = None
    saved_project_link_cleared: bool = False
    project_repo_remote_changed: bool = False

    def clear_teleport(self) -> None:
        self.teleport_pending = False
        self.teleport_prompt = None
        self.teleport_project_picker = None

    def clear_link_flags(self) -> None:
        self.saved_project_link_cleared = False
        self.project_repo_remote_changed = False


def suggested_default_branch(git_info: GitRepoInfo | None) -> str:
    if git_info is None:
        return "main"
    return git_info.default_branch or git_info.branch or "main"


def make_git_repository() -> GitRepository:
    from vibe.core.teleport.git import GitRepository

    return GitRepository()
