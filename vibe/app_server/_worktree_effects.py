from __future__ import annotations

from dataclasses import dataclass

from vibe.app_server.models import (
    CompletedEffectState,
    EffectCallDisplay,
    EffectResultDisplay,
    FailedEffectState,
    PublicError,
    RunningEffectState,
    WorktreeEffectDetail,
    WorktreeEffectInput,
)
from vibe.core.git.worktree import PreparedWorktree
from vibe.core.types import WorktreeContext


@dataclass(frozen=True)
class WorktreeEffect:
    """The settled transcript entry for a session-start worktree."""

    name: str
    branch: str
    path: str
    was_created: bool

    @classmethod
    def created(cls, worktree: PreparedWorktree) -> WorktreeEffect:
        return cls(
            name=worktree.name,
            branch=worktree.branch,
            path=str(worktree.root),
            was_created=True,
        )

    @classmethod
    def resolved(cls, worktree: PreparedWorktree) -> WorktreeEffect:
        return cls(
            name=worktree.name,
            branch=worktree.branch,
            path=str(worktree.root),
            was_created=worktree.created,
        )

    # Rebuilt from the session log rather than from the worktree, which by now
    # may be gone: the entry records what happened, not what still exists.
    @classmethod
    def restored(cls, context: WorktreeContext) -> WorktreeEffect:
        return cls(
            name=context.name,
            branch=context.branch,
            path=context.path,
            was_created=True,
        )

    @property
    def detail(self) -> WorktreeEffectDetail:
        present_verb = "Creating" if self.was_created else "Reusing"
        settled_verb = "Created" if self.was_created else "Reused"
        return WorktreeEffectDetail(
            tool_name="worktree",
            input=WorktreeEffectInput(
                name=self.name, branch=self.branch, path=self.path
            ),
            display=EffectCallDisplay(
                summary=f"worktree: {self.name}",
                verb=present_verb,
                message=self.name,
                settled_verb=settled_verb,
                settled_message=self._settled,
                status_text=f"{present_verb} worktree",
            ),
        )

    # Only ever a completed resolution: the worktree exists before this renders,
    # and a failure never reaches here because session/start raises before the
    # session exists, leaving no transcript to report into.
    #
    # No output either - the branch and path are already in detail.input, and
    # the runner renders a completed effect's output as raw JSON without one.
    @property
    def state(self) -> CompletedEffectState:
        return CompletedEffectState(
            display=EffectResultDisplay(
                success=True,
                verb="Created" if self.was_created else "Reused",
                message=self._settled,
            )
        )

    @property
    def _settled(self) -> str:
        return f"{self.name} on {self.branch}"


@dataclass(frozen=True)
class WorktreeProgress:
    """The visible step while a requested worktree is being prepared."""

    name: str | None = None

    @property
    def detail(self) -> WorktreeEffectDetail:
        message = self.name or "workspace"
        return WorktreeEffectDetail(
            tool_name="worktree",
            display=EffectCallDisplay(
                summary=f"worktree: {message}",
                verb="Creating",
                message=message,
                settled_verb="Created",
                settled_message=message,
                status_text="Creating worktree",
            ),
        )

    @property
    def state(self) -> RunningEffectState:
        return RunningEffectState()

    def failed_state(self, error: BaseException) -> FailedEffectState:
        message = str(error) or "Worktree creation failed"
        return FailedEffectState(
            error=PublicError(code=type(error).__name__, message=message),
            display=EffectResultDisplay(
                success=False, verb="Failed", message="Worktree creation failed"
            ),
        )
