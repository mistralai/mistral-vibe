from __future__ import annotations

import asyncio
from enum import StrEnum, auto
import fnmatch

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


def wildcard_match(text: str, pattern: str) -> bool:
    """If pattern ends with " *", trailing args are optional (match with or without)."""
    if fnmatch.fnmatch(text, pattern):
        return True
    if pattern.endswith(" *") and fnmatch.fnmatch(text, pattern[:-2]):
        return True
    return False


class PermissionStore:
    def __init__(self) -> None:
        self._rules: list[ApprovedRule] = []
        self._tool_permissions: dict[str, ToolPermission] = {}
        self.lock = asyncio.Lock()

    def add_rule(self, rule: ApprovedRule) -> None:
        self._rules.append(rule)

    def covers(self, tool_name: str, rp: RequiredPermission) -> bool:
        return any(
            rule.tool_name == tool_name
            and rule.scope == rp.scope
            and wildcard_match(rp.invocation_pattern, rule.session_pattern)
            for rule in self._rules
        )

    def set_tool_permission(self, tool_name: str, permission: ToolPermission) -> None:
        self._tool_permissions[tool_name] = permission

    def get_tool_permission(self, tool_name: str) -> ToolPermission | None:
        return self._tool_permissions.get(tool_name)
