from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, ToolError, ToolPermission
from vibe.core.tools.builtins.bash import Bash, BashArgs, BashToolConfig
from vibe.core.tools.permissions import PermissionContext


@pytest.fixture
def bash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = BashToolConfig()
    # Mock get_shell_executable to return None for existing tests (use cmd.exe)
    monkeypatch.setattr(
        "vibe.core.tools.builtins.bash.get_shell_executable", lambda config=None: None
    )
    return Bash(config_getter=lambda: config, state=BaseToolState())


@pytest.mark.asyncio
async def test_runs_echo_successfully(bash):
    result = await collect_result(bash.run(BashArgs(command="echo hello")))

    assert result.returncode == 0
    assert result.stdout == "hello\n"
    assert result.stderr == ""


@pytest.mark.asyncio
async def test_fails_cat_command_with_missing_file(bash):
    with pytest.raises(ToolError) as err:
        await collect_result(bash.run(BashArgs(command="cat missing_file.txt")))

    message = str(err.value)
    assert "Command failed" in message
    assert "Return code: 1" in message
    assert "No such file or directory" in message


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.name == "nt",
    reason="Skipped on Windows due to shell path format differences (Bash vs. cmd.exe)",
)
async def test_uses_effective_workdir(bash, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = await collect_result(bash.run(BashArgs(command="pwd")))
    assert result.stdout.strip() == str(tmp_path)


@pytest.mark.asyncio
async def test_handles_timeout(bash):
    with pytest.raises(ToolError) as err:
        await collect_result(bash.run(BashArgs(command="sleep 2", timeout=1)))

    assert "Command timed out after 1s" in str(err.value)


@pytest.mark.asyncio
async def test_truncates_output_to_max_bytes(bash):
    config = BashToolConfig(max_output_bytes=5)
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    result = await collect_result(
        bash_tool.run(BashArgs(command="printf 'abcdefghij'"))
    )

    assert result.stdout == "abcde"
    assert result.stderr == ""
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_cat_preserves_accents_from_latin1_encoded_file(bash, tmp_path):
    file = tmp_path / "menu.txt"
    file.write_bytes("café au lait\nthé glacé\n".encode("latin-1"))

    result = await collect_result(bash.run(BashArgs(command=f"cat {file.name}")))

    assert result.returncode == 0
    assert "\ufffd" not in result.stdout
    assert result.stdout == "café au lait\nthé glacé\n"


@pytest.mark.parametrize("predicate", ["-exec", "-execdir", "-ok", "-okdir"])
def test_find_execution_predicates_force_ask(predicate: str):
    config = BashToolConfig(permission=ToolPermission.ALWAYS)
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    permission = bash_tool.resolve_permission(
        BashArgs(command=f"find . {predicate} id \\;")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert [required.label for required in permission.required_permissions] == [
        f"find . {predicate} id \\;"
    ]


def test_find_exec_compound_includes_companion_required_permission():
    config = BashToolConfig(permission=ToolPermission.ALWAYS)
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    permission = bash_tool.resolve_permission(
        BashArgs(command='find . -exec id \\; && python3 -c "import os"')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    labels = {rp.label for rp in permission.required_permissions}
    assert any("find" in label for label in labels), (
        f"Expected a find-exec RequiredPermission, got {labels}"
    )
    assert any("python3" in label for label in labels), (
        f"Companion command should also require permission, got {labels}"
    )


def test_find_execution_predicate_does_not_override_denylist():
    config = BashToolConfig(denylist=["passwd"])
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    permission = bash_tool.resolve_permission(
        BashArgs(command="find . -exec id \\; && passwd root")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER
    assert "matches denylist pattern 'passwd'" in (permission.reason or "")


def test_resolve_permission():
    config = BashToolConfig(allowlist=["echo", "pwd"], denylist=["rm"])
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    allowlisted = bash_tool.resolve_permission(BashArgs(command="echo hi"))
    denylisted = bash_tool.resolve_permission(BashArgs(command="rm -rf /tmp"))
    mixed = bash_tool.resolve_permission(BashArgs(command="pwd && whoami"))
    empty = bash_tool.resolve_permission(BashArgs(command=""))

    assert isinstance(allowlisted, PermissionContext)
    assert allowlisted.permission is ToolPermission.ALWAYS
    assert isinstance(denylisted, PermissionContext)
    assert denylisted.permission is ToolPermission.NEVER
    assert isinstance(mixed, PermissionContext)
    assert mixed.permission is ToolPermission.ASK
    assert any(rp.label == "whoami *" for rp in mixed.required_permissions)
    assert empty is None


class TestResolvePermissionWindowsSyntax:
    """Verify allowlist/denylist works with Windows-style commands."""

    def _make_bash(self, **kwargs) -> Bash:
        config = BashToolConfig(**kwargs)
        return Bash(config_getter=lambda: config, state=BaseToolState())

    def test_dir_with_windows_flags_allowlisted(self):
        bash_tool = self._make_bash(allowlist=["dir"])
        result = bash_tool.resolve_permission(BashArgs(command="dir /s /b"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_type_command_allowlisted(self):
        bash_tool = self._make_bash(allowlist=["type"])
        result = bash_tool.resolve_permission(BashArgs(command="type file.txt"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_findstr_allowlisted(self):
        bash_tool = self._make_bash(allowlist=["findstr"])
        result = bash_tool.resolve_permission(
            BashArgs(command="findstr /s pattern *.txt")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_ver_allowlisted(self):
        bash_tool = self._make_bash(allowlist=["ver"])
        result = bash_tool.resolve_permission(BashArgs(command="ver"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_where_allowlisted(self):
        bash_tool = self._make_bash(allowlist=["where"])
        result = bash_tool.resolve_permission(BashArgs(command="where python"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_cmd_k_denylisted(self):
        bash_tool = self._make_bash(denylist=["cmd /k"])
        result = bash_tool.resolve_permission(BashArgs(command="cmd /k something"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_powershell_noexit_denylisted(self):
        bash_tool = self._make_bash(denylist=["powershell -NoExit"])
        result = bash_tool.resolve_permission(BashArgs(command="powershell -NoExit"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_notepad_denylisted(self):
        bash_tool = self._make_bash(denylist=["notepad"])
        result = bash_tool.resolve_permission(BashArgs(command="notepad file.txt"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_cmd_standalone_denylisted(self):
        bash_tool = self._make_bash(denylist_standalone=["cmd"])
        result = bash_tool.resolve_permission(BashArgs(command="cmd"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_powershell_standalone_denylisted(self):
        bash_tool = self._make_bash(denylist_standalone=["powershell"])
        result = bash_tool.resolve_permission(BashArgs(command="powershell"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_powershell_cmdlet_asks(self):
        bash_tool = self._make_bash(allowlist=["dir", "echo"])
        result = bash_tool.resolve_permission(BashArgs(command="Get-ChildItem -Path ."))
        assert isinstance(result, PermissionContext)
        assert result.permission == ToolPermission.ASK

    def test_mixed_allowed_and_unknown_asks(self):
        bash_tool = self._make_bash(allowlist=["git status"])
        result = bash_tool.resolve_permission(
            BashArgs(command="git status && npm install")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission == ToolPermission.ASK

    def test_chained_windows_commands_all_allowed(self):
        bash_tool = self._make_bash(allowlist=["dir", "echo"])
        result = bash_tool.resolve_permission(BashArgs(command="dir /s && echo done"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_chained_commands_one_denied(self):
        bash_tool = self._make_bash(allowlist=["dir"], denylist=["rm"])
        result = bash_tool.resolve_permission(BashArgs(command="dir /s && rm -rf /"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_piped_windows_commands(self):
        bash_tool = self._make_bash(allowlist=["findstr", "type"])
        result = bash_tool.resolve_permission(
            BashArgs(command="type file.txt | findstr pattern")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS


class TestDenylistWordBoundary:
    """Verify denylist matches whole command names, not prefixes."""

    def _make_bash(self, **kwargs) -> Bash:
        config = BashToolConfig(**kwargs)
        return Bash(config_getter=lambda: config, state=BaseToolState())

    def test_vi_blocks_vi_exact(self):
        bash_tool = self._make_bash(denylist=["vi"])
        result = bash_tool.resolve_permission(BashArgs(command="vi"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_vi_blocks_vi_with_args(self):
        bash_tool = self._make_bash(denylist=["vi"])
        result = bash_tool.resolve_permission(BashArgs(command="vi file.txt"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_vi_does_not_block_vibe(self):
        bash_tool = self._make_bash(denylist=["vi"])
        result = bash_tool.resolve_permission(BashArgs(command="vibe -p hello"))
        assert result is None or result.permission is not ToolPermission.NEVER

    def test_multiword_pattern_still_works(self):
        bash_tool = self._make_bash(denylist=["bash -i"])
        result = bash_tool.resolve_permission(BashArgs(command="bash -i"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_multiword_pattern_with_trailing_args(self):
        bash_tool = self._make_bash(denylist=["bash -i"])
        result = bash_tool.resolve_permission(BashArgs(command="bash -i extra"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_multiword_pattern_does_not_match_partial(self):
        bash_tool = self._make_bash(denylist=["bash -i"])
        result = bash_tool.resolve_permission(BashArgs(command="bash -init"))
        assert result is None or result.permission is not ToolPermission.NEVER

    def test_deny_reason_is_set(self):
        bash_tool = self._make_bash(denylist=["vim"])
        result = bash_tool.resolve_permission(BashArgs(command="vim file.txt"))
        assert isinstance(result, PermissionContext)
        assert result.reason is not None
        assert "vim" in result.reason

    def test_standalone_deny_reason_is_set(self):
        bash_tool = self._make_bash(denylist_standalone=["python"])
        result = bash_tool.resolve_permission(BashArgs(command="python"))
        assert isinstance(result, PermissionContext)
        assert result.reason is not None
        assert result.permission is ToolPermission.NEVER
        assert "python" in result.reason
        assert "standalone" in result.reason

    def test_allowlist_does_not_match_prefix(self):
        bash_tool = self._make_bash(allowlist=["cat"])
        result = bash_tool.resolve_permission(BashArgs(command="catalog"))
        assert result is not None and result.permission is not ToolPermission.ALWAYS


def test_default_allowlist_includes_read_only_commands():
    """Test that common read-only commands are in the default allowlist."""
    from vibe.core.tools.builtins.bash import _get_default_allowlist

    allowlist = _get_default_allowlist()

    # Read-only commands that should be in the default allowlist
    read_only_commands = [
        "grep",
        "cut",
        "sort",
        "tr",
        "uniq",
        "basename",
        "comm",
        "date",
        "diff",
        "dirname",
        "du",
        "fmt",
        "fold",
        "join",
        "less",
        "md5sum",
        "more",
        "nl",
        "od",
        "paste",
        "readlink",
        "sha1sum",
        "sha256sum",
        "shasum",
        "stat",
        "sum",
        "tac",
        "which",
    ]

    for cmd in read_only_commands:
        assert cmd in allowlist, (
            f"Read-only command '{cmd}' should be in default allowlist"
        )


def test_new_read_only_commands_are_allowlisted():
    """Test that newly added read-only commands are automatically allowed."""
    config = BashToolConfig()  # Use default config
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    # Test that newly added read-only commands are allowed by default
    test_commands = [
        "grep pattern file.txt",
        "cut -d',' -f1 file.csv",
        "sort file.txt",
        "tr 'a' 'b' < file.txt",
        "uniq file.txt",
        "basename /path/to/file",
        "comm file1.txt file2.txt",
        "date",
        "diff file1.txt file2.txt",
        "dirname /path/to/file",
        "du -sh .",
        "fmt file.txt",
        "fold -w 80 file.txt",
        "join -t',' file1.csv file2.csv",
        "less file.txt",
        "md5sum file.txt",
        "more file.txt",
        "nl file.txt",
        "od -c file.bin",
        "paste file1.txt file2.txt",
        "readlink -f /path/to/link",
        "sha1sum file.txt",
        "sha256sum file.txt",
        "shasum file.txt",
        "stat file.txt",
        "sum file.txt",
        "tac file.txt",
        "which python",
    ]

    for cmd in test_commands:
        permission = bash_tool.resolve_permission(BashArgs(command=cmd))
        assert isinstance(permission, PermissionContext), (
            f"Permission should be PermissionContext for '{cmd}'"
        )
        assert permission.permission is ToolPermission.ALWAYS, (
            f"Command '{cmd}' should be always allowed"
        )


class TestGetShellExecutable:
    """Tests for _get_shell_executable() function."""

    @patch("vibe.core.tools.builtins.bash.is_windows")
    @patch("vibe.core.utils.platform.is_windows")
    @patch("vibe.core.utils.platform.which")
    def test_returns_bash_if_in_path(
        self, mock_which, mock_plat_is_win, mock_bash_is_win
    ):
        """Test that bash.exe is returned if found in PATH."""
        mock_bash_is_win.return_value = True
        mock_plat_is_win.return_value = True
        mock_which.side_effect = lambda cmd: (
            "C:\\bash.exe" if cmd == "bash.exe" else None
        )
        from vibe.core.utils.platform import get_shell_executable

        assert get_shell_executable() == r"C:\bash.exe"

    @patch("vibe.core.tools.builtins.bash.is_windows")
    @patch("vibe.core.utils.platform.is_windows")
    @patch("vibe.core.utils.platform.which")
    @patch("vibe.core.utils.platform.Path")
    def test_derives_bash_from_git(
        self, mock_path, mock_which, mock_plat_is_win, mock_bash_is_win
    ):
        """Test that bash.exe is derived from git.exe's location."""
        mock_bash_is_win.return_value = True
        mock_plat_is_win.return_value = True
        mock_which.side_effect = lambda cmd: (
            "C:\\Program Files\\Git\\cmd\\git.exe" if cmd == "git.exe" else None
        )

        # Create a mock Path instance for git.exe's path
        git_path_mock = mock_path.return_value
        git_path_mock.__str__.return_value = "C:\\Program Files\\Git\\cmd\\git.exe"

        # Mock the parent chain: git.exe -> cmd -> Git
        cmd_dir_mock = git_path_mock.parent
        cmd_dir_mock.__str__.return_value = "C:\\Program Files\\Git\\cmd"
        git_dir_mock = cmd_dir_mock.parent
        git_dir_mock.__str__.return_value = "C:\\Program Files\\Git"

        # Mock the bin directory and bash.exe path
        bin_dir_mock = git_dir_mock.__truediv__.return_value
        bin_dir_mock.__str__.return_value = "C:\\Program Files\\Git\\bin"
        bash_exe_mock = bin_dir_mock.__truediv__.return_value
        bash_exe_mock.__str__.return_value = r"C:\Program Files\Git\bin\bash.exe"
        bash_exe_mock.exists.return_value = True

        from vibe.core.utils.platform import get_shell_executable

        assert get_shell_executable() == r"C:\Program Files\Git\bin\bash.exe"

    @patch("vibe.core.tools.builtins.bash.is_windows")
    @patch("vibe.core.utils.platform.is_windows")
    @patch("vibe.core.utils.platform.which")
    @patch("vibe.core.utils.platform.Path")
    def test_falls_back_to_common_paths(
        self, mock_path, mock_which, mock_plat_is_win, mock_bash_is_win
    ):
        """Test that common Bash paths are checked as fallbacks."""
        mock_bash_is_win.return_value = True
        mock_plat_is_win.return_value = True
        mock_which.return_value = None
        # Mock Path.exists to return True for the 5th path (C:/msys32/usr/bin/bash.exe)
        mock_path.return_value.exists.side_effect = [
            False,
            False,
            False,
            False,
            False,
            True,
        ]
        from vibe.core.utils.platform import get_shell_executable

        assert get_shell_executable() == r"C:\msys32\usr\bin\bash.exe"

    @patch("vibe.core.tools.builtins.bash.is_windows")
    @patch("vibe.core.utils.platform.is_windows")
    @patch("vibe.core.utils.platform.which")
    @patch("vibe.core.utils.platform.Path")
    @patch.dict("os.environ", {}, clear=True)
    def test_falls_back_to_cmd(
        self, mock_path, mock_which, mock_plat_is_win, mock_bash_is_win
    ):
        """Test that cmd.exe is returned if no Bash is found."""
        mock_bash_is_win.return_value = True
        mock_plat_is_win.return_value = True
        mock_which.return_value = None
        # Mock Path.exists to return False for all paths
        mock_path.return_value.exists.return_value = False
        # Mock get_windows_bash_path before importing get_shell_executable
        from vibe.core.utils import platform as platform_module

        original_get_windows_bash_path = platform_module.get_windows_bash_path
        platform_module.get_windows_bash_path = lambda: None
        try:
            from vibe.core.utils.platform import get_shell_executable

            # Should fall back to cmd.exe when no bash is found and no COMSPEC
            assert get_shell_executable() == "cmd.exe"
        finally:
            platform_module.get_windows_bash_path = original_get_windows_bash_path

    @patch("vibe.core.utils.platform.is_windows")
    @patch("vibe.core.utils.platform.Path")
    @patch.dict("os.environ", {"VIBE_SHELL": "C:\\custom\\bash.exe"})
    def test_respects_vibe_shell_env_var(self, mock_path, mock_is_windows):
        """Test that VIBE_SHELL environment variable is respected."""
        mock_is_windows.return_value = True
        # Mock Path to make the custom bash path appear to exist
        mock_path.return_value.exists.return_value = True
        from vibe.core.utils.platform import get_shell_executable

        assert get_shell_executable() == "C:\\custom\\bash.exe"

    @patch("vibe.core.utils.platform.is_windows")
    @patch("vibe.core.utils.platform.Path")
    def test_respects_config_preferred_shell(self, mock_path, mock_is_windows):
        """Test that config.preferred_shell is respected."""
        mock_is_windows.return_value = True
        # Mock Path to make the custom bash path appear to exist
        mock_path.return_value.exists.return_value = True
        from vibe.core.tools.builtins.bash import BashToolConfig
        from vibe.core.utils.platform import get_shell_executable

        config = BashToolConfig(preferred_shell="C:\\custom\\bash.exe")
        assert get_shell_executable(config) == "C:\\custom\\bash.exe"
