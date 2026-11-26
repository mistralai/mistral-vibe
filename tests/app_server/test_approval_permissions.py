from __future__ import annotations

import pytest

from vibe.app_server._approval_permissions import (
    approval_grant_permissions,
    available_path_scopes,
)
from vibe.app_server.models import (
    ApprovalCallbackDetail,
    ApprovalDecision,
    ApprovalDecisionType,
    GenericEffectDetail,
)
from vibe.permissions import (
    PathGrantScope,
    PermissionScope,
    RequiredPermission,
    path_pattern_matches,
)
from vibe.utils.tool_presentation import EffectCallDisplay


def _outside_permission(
    path: str = "/outside/shared/config.json",
    *,
    path_scope_root: str | None = "/outside/shared",
) -> RequiredPermission:
    return RequiredPermission(
        scope=PermissionScope.OUTSIDE_DIRECTORY,
        invocation_pattern=path,
        session_pattern="legacy-broad-pattern",
        label=path,
        path_scope_root=path_scope_root,
    )


def _detail(*, choices: list[PathGrantScope]) -> ApprovalCallbackDetail:
    return ApprovalCallbackDetail(
        effect=GenericEffectDetail(
            tool_name="read_file",
            display=EffectCallDisplay(summary="read", status_text="reading"),
        ),
        required_permissions=[_outside_permission()],
        path_scope_choices=choices,
    )


def test_file_permissions_offer_only_exact_scope() -> None:
    assert available_path_scopes([_outside_permission()]) == [PathGrantScope.EXACT]


def test_directory_permissions_offer_only_recursive_scope() -> None:
    assert available_path_scopes([
        _outside_permission("/outside/shared", path_scope_root="/outside/shared")
    ]) == [PathGrantScope.DIRECTORY_RECURSIVE]


@pytest.mark.parametrize(
    ("scope", "covered", "not_covered"),
    [
        (
            PathGrantScope.EXACT,
            "/outside/shared/config.json",
            "/outside/shared/other.json",
        ),
        (
            PathGrantScope.DIRECTORY_RECURSIVE,
            "/outside/shared/private/notes.txt",
            "/outside/sibling/notes.txt",
        ),
    ],
)
def test_server_derives_selected_path_scope(
    scope: PathGrantScope, covered: str, not_covered: str
) -> None:
    detail = _detail(choices=list(PathGrantScope))
    decision = ApprovalDecision(
        type=ApprovalDecisionType.APPROVE_FOR_SESSION, path_scope=scope
    )

    [grant] = approval_grant_permissions(detail, decision)

    assert path_pattern_matches(covered, grant.session_pattern)
    assert not path_pattern_matches(not_covered, grant.session_pattern)


def test_old_client_without_a_scope_defaults_to_exact_file() -> None:
    detail = _detail(choices=list(PathGrantScope))
    decision = ApprovalDecision(type=ApprovalDecisionType.APPROVE_FOR_SESSION)

    [grant] = approval_grant_permissions(detail, decision)

    assert path_pattern_matches("/outside/shared/config.json", grant.session_pattern)
    assert not path_pattern_matches(
        "/outside/shared/private/notes.txt", grant.session_pattern
    )


def test_old_client_without_a_scope_defaults_to_exact_directory() -> None:
    detail = ApprovalCallbackDetail(
        effect=GenericEffectDetail(
            tool_name="bash",
            display=EffectCallDisplay(summary="read", status_text="reading"),
        ),
        required_permissions=[
            _outside_permission("/outside/shared", path_scope_root="/outside/shared")
        ],
        path_scope_choices=[PathGrantScope.DIRECTORY_RECURSIVE],
    )
    decision = ApprovalDecision(type=ApprovalDecisionType.APPROVE_FOR_SESSION)

    [grant] = approval_grant_permissions(detail, decision)

    assert path_pattern_matches("/outside/shared", grant.session_pattern)
    assert not path_pattern_matches(
        "/outside/shared/private/notes.txt", grant.session_pattern
    )


def test_directory_target_uses_itself_as_the_scope_root(tmp_path) -> None:
    directory = tmp_path / "selected"
    directory.mkdir()
    sibling = tmp_path / "sibling"
    detail = ApprovalCallbackDetail(
        effect=GenericEffectDetail(
            tool_name="bash",
            display=EffectCallDisplay(summary="read", status_text="reading"),
        ),
        required_permissions=[
            _outside_permission(str(directory), path_scope_root=str(directory))
        ],
        path_scope_choices=[PathGrantScope.DIRECTORY_RECURSIVE],
    )
    decision = ApprovalDecision(
        type=ApprovalDecisionType.APPROVE_FOR_SESSION,
        path_scope=PathGrantScope.DIRECTORY_RECURSIVE,
    )

    [grant] = approval_grant_permissions(detail, decision)

    assert path_pattern_matches(str(directory), grant.session_pattern)
    assert path_pattern_matches(str(directory / "child.txt"), grant.session_pattern)
    assert not path_pattern_matches(str(sibling / "child.txt"), grant.session_pattern)


def test_missing_shell_target_does_not_grant_its_parent(tmp_path) -> None:
    target = tmp_path / "outside" / "missing"
    sibling = target.parent / "sibling.txt"
    detail = ApprovalCallbackDetail(
        effect=GenericEffectDetail(
            tool_name="bash",
            display=EffectCallDisplay(summary="read", status_text="reading"),
        ),
        required_permissions=[_outside_permission(str(target), path_scope_root=None)],
        path_scope_choices=[PathGrantScope.EXACT],
    )
    decision = ApprovalDecision(type=ApprovalDecisionType.APPROVE_FOR_SESSION)

    [grant] = approval_grant_permissions(detail, decision)

    assert path_pattern_matches(str(target), grant.session_pattern)
    assert not path_pattern_matches(str(target / "child.txt"), grant.session_pattern)
    assert not path_pattern_matches(str(sibling), grant.session_pattern)


def test_broad_path_scope_does_not_broaden_a_sensitive_file_permission() -> None:
    sensitive = RequiredPermission(
        scope=PermissionScope.FILE_PATTERN,
        invocation_pattern="/outside/shared/.env",
        session_pattern="/outside/shared/\\.env",
        label="sensitive file",
    )
    detail = _detail(choices=list(PathGrantScope))
    detail.required_permissions.append(sensitive)
    decision = ApprovalDecision(
        type=ApprovalDecisionType.APPROVE_FOR_SESSION,
        path_scope=PathGrantScope.DIRECTORY_RECURSIVE,
    )

    grants = approval_grant_permissions(detail, decision)

    assert grants[1] == sensitive


def test_server_rejects_a_path_scope_that_was_not_offered() -> None:
    detail = _detail(choices=[PathGrantScope.EXACT])
    decision = ApprovalDecision(
        type=ApprovalDecisionType.APPROVE_FOR_SESSION,
        path_scope=PathGrantScope.DIRECTORY_RECURSIVE,
    )

    with pytest.raises(ValueError, match="was not offered"):
        approval_grant_permissions(detail, decision)
