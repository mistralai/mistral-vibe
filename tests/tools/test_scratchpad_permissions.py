from __future__ import annotations

import pytest

import vibe.core.scratchpad as scratchpad_mod
from vibe.core.scratchpad import init_scratchpad
from vibe.core.tools.base import BaseToolState, ToolPermission
from vibe.core.tools.builtins.bash import (
    Bash,
    BashArgs,
    BashToolConfig,
    _collect_outside_dirs,
)
from vibe.core.tools.builtins.read_file import (
    ReadFile,
    ReadFileArgs,
    ReadFileState,
    ReadFileToolConfig,
)
from vibe.core.tools.builtins.write_file import (
    WriteFile,
    WriteFileArgs,
    WriteFileConfig,
)
from vibe.core.tools.permissions import PermissionContext, PermissionScope


@pytest.fixture(autouse=True)
def _setup_scratchpad():
    init_scratchpad("test-session")


class TestFileToolScratchpadPermissions:
    def test_write_file_scratchpad_always_allowed(self):
        sp = scratchpad_mod.get_scratchpad_dir("test-session")
        assert sp is not None
        tool = WriteFile(config_getter=lambda: WriteFileConfig(), state=BaseToolState())
        result = tool.resolve_permission(
            WriteFileArgs(path=str(sp / "draft.py"), content="x")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_read_file_scratchpad_always_allowed(self):
        sp = scratchpad_mod.get_scratchpad_dir("test-session")
        assert sp is not None
        tool = ReadFile(
            config_getter=lambda: ReadFileToolConfig(), state=ReadFileState()
        )
        result = tool.resolve_permission(ReadFileArgs(path=str(sp / "notes.txt")))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_scratchpad_env_file_still_allowed(self):
        """Scratchpad bypasses sensitive pattern checks."""
        sp = scratchpad_mod.get_scratchpad_dir("test-session")
        assert sp is not None
        tool = WriteFile(config_getter=lambda: WriteFileConfig(), state=BaseToolState())
        result = tool.resolve_permission(
            WriteFileArgs(path=str(sp / ".env"), content="SECRET=x")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_non_scratchpad_outside_dir_still_asks(self):
        tool = WriteFile(config_getter=lambda: WriteFileConfig(), state=BaseToolState())
        result = tool.resolve_permission(
            WriteFileArgs(path="/tmp/not-scratchpad/file.txt", content="x")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ASK


class TestBashScratchpadPermissions:
    def test_scratchpad_path_not_flagged_as_outside_dir(self):
        sp = scratchpad_mod.get_scratchpad_dir("test-session")
        assert sp is not None
        dirs = _collect_outside_dirs([f"cat {sp}/file.txt"])
        assert len(dirs) == 0

    def test_non_scratchpad_outside_path_still_flagged(self):
        dirs = _collect_outside_dirs(["cat /etc/hosts"])
        assert len(dirs) >= 1

    def test_bash_scratchpad_mkdir_no_outside_dir_permission(self):
        sp = scratchpad_mod.get_scratchpad_dir("test-session")
        assert sp is not None
        bash = Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())
        result = bash.resolve_permission(BashArgs(command=f"mkdir {sp}/subdir"))
        assert isinstance(result, PermissionContext)
        outside = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.OUTSIDE_DIRECTORY
        ]
        assert len(outside) == 0
