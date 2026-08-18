from __future__ import annotations

import asyncio
import fnmatch

from vibe.core.tools.models import (
    ApprovedRule,
    PermissionContext as PermissionContext,
    PermissionScope as PermissionScope,
    RequiredPermission,
    ToolPermission,
)


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

    def reset(self) -> None:
        """Drop all session-scoped approvals so they never leak across sessions."""
        self._rules.clear()
        self._tool_permissions.clear()

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
