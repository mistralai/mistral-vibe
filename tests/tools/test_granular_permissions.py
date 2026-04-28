from __future__ import annotations

import os

import pytest

from vibe.core.tools.base import BaseToolState, ToolPermission
from vibe.core.tools.builtins.bash import (
    Bash,
    BashArgs,
    BashToolConfig,
    _collect_outside_dirs,
)
from vibe.core.tools.builtins.grep import Grep, GrepArgs, GrepToolConfig
from vibe.core.tools.builtins.read_file import (
    ReadFile,
    ReadFileArgs,
    ReadFileState,
    ReadFileToolConfig,
)
from vibe.core.tools.builtins.search_replace import (
    SearchReplace,
    SearchReplaceArgs,
    SearchReplaceConfig,
)
from vibe.core.tools.builtins.webfetch import WebFetch, WebFetchArgs, WebFetchConfig
from vibe.core.tools.builtins.write_file import (
    WriteFile,
    WriteFileArgs,
    WriteFileConfig,
)
from vibe.core.tools.permissions import (
    ApprovedRule,
    PermissionContext,
    PermissionScope,
    RequiredPermission,
)
from vibe.core.tools.utils import wildcard_match


class TestBashGranularPermissions:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self.workdir = tmp_path

    def _bash(self, **kwargs):
        config = BashToolConfig(**kwargs)
        return Bash(config_getter=lambda: config, state=BaseToolState())

    def test_allowlisted_command_always(self):
        bash = self._bash()
        result = bash.resolve_permission(BashArgs(command="git status"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_denylisted_command_never(self):
        bash = self._bash()
        result = bash.resolve_permission(BashArgs(command="vim file.txt"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_standalone_denylisted_never(self):
        bash = self._bash()
        result = bash.resolve_permission(BashArgs(command="python"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_standalone_denylisted_with_args_not_denied(self):
        bash = self._bash()
        result = bash.resolve_permission(BashArgs(command="python script.py"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ASK

    def test_unknown_command_returns_permission_context(self):
        bash = self._bash()
        result = bash.resolve_permission(BashArgs(command="npm install"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ASK
        assert len(result.required_permissions) == 1
        rp = result.required_permissions[0]
        assert rp.scope is PermissionScope.COMMAND_PATTERN
        assert rp.session_pattern == "npm install *"

    def test_arity_based_prefix(self):
        bash = self._bash()
        result = bash.resolve_permission(BashArgs(command="docker compose up -d"))
        assert isinstance(result, PermissionContext)
        rp = result.required_permissions[0]
        assert rp.session_pattern == "docker compose up *"

    def test_multiple_commands_dedup(self):
        bash = self._bash()
        result = bash.resolve_permission(
            BashArgs(command="npm install foo && npm install bar")
        )
        assert isinstance(result, PermissionContext)
        command_labels = [
            rp.label
            for rp in result.required_permissions
            if rp.scope is PermissionScope.COMMAND_PATTERN
        ]
        assert command_labels == ["npm install *"]

    def test_cd_excluded_from_command_patterns(self):
        bash = self._bash()
        result = bash.resolve_permission(BashArgs(command="cd /tmp"))
        assert isinstance(result, PermissionContext)
        assert all(
            rp.scope is not PermissionScope.COMMAND_PATTERN
            for rp in result.required_permissions
        )

    def test_outside_directory_detection(self):
        bash = self._bash()
        result = bash.resolve_permission(BashArgs(command="mkdir /tmp/test"))
        assert isinstance(result, PermissionContext)
        outside = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.OUTSIDE_DIRECTORY
        ]
        assert len(outside) >= 1

    def test_outside_directory_has_glob_pattern(self):
        bash = self._bash()
        result = bash.resolve_permission(BashArgs(command="mkdir /tmp/test"))
        assert isinstance(result, PermissionContext)
        outside = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.OUTSIDE_DIRECTORY
        ]
        assert any("/tmp" in rp.session_pattern for rp in outside)

    def test_in_workdir_no_outside_directory(self):
        bash = self._bash()
        (self.workdir / "subdir").mkdir()
        result = bash.resolve_permission(BashArgs(command="mkdir subdir/child"))
        assert isinstance(result, PermissionContext)
        outside = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.OUTSIDE_DIRECTORY
        ]
        assert len(outside) == 0

    def test_rm_uses_arity_based_pattern(self):
        bash = self._bash()
        result = bash.resolve_permission(BashArgs(command="rm -rf /tmp/something"))
        assert isinstance(result, PermissionContext)
        cmd_perms = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.COMMAND_PATTERN
        ]
        assert len(cmd_perms) == 1
        assert cmd_perms[0].session_pattern == "rm *"

    def test_sensitive_sudo_exact_pattern(self):
        bash = self._bash()
        result = bash.resolve_permission(BashArgs(command="sudo apt install foo"))
        assert isinstance(result, PermissionContext)
        cmd_perms = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.COMMAND_PATTERN
        ]
        assert cmd_perms[0].session_pattern == "sudo apt install foo"

    def test_rmdir_uses_arity_based_pattern(self):
        bash = self._bash()
        result = bash.resolve_permission(BashArgs(command="rmdir foo"))
        assert isinstance(result, PermissionContext)
        cmd_perms = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.COMMAND_PATTERN
        ]
        assert cmd_perms[0].session_pattern == "rmdir *"

    def test_sensitive_bypasses_allowlist(self):
        bash = self._bash(allowlist=["sudo"])
        result = bash.resolve_permission(BashArgs(command="sudo ls"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ASK

    def test_sensitive_bypasses_global_always_permission(self):
        bash = self._bash(permission=ToolPermission.ALWAYS)
        result = bash.resolve_permission(BashArgs(command="sudo ls"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ASK

    def test_allowlisted_outside_dir_still_asks(self):
        bash = self._bash()
        # cat is allowlisted but /etc/passwd is outside workdir
        result = bash.resolve_permission(BashArgs(command="cat /etc/passwd"))
        assert isinstance(result, PermissionContext)
        outside = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.OUTSIDE_DIRECTORY
        ]
        assert len(outside) == 1

    def test_allowlisted_relative_traversal_outside_dir_still_asks(self):
        bash = self._bash()
        (self.workdir / "src").mkdir()
        result = bash.resolve_permission(
            BashArgs(command="cat src/../../../etc/passwd")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ASK
        outside = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.OUTSIDE_DIRECTORY
        ]
        assert len(outside) >= 1

    def test_allowlisted_in_workdir_subdir_auto_approves(self):
        bash = self._bash()
        (self.workdir / "foo").mkdir()
        (self.workdir / "foo" / "bar.txt").touch()
        result = bash.resolve_permission(BashArgs(command="cat foo/bar.txt"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_allowlisted_in_workdir_auto_approves(self):
        bash = self._bash()
        result = bash.resolve_permission(BashArgs(command="cat README.md"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_mixed_allowlisted_and_not(self):
        bash = self._bash()
        result = bash.resolve_permission(
            BashArgs(command="echo hello && npm install foo")
        )
        assert isinstance(result, PermissionContext)
        cmd_perms = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.COMMAND_PATTERN
        ]
        assert len(cmd_perms) == 1
        assert cmd_perms[0].session_pattern == "npm install *"

    def test_empty_command_returns_none(self):
        bash = self._bash()
        assert bash.resolve_permission(BashArgs(command="")) is None

    def test_chmod_plus_skipped_as_flag(self):
        bash = self._bash()
        result = bash.resolve_permission(BashArgs(command="chmod +x /tmp/script.sh"))
        assert isinstance(result, PermissionContext)
        outside = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.OUTSIDE_DIRECTORY
        ]
        assert len(outside) >= 1


class TestReadFileGranularPermissions:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self.workdir = tmp_path

    def _read_file(self, **kwargs):
        config = ReadFileToolConfig(**kwargs)
        return ReadFile(config_getter=lambda: config, state=ReadFileState())

    def test_in_workdir_normal_file_returns_none(self):
        (self.workdir / "test.py").touch()
        tool = self._read_file()
        assert tool.resolve_permission(ReadFileArgs(path="test.py")) is None

    def test_outside_workdir_returns_permission_context(self):
        tool = self._read_file()
        result = tool.resolve_permission(ReadFileArgs(path="/tmp/file.txt"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ASK
        outside = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.OUTSIDE_DIRECTORY
        ]
        assert len(outside) == 1

    def test_sensitive_env_file_returns_permission_context(self):
        (self.workdir / ".env").touch()
        tool = self._read_file()
        result = tool.resolve_permission(ReadFileArgs(path=".env"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ASK
        sensitive = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.FILE_PATTERN
        ]
        assert len(sensitive) == 1
        assert sensitive[0].label.startswith("accessing sensitive files")

    def test_sensitive_env_local_file(self):
        (self.workdir / ".env.local").touch()
        tool = self._read_file()
        result = tool.resolve_permission(ReadFileArgs(path=".env.local"))
        assert isinstance(result, PermissionContext)
        sensitive = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.FILE_PATTERN
        ]
        assert len(sensitive) == 1

    def test_sensitive_outside_both_permissions(self):
        tool = self._read_file()
        result = tool.resolve_permission(ReadFileArgs(path="/tmp/.env"))
        assert isinstance(result, PermissionContext)
        scopes = {rp.scope for rp in result.required_permissions}
        assert PermissionScope.FILE_PATTERN in scopes
        assert PermissionScope.OUTSIDE_DIRECTORY in scopes

    def test_denylisted_returns_never(self):
        tool = self._read_file(denylist=["*/secret*"])
        result = tool.resolve_permission(ReadFileArgs(path="secret.key"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_allowlisted_returns_always(self):
        tool = self._read_file(allowlist=["*/README*"])
        result = tool.resolve_permission(
            ReadFileArgs(path=str(self.workdir / "README.md"))
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_custom_sensitive_patterns(self):
        (self.workdir / "credentials.json").touch()
        tool = self._read_file(sensitive_patterns=["*/credentials*"])
        result = tool.resolve_permission(ReadFileArgs(path="credentials.json"))
        assert isinstance(result, PermissionContext)


class TestWriteFileGranularPermissions:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self.workdir = tmp_path

    def _write_file(self):
        config = WriteFileConfig()
        return WriteFile(config_getter=lambda: config, state=BaseToolState())

    def test_in_workdir_returns_none(self):
        tool = self._write_file()
        assert (
            tool.resolve_permission(WriteFileArgs(path="test.py", content="x")) is None
        )

    def test_outside_workdir_returns_permission_context(self):
        tool = self._write_file()
        result = tool.resolve_permission(
            WriteFileArgs(path="/tmp/file.txt", content="x")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ASK

    def test_sensitive_env_file_asks(self):
        (self.workdir / ".env").touch()
        tool = self._write_file()
        result = tool.resolve_permission(
            WriteFileArgs(path=".env", content="x", overwrite=True)
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ASK


class TestSearchReplaceGranularPermissions:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

    def test_outside_workdir_returns_permission_context(self):
        config = SearchReplaceConfig()
        tool = SearchReplace(config_getter=lambda: config, state=BaseToolState())
        result = tool.resolve_permission(
            SearchReplaceArgs(file_path="/tmp/file.py", content="x")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ASK


class TestGrepGranularPermissions:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self.workdir = tmp_path

    def _grep(self):
        config = GrepToolConfig()
        return Grep(config_getter=lambda: config, state=BaseToolState())

    def test_in_workdir_normal_path_returns_none(self):
        tool = self._grep()
        assert tool.resolve_permission(GrepArgs(pattern="foo", path=".")) is None

    def test_outside_workdir_returns_permission_context(self):
        tool = self._grep()
        result = tool.resolve_permission(GrepArgs(pattern="foo", path="/tmp"))
        assert isinstance(result, PermissionContext)

    def test_sensitive_env_directory(self):
        (self.workdir / ".env").touch()
        tool = self._grep()
        result = tool.resolve_permission(GrepArgs(pattern="foo", path=".env"))
        assert isinstance(result, PermissionContext)
        sensitive = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.FILE_PATTERN
        ]
        assert len(sensitive) == 1


class TestApprovalFlowSimulation:
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

    def _is_covered(
        self, tool_name: str, rp: RequiredPermission, rules: list[ApprovedRule]
    ) -> bool:
        return any(
            rule.tool_name == tool_name
            and rule.scope == rp.scope
            and wildcard_match(rp.invocation_pattern, rule.session_pattern)
            for rule in rules
        )

    def test_mkdir_approved_covers_subsequent_mkdir(self):
        rules = [
            ApprovedRule(
                tool_name="bash",
                scope=PermissionScope.COMMAND_PATTERN,
                session_pattern="mkdir *",
            )
        ]
        bash = Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())
        result = bash.resolve_permission(BashArgs(command="mkdir another_dir"))
        assert isinstance(result, PermissionContext)
        uncovered = [
            rp
            for rp in result.required_permissions
            if not self._is_covered("bash", rp, rules)
        ]
        assert not any(rp.scope is PermissionScope.COMMAND_PATTERN for rp in uncovered)

    def test_mkdir_approved_does_not_cover_npm(self):
        rules = [
            ApprovedRule(
                tool_name="bash",
                scope=PermissionScope.COMMAND_PATTERN,
                session_pattern="mkdir *",
            )
        ]
        bash = Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())
        result = bash.resolve_permission(BashArgs(command="npm install"))
        assert isinstance(result, PermissionContext)
        uncovered = [
            rp
            for rp in result.required_permissions
            if not self._is_covered("bash", rp, rules)
        ]
        assert len(uncovered) == 1
        assert uncovered[0].session_pattern == "npm install *"

    def test_outside_dir_approved_covers_subsequent(self):
        bash = Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())
        result = bash.resolve_permission(BashArgs(command="mkdir /tmp/newdir"))
        assert isinstance(result, PermissionContext)
        outside_rps = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.OUTSIDE_DIRECTORY
        ]
        assert len(outside_rps) == 1
        # Resolved pattern may differ per OS (e.g. /private/tmp/* on macOS)
        rules = [
            ApprovedRule(
                tool_name="bash",
                scope=PermissionScope.OUTSIDE_DIRECTORY,
                session_pattern=outside_rps[0].session_pattern,
            ),
            ApprovedRule(
                tool_name="bash",
                scope=PermissionScope.COMMAND_PATTERN,
                session_pattern="mkdir *",
            ),
        ]
        uncovered = [
            rp
            for rp in result.required_permissions
            if not self._is_covered("bash", rp, rules)
        ]
        assert len(uncovered) == 0

    def test_rm_approved_covers_subsequent_rm(self):
        rules = [
            ApprovedRule(
                tool_name="bash",
                scope=PermissionScope.COMMAND_PATTERN,
                session_pattern="rm *",
            )
        ]
        bash = Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())
        result = bash.resolve_permission(BashArgs(command="rm -rf /tmp/something"))
        assert isinstance(result, PermissionContext)
        cmd_perms = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.COMMAND_PATTERN
        ]
        assert cmd_perms[0].session_pattern == "rm *"
        uncovered = [rp for rp in cmd_perms if not self._is_covered("bash", rp, rules)]
        assert len(uncovered) == 0

    def test_sudo_exact_approval_doesnt_cover_different_invocation(self):
        rules = [
            ApprovedRule(
                tool_name="bash",
                scope=PermissionScope.COMMAND_PATTERN,
                session_pattern="sudo apt install foo",
            )
        ]
        bash = Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())
        result = bash.resolve_permission(BashArgs(command="sudo apt install bar"))
        assert isinstance(result, PermissionContext)
        cmd_perms = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.COMMAND_PATTERN
        ]
        uncovered = [rp for rp in cmd_perms if not self._is_covered("bash", rp, rules)]
        assert len(uncovered) == 1

    def test_read_file_sensitive_approved_covers_subsequent(self):
        rules = [
            ApprovedRule(
                tool_name="read_file",
                scope=PermissionScope.FILE_PATTERN,
                session_pattern="*",
            )
        ]
        rp = RequiredPermission(
            scope=PermissionScope.FILE_PATTERN,
            invocation_pattern=".env.production",
            session_pattern="*",
            label="reading sensitive files (read_file)",
        )
        assert self._is_covered("read_file", rp, rules)

    def test_different_tool_rule_doesnt_cover(self):
        rules = [
            ApprovedRule(
                tool_name="bash",
                scope=PermissionScope.COMMAND_PATTERN,
                session_pattern="mkdir *",
            )
        ]
        rp = RequiredPermission(
            scope=PermissionScope.COMMAND_PATTERN,
            invocation_pattern="mkdir foo",
            session_pattern="mkdir *",
            label="mkdir *",
        )
        assert not self._is_covered("grep", rp, rules)


class TestWebFetchPermissions:
    def _make_webfetch(self) -> WebFetch:
        return WebFetch(config_getter=lambda: WebFetchConfig(), state=BaseToolState())

    def test_returns_url_pattern_with_domain(self):
        wf = self._make_webfetch()
        result = wf.resolve_permission(
            WebFetchArgs(url="https://docs.python.org/3/library")
        )
        assert isinstance(result, PermissionContext)
        assert len(result.required_permissions) == 1
        rp = result.required_permissions[0]
        assert rp.scope is PermissionScope.URL_PATTERN
        assert rp.invocation_pattern == "docs.python.org"
        assert rp.session_pattern == "docs.python.org"
        assert "docs.python.org" in rp.label

    def test_http_url(self):
        wf = self._make_webfetch()
        result = wf.resolve_permission(WebFetchArgs(url="http://example.com/page"))
        assert isinstance(result, PermissionContext)
        rp = result.required_permissions[0]
        assert rp.invocation_pattern == "example.com"

    def test_url_without_scheme(self):
        wf = self._make_webfetch()
        result = wf.resolve_permission(WebFetchArgs(url="github.com/anthropics"))
        assert isinstance(result, PermissionContext)
        rp = result.required_permissions[0]
        assert rp.invocation_pattern == "github.com"

    def test_url_with_port(self):
        wf = self._make_webfetch()
        result = wf.resolve_permission(WebFetchArgs(url="http://localhost:8080/api"))
        assert isinstance(result, PermissionContext)
        rp = result.required_permissions[0]
        assert rp.invocation_pattern == "localhost:8080"

    def test_url_without_scheme_with_port(self):
        wf = self._make_webfetch()
        result = wf.resolve_permission(WebFetchArgs(url="example.com:3000/path"))
        assert isinstance(result, PermissionContext)
        rp = result.required_permissions[0]
        assert rp.invocation_pattern == "example.com:3000"

    def test_different_domains_not_covered(self):
        rules = [
            ApprovedRule(
                tool_name="web_fetch",
                scope=PermissionScope.URL_PATTERN,
                session_pattern="docs.python.org",
            )
        ]
        rp = RequiredPermission(
            scope=PermissionScope.URL_PATTERN,
            invocation_pattern="evil.com",
            session_pattern="evil.com",
            label="fetching from evil.com",
        )
        covered = any(
            rule.tool_name == "web_fetch"
            and rule.scope == rp.scope
            and wildcard_match(rp.invocation_pattern, rule.session_pattern)
            for rule in rules
        )
        assert not covered

    def test_same_domain_covered(self):
        rules = [
            ApprovedRule(
                tool_name="web_fetch",
                scope=PermissionScope.URL_PATTERN,
                session_pattern="docs.python.org",
            )
        ]
        rp = RequiredPermission(
            scope=PermissionScope.URL_PATTERN,
            invocation_pattern="docs.python.org",
            session_pattern="docs.python.org",
            label="fetching from docs.python.org",
        )
        covered = any(
            rule.tool_name == "web_fetch"
            and rule.scope == rp.scope
            and wildcard_match(rp.invocation_pattern, rule.session_pattern)
            for rule in rules
        )
        assert covered

    def test_double_slash_url(self):
        wf = self._make_webfetch()
        result = wf.resolve_permission(WebFetchArgs(url="//cdn.example.com/lib.js"))
        assert isinstance(result, PermissionContext)
        rp = result.required_permissions[0]
        assert rp.invocation_pattern == "cdn.example.com"

    def test_config_permission_always_honored(self):
        wf = WebFetch(
            config_getter=lambda: WebFetchConfig(permission=ToolPermission.ALWAYS),
            state=BaseToolState(),
        )
        result = wf.resolve_permission(WebFetchArgs(url="https://example.com"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_config_permission_never_honored(self):
        wf = WebFetch(
            config_getter=lambda: WebFetchConfig(permission=ToolPermission.NEVER),
            state=BaseToolState(),
        )
        result = wf.resolve_permission(WebFetchArgs(url="https://example.com"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_config_permission_ask_falls_through_to_domain(self):
        wf = WebFetch(
            config_getter=lambda: WebFetchConfig(permission=ToolPermission.ASK),
            state=BaseToolState(),
        )
        result = wf.resolve_permission(WebFetchArgs(url="https://example.com"))
        assert isinstance(result, PermissionContext)
        assert result.required_permissions[0].invocation_pattern == "example.com"


class TestCollectOutsideDirs:
    """Tests for _collect_outside_dirs helper."""

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self.workdir = tmp_path

    def test_relative_path_resolving_outside_workdir(self):
        dirs = _collect_outside_dirs(["cat ../../etc/passwd"])
        # The relative path resolves outside workdir, should collect parent dir
        assert len(dirs) >= 1

    def test_multiple_targets_in_one_command(self):
        dirs = _collect_outside_dirs(["cp /tmp/a /var/b"])
        assert len(dirs) == 2

    def test_chmod_skips_plus_x_token(self):
        dirs = _collect_outside_dirs(["chmod +x /tmp/script.sh"])
        # +x should be skipped, only /tmp/script.sh should be considered
        assert len(dirs) >= 1
        # Verify no dir was created from the "+x" token
        for d in dirs:
            assert "+x" not in d

    def test_empty_command_list(self):
        assert _collect_outside_dirs([]) == set()

    def test_home_relative_path(self):
        home = os.path.expanduser("~")
        dirs = _collect_outside_dirs(["cat ~/some_file"])
        # ~/some_file resolves to home directory, which is likely outside workdir
        if home != str(self.workdir):
            assert len(dirs) >= 1

    def test_in_workdir_path_not_collected(self):
        (self.workdir / "local_file").touch()
        dirs = _collect_outside_dirs(["cat ./local_file"])
        assert len(dirs) == 0

    def test_traversal_path_without_dot_prefix(self):
        """Paths like src/../../../etc/passwd don't start with . but contain /."""
        (self.workdir / "src").mkdir()
        dirs = _collect_outside_dirs(["cat src/../../../etc/passwd"])
        assert len(dirs) >= 1

    def test_in_workdir_subdir_path_not_collected(self):
        """foo/bar inside workdir should not be flagged."""
        (self.workdir / "foo").mkdir()
        (self.workdir / "foo" / "bar").touch()
        dirs = _collect_outside_dirs(["cat foo/bar"])
        assert len(dirs) == 0
