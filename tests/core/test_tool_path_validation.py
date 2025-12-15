"""Tests for tool path validation (is_path_within_workdir)."""

from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from vibe.core.config import VibeConfig
from vibe.core.tools.builtins.grep import Grep, GrepArgs, GrepState, GrepToolConfig
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
    SearchReplaceState,
)
from vibe.core.tools.builtins.write_file import (
    WriteFile,
    WriteFileArgs,
    WriteFileConfig,
    WriteFileState,
)


@pytest.fixture
def temp_workdir():
    """Create a temporary working directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workdir = Path(tmpdir)
        # Create some test files and directories
        (workdir / "file.txt").write_text("test")
        (workdir / "subdir").mkdir()
        (workdir / "subdir" / "nested.txt").write_text("nested")
        yield workdir


@pytest.fixture
def config_with_workdir(temp_workdir):
    """Create a VibeConfig with the temp workdir."""
    return VibeConfig(workdir=temp_workdir)


@pytest.fixture
def read_file_tool(config_with_workdir):
    """Create a ReadFile tool instance."""
    tool_config = ReadFileToolConfig()
    tool_config.workdir = config_with_workdir.effective_workdir
    return ReadFile(config=tool_config, state=ReadFileState())


@pytest.fixture
def write_file_tool(config_with_workdir):
    """Create a WriteFile tool instance."""
    tool_config = WriteFileConfig()
    tool_config.workdir = config_with_workdir.effective_workdir
    return WriteFile(config=tool_config, state=WriteFileState())


@pytest.fixture
def search_replace_tool(config_with_workdir):
    """Create a SearchReplace tool instance."""
    tool_config = SearchReplaceConfig()
    tool_config.workdir = config_with_workdir.effective_workdir
    return SearchReplace(config=tool_config, state=SearchReplaceState())


@pytest.fixture
def grep_tool(config_with_workdir):
    """Create a Grep tool instance."""
    tool_config = GrepToolConfig()
    tool_config.workdir = config_with_workdir.effective_workdir
    return Grep(config=tool_config, state=GrepState())


class TestReadFilePathValidation:
    """Test ReadFile.is_path_within_workdir()."""

    def test_absolute_path_within_workdir(self, read_file_tool, temp_workdir):
        """Test absolute path within workdir."""
        args = ReadFileArgs(path=str(temp_workdir / "file.txt"))
        assert read_file_tool.is_path_within_workdir(args) is True

    def test_relative_path_within_workdir(self, read_file_tool):
        """Test relative path within workdir."""
        args = ReadFileArgs(path="file.txt")
        assert read_file_tool.is_path_within_workdir(args) is True

    def test_nested_path_within_workdir(self, read_file_tool):
        """Test nested path within workdir."""
        args = ReadFileArgs(path="subdir/nested.txt")
        assert read_file_tool.is_path_within_workdir(args) is True

    def test_absolute_path_outside_workdir(self, read_file_tool):
        """Test absolute path outside workdir."""
        args = ReadFileArgs(path="/etc/passwd")
        assert read_file_tool.is_path_within_workdir(args) is False

    def test_relative_path_escaping_workdir(self, read_file_tool):
        """Test relative path trying to escape workdir."""
        args = ReadFileArgs(path="../../../etc/passwd")
        assert read_file_tool.is_path_within_workdir(args) is False

    def test_symlink_outside_workdir(self, read_file_tool, temp_workdir, tmp_path):
        """Test symlink pointing outside workdir."""
        # Create a file outside workdir
        external_file = tmp_path / "external.txt"
        external_file.write_text("external")

        # Create symlink inside workdir pointing outside
        symlink = temp_workdir / "link.txt"
        symlink.symlink_to(external_file)

        args = ReadFileArgs(path="link.txt")
        # Should return False because resolved path is outside workdir
        assert read_file_tool.is_path_within_workdir(args) is False

    def test_tilde_expansion(self, read_file_tool):
        """Test that tilde expansion works."""
        # ~/somefile should expand and be checked against workdir
        args = ReadFileArgs(path="~/somefile")
        # This will be outside workdir since ~ expands to home
        assert read_file_tool.is_path_within_workdir(args) is False


class TestWriteFilePathValidation:
    """Test WriteFile.is_path_within_workdir()."""

    def test_write_within_workdir(self, write_file_tool):
        """Test writing file within workdir."""
        args = WriteFileArgs(path="newfile.txt", content="test")
        assert write_file_tool.is_path_within_workdir(args) is True

    def test_write_outside_workdir(self, write_file_tool):
        """Test writing file outside workdir."""
        args = WriteFileArgs(path="/tmp/evil.txt", content="test")
        assert write_file_tool.is_path_within_workdir(args) is False

    def test_write_with_parent_traversal(self, write_file_tool):
        """Test writing with ../ traversal."""
        args = WriteFileArgs(path="../../outside.txt", content="test")
        assert write_file_tool.is_path_within_workdir(args) is False


class TestSearchReplacePathValidation:
    """Test SearchReplace.is_path_within_workdir()."""

    def test_edit_within_workdir(self, search_replace_tool):
        """Test editing file within workdir."""
        args = SearchReplaceArgs(
            file_path="file.txt",
            content="<<<<<<< SEARCH\ntest\n=======\nnew\n>>>>>>> REPLACE",
        )
        assert search_replace_tool.is_path_within_workdir(args) is True

    def test_edit_outside_workdir(self, search_replace_tool):
        """Test editing file outside workdir."""
        args = SearchReplaceArgs(
            file_path="/etc/passwd",
            content="<<<<<<< SEARCH\ntest\n=======\nnew\n>>>>>>> REPLACE",
        )
        assert search_replace_tool.is_path_within_workdir(args) is False


class TestGrepPathValidation:
    """Test Grep.is_path_within_workdir()."""

    def test_grep_within_workdir(self, grep_tool):
        """Test grepping within workdir."""
        args = GrepArgs(pattern="test", path=".")
        assert grep_tool.is_path_within_workdir(args) is True

    def test_grep_subdir_within_workdir(self, grep_tool):
        """Test grepping subdirectory within workdir."""
        args = GrepArgs(pattern="test", path="subdir")
        assert grep_tool.is_path_within_workdir(args) is True

    def test_grep_outside_workdir(self, grep_tool):
        """Test grepping outside workdir."""
        args = GrepArgs(pattern="test", path="/etc")
        assert grep_tool.is_path_within_workdir(args) is False

    def test_grep_with_traversal(self, grep_tool):
        """Test grepping with ../ traversal."""
        args = GrepArgs(pattern="test", path="../../../")
        assert grep_tool.is_path_within_workdir(args) is False


class TestBaseToolPathValidation:
    """Test BaseTool default behavior."""

    def test_base_tool_has_method(self, read_file_tool):
        """Test that BaseTool.is_path_within_workdir() method exists."""
        # The base class method should exist
        # For file tools it's overridden, but the method should always be present
        assert hasattr(read_file_tool, "is_path_within_workdir")

    def test_base_tool_has_get_file_paths(self, read_file_tool):
        """Test that BaseTool.get_file_paths() method exists."""
        assert hasattr(read_file_tool, "get_file_paths")


class TestGetFilePaths:
    """Test get_file_paths() implementations."""

    def test_read_file_get_paths(self, read_file_tool, temp_workdir):
        """Test ReadFile.get_file_paths() returns resolved path."""
        args = ReadFileArgs(path="file.txt")
        paths = read_file_tool.get_file_paths(args)
        assert len(paths) == 1
        assert paths[0] == (temp_workdir / "file.txt").resolve()

    def test_write_file_get_paths(self, write_file_tool, temp_workdir):
        """Test WriteFile.get_file_paths() returns resolved path."""
        args = WriteFileArgs(path="newfile.txt", content="test")
        paths = write_file_tool.get_file_paths(args)
        assert len(paths) == 1
        assert paths[0] == (temp_workdir / "newfile.txt").resolve()

    def test_search_replace_get_paths(self, search_replace_tool, temp_workdir):
        """Test SearchReplace.get_file_paths() returns resolved path."""
        args = SearchReplaceArgs(
            file_path="file.txt",
            content="<<<<<<< SEARCH\ntest\n=======\nnew\n>>>>>>> REPLACE",
        )
        paths = search_replace_tool.get_file_paths(args)
        assert len(paths) == 1
        assert paths[0] == (temp_workdir / "file.txt").resolve()

    def test_grep_get_paths(self, grep_tool, temp_workdir):
        """Test Grep.get_file_paths() returns resolved path."""
        args = GrepArgs(pattern="test", path="subdir")
        paths = grep_tool.get_file_paths(args)
        assert len(paths) == 1
        assert paths[0] == (temp_workdir / "subdir").resolve()

    def test_get_paths_with_absolute_path(self, read_file_tool, temp_workdir):
        """Test get_file_paths() with absolute path."""
        abs_path = temp_workdir / "file.txt"
        args = ReadFileArgs(path=str(abs_path))
        paths = read_file_tool.get_file_paths(args)
        assert len(paths) == 1
        assert paths[0] == abs_path.resolve()

    def test_get_paths_with_tilde(self, read_file_tool):
        """Test get_file_paths() expands tilde."""
        args = ReadFileArgs(path="~/somefile")
        paths = read_file_tool.get_file_paths(args)
        assert len(paths) == 1
        assert "~" not in str(paths[0])  # Tilde should be expanded
