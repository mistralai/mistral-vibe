from __future__ import annotations

from collections.abc import Sequence

from vibe.app_server.models import ApprovalCallbackDetail, ApprovalDecision
from vibe.permissions import (
    PathGrantScope,
    PermissionScope,
    RequiredPermission,
    path_grant_pattern,
    path_pattern_matches,
    scope_required_permissions,
)


def available_path_scopes(
    required_permissions: Sequence[RequiredPermission],
) -> list[PathGrantScope]:
    outside_permissions = [
        permission
        for permission in required_permissions
        if permission.scope is PermissionScope.OUTSIDE_DIRECTORY
    ]
    if not outside_permissions:
        return []
    if all(
        permission.path_scope_root is not None
        and path_pattern_matches(
            permission.path_scope_root,
            path_grant_pattern(permission.invocation_pattern, PathGrantScope.EXACT),
        )
        for permission in outside_permissions
    ):
        return [PathGrantScope.DIRECTORY_RECURSIVE]
    return [PathGrantScope.EXACT]


def approval_grant_permissions(
    detail: ApprovalCallbackDetail, decision: ApprovalDecision
) -> list[RequiredPermission]:
    """Validate the client's choice and derive the grant on the server."""
    offered = detail.path_scope_choices
    if not offered:
        if decision.path_scope is not None:
            raise ValueError("A path scope was selected for an approval without paths")
        return list(detail.required_permissions)

    if decision.path_scope is None:
        return scope_required_permissions(
            detail.required_permissions, PathGrantScope.EXACT
        )

    selected = decision.path_scope
    if selected not in offered:
        raise ValueError(f"Path scope {selected.value!r} was not offered")
    return scope_required_permissions(detail.required_permissions, selected)
