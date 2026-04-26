from __future__ import annotations

from vibe.acp.utils import (
    TOOL_OPTIONS,
    ToolOption,
    build_permission_options,
    get_proxy_help_text,
)
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.core.proxy_setup import SUPPORTED_PROXY_VARS
from vibe.core.tools.permissions import PermissionScope, RequiredPermission


def _write_env_file(content: str) -> None:
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_ENV_FILE.path.write_text(content, encoding="utf-8")


class TestGetProxyHelpText:
    def test_returns_string(self) -> None:
        result = get_proxy_help_text()

        assert isinstance(result, str)

    def test_includes_proxy_configuration_header(self) -> None:
        result = get_proxy_help_text()

        assert "## Proxy Configuration" in result

    def test_includes_usage_section(self) -> None:
        result = get_proxy_help_text()

        assert "### Usage:" in result
        assert "/proxy-setup" in result

    def test_includes_all_supported_variables(self) -> None:
        result = get_proxy_help_text()

        for key in SUPPORTED_PROXY_VARS:
            assert f"`{key}`" in result

    def test_shows_none_configured_when_no_settings(self) -> None:
        result = get_proxy_help_text()

        assert "(none configured)" in result

    def test_shows_current_settings_when_configured(self) -> None:
        _write_env_file("HTTP_PROXY=http://proxy:8080\n")

        result = get_proxy_help_text()

        assert "HTTP_PROXY=http://proxy:8080" in result
        assert "(none configured)" not in result

    def test_shows_only_set_values(self) -> None:
        _write_env_file("HTTP_PROXY=http://proxy:8080\n")

        result = get_proxy_help_text()

        assert "HTTP_PROXY=http://proxy:8080" in result
        assert "HTTPS_PROXY=" not in result


class TestBuildPermissionOptions:
    def test_no_permissions_returns_default_options(self) -> None:
        result = build_permission_options(None)
        assert result is TOOL_OPTIONS

    def test_empty_list_returns_default_options(self) -> None:
        result = build_permission_options([])
        assert result is TOOL_OPTIONS

    def test_with_permissions_includes_labels_in_allow_always(self) -> None:
        permissions = [
            RequiredPermission(
                scope=PermissionScope.COMMAND_PATTERN,
                invocation_pattern="npm install foo",
                session_pattern="npm install *",
                label="npm install *",
            )
        ]
        result = build_permission_options(permissions)

        assert len(result) == 3
        allow_always = next(o for o in result if o.option_id == ToolOption.ALLOW_ALWAYS)
        assert "npm install *" in allow_always.name
        assert "session" in allow_always.name.lower()

    def test_allow_always_has_field_meta(self) -> None:
        permissions = [
            RequiredPermission(
                scope=PermissionScope.COMMAND_PATTERN,
                invocation_pattern="mkdir foo",
                session_pattern="mkdir *",
                label="mkdir *",
            )
        ]
        result = build_permission_options(permissions)

        allow_always = next(o for o in result if o.option_id == ToolOption.ALLOW_ALWAYS)
        assert allow_always.field_meta is not None
        assert "required_permissions" in allow_always.field_meta
        meta_perms = allow_always.field_meta["required_permissions"]
        assert len(meta_perms) == 1
        assert meta_perms[0]["session_pattern"] == "mkdir *"

    def test_allow_once_and_reject_unchanged(self) -> None:
        permissions = [
            RequiredPermission(
                scope=PermissionScope.URL_PATTERN,
                invocation_pattern="example.com",
                session_pattern="example.com",
                label="fetching from example.com",
            )
        ]
        result = build_permission_options(permissions)

        allow_once = next(o for o in result if o.option_id == ToolOption.ALLOW_ONCE)
        reject_once = next(o for o in result if o.option_id == ToolOption.REJECT_ONCE)
        assert allow_once.name == "Allow once"
        assert reject_once.name == "Reject once"
        assert allow_once.field_meta is None
        assert reject_once.field_meta is None
