from __future__ import annotations

from enum import StrEnum, auto

from pydantic import BaseModel, Field

from vibe.permissions import PermissionScope, RequiredPermission


class ToolPermissionError(Exception):
    pass


class ToolPermission(StrEnum):
    ALWAYS = auto()
    NEVER = auto()
    ASK = auto()

    @classmethod
    def by_name(cls, name: str) -> ToolPermission:
        try:
            return ToolPermission(name.upper())
        except ValueError:
            raise ToolPermissionError(
                f"Invalid tool permission: {name}. Must be one of {list(cls)}"
            )


class PermissionContext(BaseModel):
    permission: ToolPermission
    required_permissions: list[RequiredPermission] = Field(default_factory=list)
    reason: str | None = None


class ApprovedRule(BaseModel):
    tool_name: str
    scope: PermissionScope
    session_pattern: str
