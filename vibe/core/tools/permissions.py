from __future__ import annotations

from enum import StrEnum, auto

from pydantic import BaseModel, Field

from vibe.core.tools.base import ToolPermission


class PermissionScope(StrEnum):
    COMMAND_PATTERN = auto()
    OUTSIDE_DIRECTORY = auto()
    FILE_PATTERN = auto()
    URL_PATTERN = auto()


class RequiredPermission(BaseModel):
    scope: PermissionScope
    invocation_pattern: str
    session_pattern: str
    label: str


class PermissionContext(BaseModel):
    permission: ToolPermission
    required_permissions: list[RequiredPermission] = Field(default_factory=list)
    reason: str | None = None


class ApprovedRule(BaseModel):
    tool_name: str
    scope: PermissionScope
    session_pattern: str
