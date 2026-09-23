from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum, auto

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic.alias_generators import to_camel

from vibe.permissions import PathGrantScope, PermissionScope, RequiredPermission


def path_scope_label(
    required_permissions: Sequence[RequiredPermission],
    scope: PathGrantScope,
    *,
    for_session: bool,
) -> str:
    """Describe every path target affected by a scoped approval."""
    target_count = sum(
        permission.scope is PermissionScope.OUTSIDE_DIRECTORY
        for permission in required_permissions
    )
    if scope is PathGrantScope.DIRECTORY_RECURSIVE:
        return "these folders" if target_count > 1 else "this folder"
    target = "these paths" if target_count > 1 else "this file"
    return f"{target} only" if for_session else target


class PresentationModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
    )


class ToolEffectKind(StrEnum):
    TOOL = auto()
    SHELL = auto()
    FILE_EDIT = auto()
    FILE_SEARCH = auto()
    FILE_READ = auto()
    TODO = auto()
    FILE_WRITE = auto()
    USER_QUESTION = auto()
    WEB_SEARCH = auto()
    WEB_FETCH = auto()
    SKILL = auto()
    SUBAGENT = auto()
    WORKTREE = auto()
    PROCESS = auto()


class EffectCallDisplay(PresentationModel):
    summary: str
    content: str | None = None
    suffix: str = ""
    verb: str = ""
    message: str | None = None
    settled_verb: str = ""
    settled_message: str | None = None
    status_text: str


class EffectResultDisplay(PresentationModel):
    success: bool
    verb: str = ""
    message: str
    warnings: list[str] = Field(default_factory=list)
    # Why the call ran without asking (smart approve). Not a warning: the
    # call succeeded and was authorised, so it must not read as a problem.
    approval_note: str | None = None
    suffix: str = ""

    @property
    def text(self) -> str:
        return f"{self.verb} {self.message}".strip()


class ToolCallPresentation(PresentationModel):
    kind: ToolEffectKind
    display: EffectCallDisplay


class ToolResultPresentation(PresentationModel):
    kind: ToolEffectKind
    display: EffectResultDisplay
    projected_output: JsonValue = None
