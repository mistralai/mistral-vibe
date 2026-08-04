from __future__ import annotations

from vibe.acp.utils import build_permission_options
from vibe.permissions import PermissionScope, RequiredPermission


def test_build_permission_options_serializes_scopes_with_snake_case_keys() -> None:
    required = [
        RequiredPermission(
            scope=PermissionScope.COMMAND_PATTERN,
            invocation_pattern="pnpm add --save-dev vitest",
            session_pattern="pnpm *",
            label="pnpm *",
        )
    ]

    options = build_permission_options(required)

    session_options = [option for option in options if option.kind == "allow_always"]
    assert session_options
    expected_meta = [
        {
            "scope": "command_pattern",
            "invocation_pattern": "pnpm add --save-dev vitest",
            "session_pattern": "pnpm *",
            "label": "pnpm *",
        }
    ]
    for option in session_options:
        assert option.field_meta is not None
        assert option.field_meta["required_permissions"] == expected_meta
