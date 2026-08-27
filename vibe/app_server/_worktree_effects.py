from __future__ import annotations

from dataclasses import dataclass

from vibe.app_server.models import (
    CompletedEffectState,
    EffectCallDisplay,
    EffectDetail,
    EffectResultDisplay,
    EffectState,
    WorktreeEffectDetail,
    WorktreeEffectInput,
)
from vibe.core.git.worktree import PreparedWorktree
from vibe.core.types import WorktreeContext


@dataclass(frozen=True)
class WorktreeEffect:
    """The transcript entry for a worktree the session start created."""

    name: str
    branch: str
    path: str

    @classmethod
    def created(cls, worktree: PreparedWorktree) -> WorktreeEffect:
        return cls(name=worktree.name, branch=worktree.branch, path=str(worktree.root))

    # Rebuilt from the session log rather than from the worktree, which by now
    # may be gone: the entry records what happened, not what still exists.
    @classmethod
    def restored(cls, context: WorktreeContext) -> WorktreeEffect:
        return cls(name=context.name, branch=context.branch, path=context.path)

    @property
    def detail(self) -> EffectDetail:
        return WorktreeEffectDetail(
            tool_name="worktree",
            input=WorktreeEffectInput(
                name=self.name, branch=self.branch, path=self.path
            ),
            display=EffectCallDisplay(
                summary=f"worktree: {self.name}",
                verb="Creating",
                message=self.name,
                settled_verb="Created",
                settled_message=self._settled,
                status_text="Creating worktree",
            ),
        )

    # Only ever a completed creation: the worktree exists before this renders,
    # and a failure never reaches here because session/start raises before the
    # session exists, leaving no transcript to report into.
    #
    # No output either - the branch and path are already in detail.input, and
    # the runner renders a completed effect's output as raw JSON without one.
    @property
    def state(self) -> EffectState:
        return CompletedEffectState(
            display=EffectResultDisplay(
                success=True, verb="Created", message=self._settled
            )
        )

    @property
    def _settled(self) -> str:
        return f"{self.name} on {self.branch}"
