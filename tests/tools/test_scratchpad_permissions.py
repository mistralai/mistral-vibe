from __future__ import annotations

import pytest

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
    ReadFileConfig,
    ReadFileState,
)
from vibe.core.tools.builtins.write_file import (
    WriteFile,
    WriteFileArgs,
    WriteFileConfig,
)
from vibe.core.tools.permissions import PermissionContext, PermissionScope


@pytest.fixture
def scratchpad():
    path = init_scratchpad("test-session")
    assert path is not None
    return path


class TestFileToolScratchpadPermissions:
    def test_write_file_scratchpad_always_allowed(self, scratchpad):
        tool = WriteFile(
            config_getter=lambda: WriteFileConfig(),
            state=BaseToolState(),
            scratchpad_dir=scratchpad,
        )
        result = tool.resolve_permission(
            WriteFileArgs(file_path=str(scratchpad / "draft.py"), content="x")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_read_scratchpad_always_allowed(self, scratchpad):
        tool = ReadFile(
            config_getter=lambda: ReadFileConfig(),
            state=ReadFileState(),
            scratchpad_dir=scratchpad,
        )
        result = tool.resolve_permission(
            ReadFileArgs(file_path=str(scratchpad / "notes.txt"))
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_scratchpad_env_file_still_allowed(self, scratchpad):
        """Scratchpad bypasses sensitive pattern checks."""
        tool = WriteFile(
            config_getter=lambda: WriteFileConfig(),
            state=BaseToolState(),
            scratchpad_dir=scratchpad,
        )
        result = tool.resolve_permission(
            WriteFileArgs(file_path=str(scratchpad / ".env"), content="SECRET=x")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_non_scratchpad_outside_dir_still_asks(self):
        tool = WriteFile(config_getter=lambda: WriteFileConfig(), state=BaseToolState())
        result = tool.resolve_permission(
            WriteFileArgs(file_path="/tmp/not-scratchpad/file.txt", content="x")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ASK


class TestBashScratchpadPermissions:
    def test_scratchpad_path_not_flagged_as_outside_dir(self, scratchpad):
        dirs = _collect_outside_dirs(
            [f"cat {scratchpad}/file.txt"], scratchpad_dir=scratchpad
        )
        assert len(dirs) == 0

    def test_non_scratchpad_outside_path_still_flagged(self):
        dirs = _collect_outside_dirs(["cat /etc/hosts"])
        assert len(dirs) >= 1

    def test_bash_scratchpad_mkdir_no_outside_dir_permission(self, scratchpad):
        bash = Bash(
            config_getter=lambda: BashToolConfig(),
            state=BaseToolState(),
            scratchpad_dir=scratchpad,
        )
        result = bash.resolve_permission(BashArgs(command=f"mkdir {scratchpad}/subdir"))
        assert isinstance(result, PermissionContext)
        outside = [
            rp
            for rp in result.required_permissions
            if rp.scope is PermissionScope.OUTSIDE_DIRECTORY
        ]
        assert len(outside) == 0
