from __future__ import annotations

import pytest

from vibe.core.tools.base import ToolPermission
from vibe.core.tools.permissions import (
    ApprovedRule,
    PermissionScope,
    PermissionStore,
    RequiredPermission,
)
from vibe.permissions import PathGrantScope, path_grant_pattern


class TestPermissionStore:
    def test_covers_returns_false_when_empty(self):
        store = PermissionStore()
        rp = RequiredPermission(
            scope=PermissionScope.COMMAND_PATTERN,
            invocation_pattern="npm install foo",
            session_pattern="npm install *",
            label="npm install *",
        )

        assert not store.covers("bash", rp)

    def test_covers_returns_true_after_matching_rule_added(self):
        store = PermissionStore()
        store.add_rule(
            ApprovedRule(
                tool_name="bash",
                scope=PermissionScope.COMMAND_PATTERN,
                session_pattern="npm install *",
            )
        )
        rp = RequiredPermission(
            scope=PermissionScope.COMMAND_PATTERN,
            invocation_pattern="npm install foo",
            session_pattern="npm install *",
            label="npm install *",
        )

        assert store.covers("bash", rp)

    def test_covers_isolates_by_tool_name(self):
        store = PermissionStore()
        store.add_rule(
            ApprovedRule(
                tool_name="bash",
                scope=PermissionScope.COMMAND_PATTERN,
                session_pattern="npm install *",
            )
        )
        rp = RequiredPermission(
            scope=PermissionScope.COMMAND_PATTERN,
            invocation_pattern="npm install foo",
            session_pattern="npm install *",
            label="npm install *",
        )

        assert not store.covers("read", rp)

    def test_tool_permission_round_trip(self):
        store = PermissionStore()
        assert store.get_tool_permission("bash") is None

        store.set_tool_permission("bash", ToolPermission.ALWAYS)

        assert store.get_tool_permission("bash") == ToolPermission.ALWAYS

    @pytest.mark.parametrize(
        ("scope", "covered", "not_covered"),
        [
            (
                PathGrantScope.EXACT,
                "/outside/shared/config[prod].json",
                "/outside/shared/other.json",
            ),
            (
                PathGrantScope.DIRECTORY_RECURSIVE,
                "/outside/shared/private/notes.txt",
                "/outside/sibling/notes.txt",
            ),
        ],
    )
    def test_outside_path_scopes_do_not_overgrant(
        self, scope: PathGrantScope, covered: str, not_covered: str
    ) -> None:
        store = PermissionStore()
        grant_path = (
            "/outside/shared/config[prod].json"
            if scope is PathGrantScope.EXACT
            else "/outside/shared"
        )
        store.add_rule(
            ApprovedRule(
                tool_name="read_file",
                scope=PermissionScope.OUTSIDE_DIRECTORY,
                session_pattern=path_grant_pattern(grant_path, scope),
            )
        )

        def permission(path: str) -> RequiredPermission:
            return RequiredPermission(
                scope=PermissionScope.OUTSIDE_DIRECTORY,
                invocation_pattern=path,
                session_pattern=path_grant_pattern(path, PathGrantScope.EXACT),
                label=path,
            )

        assert store.covers("read_file", permission(covered))
        assert not store.covers("read_file", permission(not_covered))
        assert not store.covers("write_file", permission(covered))

    def test_outside_path_scope_normalizes_traversal_and_windows_case(self) -> None:
        store = PermissionStore()
        store.add_rule(
            ApprovedRule(
                tool_name="read_file",
                scope=PermissionScope.OUTSIDE_DIRECTORY,
                session_pattern=path_grant_pattern(
                    r"C:\Users\Me\Shared", PathGrantScope.DIRECTORY_RECURSIVE
                ),
            )
        )
        permission = RequiredPermission(
            scope=PermissionScope.OUTSIDE_DIRECTORY,
            invocation_pattern=r"c:\users\me\shared\private\..\notes.txt",
            session_pattern="unused",
            label="unused",
        )

        assert store.covers("read_file", permission)

    def test_reset_drops_path_scope_rules(self) -> None:
        store = PermissionStore()
        permission = RequiredPermission(
            scope=PermissionScope.OUTSIDE_DIRECTORY,
            invocation_pattern="/outside/file.txt",
            session_pattern=path_grant_pattern(
                "/outside/file.txt", PathGrantScope.EXACT
            ),
            label="unused",
        )
        store.add_rule(
            ApprovedRule(
                tool_name="read_file",
                scope=permission.scope,
                session_pattern=permission.session_pattern,
            )
        )

        store.reset()

        assert not store.covers("read_file", permission)
