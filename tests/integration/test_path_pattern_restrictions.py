"""Integration tests for path pattern restrictions in mode system."""

from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from vibe.core.agent import Agent
from vibe.core.config import VibeConfig
from vibe.core.modes import PathRestrictionConfig
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
    WriteFileState,
)


@pytest.fixture
def temp_workdir():
    """Create a temporary working directory with test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workdir = Path(tmpdir)
        # Create test files in different subdirectories
        (workdir / "docs").mkdir()
        (workdir / "docs" / "README.md").write_text("# Readme")
        (workdir / "docs" / "guide.md").write_text("# Guide")
        (workdir / "src").mkdir()
        (workdir / "src" / "main.py").write_text("print('hello')")
        (workdir / "src" / "util.py").write_text("def util(): pass")
        (workdir / "config.toml").write_text("[app]")
        (workdir / ".env").write_text("SECRET=value")
        (workdir / "secrets").mkdir()
        (workdir / "secrets" / "key.pem").write_text("-----BEGIN PRIVATE KEY-----")
        yield workdir


@pytest.fixture
def config_with_workdir(temp_workdir):
    """Create config with temp workdir."""
    return VibeConfig(workdir=temp_workdir)


@pytest.fixture
def agent_with_workdir(config_with_workdir):
    """Create agent with temp workdir."""
    return Agent(config=config_with_workdir)


@pytest.fixture
def read_file_tool(config_with_workdir):
    """Create ReadFile tool."""
    tool_config = ReadFileToolConfig()
    tool_config.workdir = config_with_workdir.effective_workdir
    return ReadFile(config=tool_config, state=ReadFileState())


@pytest.fixture
def write_file_tool(config_with_workdir):
    """Create WriteFile tool."""
    tool_config = WriteFileConfig()
    tool_config.workdir = config_with_workdir.effective_workdir
    return WriteFile(config=tool_config, state=WriteFileState())


class TestAllowedPatterns:
    """Test allowed_patterns path restrictions."""

    def test_allowed_pattern_allows_matching_file(
        self, agent_with_workdir, read_file_tool
    ):
        """Test that allowed pattern allows matching files."""
        # Create mode with only markdown files allowed
        agent_with_workdir._mode_config.path_restrictions = PathRestrictionConfig(
            restrict_to_workdir=True, allowed_patterns=["**/*.md"], denied_patterns=[]
        )

        args = ReadFileArgs(path="docs/README.md")
        result = agent_with_workdir._validate_path_restrictions(read_file_tool, args)
        assert result is True

    def test_allowed_pattern_blocks_non_matching_file(
        self, agent_with_workdir, read_file_tool
    ):
        """Test that allowed pattern blocks non-matching files."""
        agent_with_workdir._mode_config.path_restrictions = PathRestrictionConfig(
            restrict_to_workdir=True, allowed_patterns=["**/*.md"], denied_patterns=[]
        )

        args = ReadFileArgs(path="src/main.py")
        result = agent_with_workdir._validate_path_restrictions(read_file_tool, args)
        assert result is False

    def test_multiple_allowed_patterns(self, agent_with_workdir, read_file_tool):
        """Test multiple allowed patterns (OR logic)."""
        agent_with_workdir._mode_config.path_restrictions = PathRestrictionConfig(
            restrict_to_workdir=True,
            allowed_patterns=["**/*.md", "**/*.py"],
            denied_patterns=[],
        )

        # Both .md and .py should be allowed
        args_md = ReadFileArgs(path="docs/README.md")
        assert (
            agent_with_workdir._validate_path_restrictions(read_file_tool, args_md)
            is True
        )

        args_py = ReadFileArgs(path="src/main.py")
        assert (
            agent_with_workdir._validate_path_restrictions(read_file_tool, args_py)
            is True
        )

        # .toml should be blocked
        args_toml = ReadFileArgs(path="config.toml")
        assert (
            agent_with_workdir._validate_path_restrictions(read_file_tool, args_toml)
            is False
        )

    def test_directory_pattern(self, agent_with_workdir, read_file_tool):
        """Test pattern matching specific directory."""
        agent_with_workdir._mode_config.path_restrictions = PathRestrictionConfig(
            restrict_to_workdir=True, allowed_patterns=["docs/**"], denied_patterns=[]
        )

        # Files in docs/ should be allowed
        args_docs = ReadFileArgs(path="docs/README.md")
        assert (
            agent_with_workdir._validate_path_restrictions(read_file_tool, args_docs)
            is True
        )

        # Files outside docs/ should be blocked
        args_src = ReadFileArgs(path="src/main.py")
        assert (
            agent_with_workdir._validate_path_restrictions(read_file_tool, args_src)
            is False
        )

    def test_default_all_pattern_allows_everything(
        self, agent_with_workdir, read_file_tool
    ):
        """Test that default ['**/*'] pattern allows all files."""
        agent_with_workdir._mode_config.path_restrictions = PathRestrictionConfig(
            restrict_to_workdir=True,
            allowed_patterns=["**/*"],  # Default
            denied_patterns=[],
        )

        # All files should be allowed
        args1 = ReadFileArgs(path="docs/README.md")
        assert (
            agent_with_workdir._validate_path_restrictions(read_file_tool, args1)
            is True
        )

        args2 = ReadFileArgs(path="src/main.py")
        assert (
            agent_with_workdir._validate_path_restrictions(read_file_tool, args2)
            is True
        )


class TestDeniedPatterns:
    """Test denied_patterns path restrictions."""

    def test_denied_pattern_blocks_matching_file(
        self, agent_with_workdir, read_file_tool
    ):
        """Test that denied pattern blocks matching files."""
        agent_with_workdir._mode_config.path_restrictions = PathRestrictionConfig(
            restrict_to_workdir=True,
            allowed_patterns=["**/*"],
            denied_patterns=["**/.env"],
        )

        args = ReadFileArgs(path=".env")
        result = agent_with_workdir._validate_path_restrictions(read_file_tool, args)
        assert result is False

    def test_denied_pattern_allows_non_matching_file(
        self, agent_with_workdir, read_file_tool
    ):
        """Test that denied pattern allows non-matching files."""
        agent_with_workdir._mode_config.path_restrictions = PathRestrictionConfig(
            restrict_to_workdir=True,
            allowed_patterns=["**/*"],
            denied_patterns=["**/.env"],
        )

        args = ReadFileArgs(path="src/main.py")
        result = agent_with_workdir._validate_path_restrictions(read_file_tool, args)
        assert result is True

    def test_multiple_denied_patterns(self, agent_with_workdir, read_file_tool):
        """Test multiple denied patterns."""
        agent_with_workdir._mode_config.path_restrictions = PathRestrictionConfig(
            restrict_to_workdir=True,
            allowed_patterns=["**/*"],
            denied_patterns=["**/.env", "**/*.pem", "secrets/**"],
        )

        # .env should be blocked
        args_env = ReadFileArgs(path=".env")
        assert (
            agent_with_workdir._validate_path_restrictions(read_file_tool, args_env)
            is False
        )

        # .pem files should be blocked
        args_pem = ReadFileArgs(path="secrets/key.pem")
        assert (
            agent_with_workdir._validate_path_restrictions(read_file_tool, args_pem)
            is False
        )

        # Regular files should be allowed
        args_ok = ReadFileArgs(path="src/main.py")
        assert (
            agent_with_workdir._validate_path_restrictions(read_file_tool, args_ok)
            is True
        )


class TestCombinedPatterns:
    """Test combined allowed and denied patterns."""

    def test_allowed_and_denied_together(self, agent_with_workdir, read_file_tool):
        """Test that denied patterns override allowed patterns."""
        agent_with_workdir._mode_config.path_restrictions = PathRestrictionConfig(
            restrict_to_workdir=True,
            allowed_patterns=["**/*.py"],
            denied_patterns=["**/test_*.py"],
        )

        # Regular .py files should be allowed
        args_ok = ReadFileArgs(path="src/main.py")
        assert (
            agent_with_workdir._validate_path_restrictions(read_file_tool, args_ok)
            is True
        )

        # test_*.py files should be blocked even though *.py is allowed
        args_test = ReadFileArgs(path="src/test_util.py")
        assert (
            agent_with_workdir._validate_path_restrictions(read_file_tool, args_test)
            is False
        )

    def test_docs_only_mode(self, agent_with_workdir, write_file_tool):
        """Test realistic 'docs-only' mode configuration."""
        agent_with_workdir._mode_config.path_restrictions = PathRestrictionConfig(
            restrict_to_workdir=True,
            allowed_patterns=["**/*.md", "docs/**"],
            denied_patterns=[],
        )

        # Markdown files should be allowed
        args_md = WriteFileArgs(path="docs/new.md", content="# New")
        assert (
            agent_with_workdir._validate_path_restrictions(write_file_tool, args_md)
            is True
        )

        # Python files should be blocked
        args_py = WriteFileArgs(path="src/new.py", content="pass")
        assert (
            agent_with_workdir._validate_path_restrictions(write_file_tool, args_py)
            is False
        )

    def test_safe_scripting_mode(self, agent_with_workdir, write_file_tool):
        """Test realistic 'safe-scripting' mode that blocks secrets."""
        agent_with_workdir._mode_config.path_restrictions = PathRestrictionConfig(
            restrict_to_workdir=True,
            allowed_patterns=["**/*"],
            denied_patterns=["**/.env", "**/secrets/**", "**/*.key", "**/*.pem"],
        )

        # Regular files should be allowed
        args_ok = WriteFileArgs(path="src/app.py", content="pass")
        assert (
            agent_with_workdir._validate_path_restrictions(write_file_tool, args_ok)
            is True
        )

        # Secret files should be blocked
        args_env = WriteFileArgs(path=".env", content="SECRET=x")
        assert (
            agent_with_workdir._validate_path_restrictions(write_file_tool, args_env)
            is False
        )

        args_key = WriteFileArgs(path="id_rsa.key", content="key")
        assert (
            agent_with_workdir._validate_path_restrictions(write_file_tool, args_key)
            is False
        )


class TestEdgeCases:
    """Test edge cases and corner scenarios."""

    def test_no_restrictions(self, agent_with_workdir, read_file_tool):
        """Test that None restrictions allows everything."""
        agent_with_workdir._mode_config.path_restrictions = None

        args = ReadFileArgs(path="any/file.txt")
        result = agent_with_workdir._validate_path_restrictions(read_file_tool, args)
        assert result is True

    def test_empty_denied_patterns(self, agent_with_workdir, read_file_tool):
        """Test that empty denied patterns allows everything."""
        agent_with_workdir._mode_config.path_restrictions = PathRestrictionConfig(
            restrict_to_workdir=True, allowed_patterns=["**/*"], denied_patterns=[]
        )

        args = ReadFileArgs(path="any/file.txt")
        result = agent_with_workdir._validate_path_restrictions(read_file_tool, args)
        assert result is True

    def test_workdir_and_patterns_both_enforced(
        self, agent_with_workdir, read_file_tool
    ):
        """Test that both workdir restriction and patterns are enforced."""
        agent_with_workdir._mode_config.path_restrictions = PathRestrictionConfig(
            restrict_to_workdir=True, allowed_patterns=["**/*.md"], denied_patterns=[]
        )

        # File outside workdir should be blocked even if pattern matches
        args_outside = ReadFileArgs(path="/tmp/README.md")
        result = agent_with_workdir._validate_path_restrictions(
            read_file_tool, args_outside
        )
        assert result is False

    def test_empty_allowed_patterns_blocks_everything(
        self, agent_with_workdir, read_file_tool
    ):
        """Test that empty allowed_patterns list blocks all files."""
        agent_with_workdir._mode_config.path_restrictions = PathRestrictionConfig(
            restrict_to_workdir=True,
            allowed_patterns=[],  # Empty list should block everything
            denied_patterns=[],
        )

        # All files should be blocked
        args = ReadFileArgs(path="docs/README.md")
        result = agent_with_workdir._validate_path_restrictions(read_file_tool, args)
        assert result is False
