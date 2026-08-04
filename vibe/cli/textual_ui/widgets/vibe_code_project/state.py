from __future__ import annotations

from dataclasses import dataclass

from vibe.app_server.models import (
    VibeCodeGitInfo,
    VibeCodePickerContext,
    VibeCodePickerState,
    VibeCodePickerView,
)


@dataclass
class VibeCodeProjectPickerUiState:
    view: VibeCodePickerView | None = None
    teleport_pending: bool = False
    teleport_prompt: str | None = None

    @property
    def picker_state(self) -> VibeCodePickerState | None:
        return self.view.state if self.view is not None else None

    @property
    def context(self) -> VibeCodePickerContext | None:
        return self.view.context if self.view is not None else None

    @property
    def git_info(self) -> VibeCodeGitInfo | None:
        return self.view.git if self.view is not None else None

    def clear_teleport(self) -> None:
        self.teleport_pending = False
        self.teleport_prompt = None


def suggested_default_branch(git_info: VibeCodeGitInfo | None) -> str:
    if git_info is None:
        return "main"
    return git_info.default_branch or git_info.branch or "main"
