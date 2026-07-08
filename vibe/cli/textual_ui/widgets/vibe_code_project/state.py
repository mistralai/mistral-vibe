from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

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


def suggested_default_branch(git_info: GitRepoInfo | None) -> str:
    if git_info is None:
        return "main"
    return git_info.default_branch or git_info.branch or "main"


def make_git_repository() -> GitRepository:
    from vibe.core.teleport.git import GitRepository

    return GitRepository()
