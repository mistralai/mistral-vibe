"""Tests for the RepoMap functionality."""

from __future__ import annotations

import os
import tempfile
from collections import defaultdict
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vibe.core.middleware import (
    ConversationContext,
    MiddlewareResult,
    RepoMapMiddleware,
)
from vibe.core.types import AgentStats, LLMMessage
from vibe.repomap import RepoMap
from vibe.repomap.rendering import render_repo_map, render_repo_map_markdown, to_tree
from vibe.repomap.tags import Tag, TagExtractor


class TestTagExtractor:
    """Tests for the TagExtractor class."""

    def test_extracts_python_class_definitions(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.py"
        test_file.write_text("class MyClass:\n    pass\n")

        extractor = TagExtractor()
        tags, error = extractor.get_tags(str(test_file), "test.py")

        assert error is None
        def_tags = [t for t in tags if t.kind == "def"]
        assert len(def_tags) >= 1
        assert any(t.name == "MyClass" for t in def_tags)

    def test_extracts_python_function_definitions(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.py"
        test_file.write_text("def my_function():\n    return 42\n")

        extractor = TagExtractor()
        tags, error = extractor.get_tags(str(test_file), "test.py")

        assert error is None
        def_tags = [t for t in tags if t.kind == "def"]
        assert len(def_tags) >= 1
        assert any(t.name == "my_function" for t in def_tags)

    def test_extracts_references(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.py"
        test_file.write_text("class A:\n    pass\n\nx = A()\n")

        extractor = TagExtractor()
        tags, error = extractor.get_tags(str(test_file), "test.py")

        assert error is None
        ref_tags = [t for t in tags if t.kind == "ref"]
        assert any(t.name == "A" for t in ref_tags)

    def test_returns_empty_for_nonexistent_file(self) -> None:
        extractor = TagExtractor()
        tags, error = extractor.get_tags("/nonexistent/file.py", "file.py")
        assert tags == []
        assert error is not None
        assert error.error_type == "FileNotFound"

    def test_caches_results(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()

        test_file = tmp_path / "test.py"
        test_file.write_text("def cached_func():\n    pass\n")

        extractor = TagExtractor(cache_dir=str(cache_dir))

        # First call - populates cache
        tags1, error1 = extractor.get_tags(str(test_file), "test.py")

        # Second call - should use cache
        tags2, error2 = extractor.get_tags(str(test_file), "test.py")

        assert error1 is None
        assert error2 is None
        assert len(tags1) == len(tags2)


class TestToTree:
    """Tests for the to_tree rendering function."""

    def test_renders_single_file(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.py"
        test_file.write_text("class SingleClass:\n    pass\n")

        tag = Tag("test.py", str(test_file), 0, "SingleClass", "def")
        definitions = {(str(test_file), "SingleClass"): {tag}}
        ranked_tags = [((str(test_file), "SingleClass"), 1.0)]

        result = to_tree(ranked_tags, definitions, set())

        assert "SingleClass" in result

    def test_renders_multiple_files(self, tmp_path: Path) -> None:
        file1 = tmp_path / "file1.py"
        file2 = tmp_path / "file2.py"
        file1.write_text("class Class1:\n    pass\n")
        file2.write_text("class Class2:\n    pass\n")

        tag1 = Tag("file1.py", str(file1), 0, "Class1", "def")
        tag2 = Tag("file2.py", str(file2), 0, "Class2", "def")

        definitions = {
            (str(file1), "Class1"): {tag1},
            (str(file2), "Class2"): {tag2},
        }
        ranked_tags = [
            ((str(file1), "Class1"), 1.0),
            ((str(file2), "Class2"), 0.5),
        ]

        result = to_tree(ranked_tags, definitions, set())

        # Both classes should be present (this was the bug that was fixed)
        assert "Class1" in result, "Class1 should be in output"
        assert "Class2" in result, "Class2 should be in output"

    def test_excludes_chat_files(self, tmp_path: Path) -> None:
        test_file = tmp_path / "chat_file.py"
        test_file.write_text("class ChatClass:\n    pass\n")

        tag = Tag("chat_file.py", str(test_file), 0, "ChatClass", "def")
        definitions = {(str(test_file), "ChatClass"): {tag}}
        ranked_tags = [((str(test_file), "ChatClass"), 1.0)]

        result = to_tree(ranked_tags, definitions, {str(test_file)})

        assert result == ""

    def test_empty_input_returns_empty(self) -> None:
        result = to_tree([], {}, set())
        assert result == ""


class TestRenderRepoMap:
    """Tests for the render_repo_map function."""

    def test_respects_token_limit(self, tmp_path: Path) -> None:
        # Create a file with many symbols
        code = "\n".join([f"def func{i}():\n    pass\n" for i in range(50)])
        test_file = tmp_path / "many_funcs.py"
        test_file.write_text(code)

        extractor = TagExtractor()
        tags, error = extractor.get_tags(str(test_file), "many_funcs.py")

        assert error is None
        definitions: dict = defaultdict(set)
        for tag in tags:
            if tag.kind == "def":
                definitions[(tag.fname, tag.name)].add(tag)

        ranked_tags = [
            ((fname, name), 1.0 / (i + 1))
            for i, ((fname, name), _) in enumerate(definitions.items())
        ]

        result = render_repo_map(ranked_tags, definitions, 100, set())
        # Token count is approximate (chars / 4)
        assert len(result) <= 500  # Generous margin

    def test_returns_empty_for_no_tags(self) -> None:
        result = render_repo_map([], {}, 1024, set())
        assert result == ""

    def test_renders_markdown_output(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.py"
        test_file.write_text("class HtmlClass:\n    pass\n")

        tag = Tag("test.py", str(test_file), 0, "HtmlClass", "def")
        definitions = {(str(test_file), "HtmlClass"): {tag}}
        ranked_tags = [((str(test_file), "HtmlClass"), 1.0)]

        result = render_repo_map_markdown(ranked_tags, definitions, 1024, set())

        assert result
        assert result.lstrip().startswith("# RepoMap")
        assert "test.py" in result


class TestRepoMap:
    """Tests for the main RepoMap class."""

    def test_generates_repo_map(self, tmp_path: Path) -> None:
        # Create some test files
        file1 = tmp_path / "module1.py"
        file2 = tmp_path / "module2.py"
        file1.write_text("class Module1Class:\n    pass\n")
        file2.write_text("def module2_function():\n    return 42\n")

        rm = RepoMap(root=str(tmp_path), map_tokens=1024)
        result = rm.get_repo_map(
            chat_files=[],
            other_files=[str(file1), str(file2)],
            mentioned_fnames=set(),
            mentioned_idents=set(),
        )

        assert result  # Should produce some output
        assert "Module1Class" in result or "module2_function" in result

    def test_handles_empty_other_files(self) -> None:
        rm = RepoMap(root=".", map_tokens=1024)
        result = rm.get_repo_map(
            chat_files=[],
            other_files=[],
            mentioned_fnames=set(),
            mentioned_idents=set(),
        )

        assert result == ""

    def test_boosts_mentioned_idents(self, tmp_path: Path) -> None:
        file1 = tmp_path / "important.py"
        file1.write_text("def important_function():\n    return 'important'\n")

        rm = RepoMap(root=str(tmp_path), map_tokens=1024)
        result = rm.get_repo_map(
            chat_files=[],
            other_files=[str(file1)],
            mentioned_fnames=set(),
            mentioned_idents={"important_function"},
        )

        assert "important_function" in result


class TestRepoMapMiddleware:
    """Tests for the RepoMapMiddleware."""

    def _make_mock_config(self, *, enabled: bool = True) -> MagicMock:
        config = MagicMock()
        config.repo_map.enabled = enabled
        config.repo_map.exclude_patterns = [
            "node_modules",
            "__pycache__",
            ".git",
        ]
        return config

    @pytest.mark.asyncio
    async def test_returns_middleware_result_when_enabled(self) -> None:
        config = self._make_mock_config()
        middleware = RepoMapMiddleware(lambda: config)

        stats = AgentStats()
        messages = [LLMMessage(role="user", content="Hello")]
        context = ConversationContext(messages=messages, stats=stats, config=config)

        result = await middleware.before_turn(context)

        assert isinstance(result, MiddlewareResult)

    @pytest.mark.asyncio
    async def test_returns_continue_when_disabled(self) -> None:
        config = self._make_mock_config(enabled=False)
        middleware = RepoMapMiddleware(lambda: config)

        stats = AgentStats()
        messages = [LLMMessage(role="user", content="Hello")]
        context = ConversationContext(messages=messages, stats=stats, config=config)

        result = await middleware.before_turn(context)

        assert isinstance(result, MiddlewareResult)
        assert result.action.value == "continue"

    @pytest.mark.asyncio
    async def test_after_turn_returns_continue(self) -> None:
        config = self._make_mock_config()
        middleware = RepoMapMiddleware(lambda: config)

        stats = AgentStats()
        messages = []
        context = ConversationContext(messages=messages, stats=stats, config=config)

        result = await middleware.after_turn(context)

        assert isinstance(result, MiddlewareResult)
        assert result.action.value == "continue"


class TestQueryFiles:
    """Tests for tree-sitter query files."""

    def test_python_query_exists(self) -> None:
        query_path = Path(__file__).parent.parent / "vibe" / "repomap" / "queries" / "python.scm"
        assert query_path.exists(), "Python query file should exist"

    def test_javascript_query_exists(self) -> None:
        query_path = Path(__file__).parent.parent / "vibe" / "repomap" / "queries" / "javascript.scm"
        assert query_path.exists(), "JavaScript query file should exist"

    def test_typescript_query_exists(self) -> None:
        query_path = Path(__file__).parent.parent / "vibe" / "repomap" / "queries" / "typescript.scm"
        assert query_path.exists(), "TypeScript query file should exist"

    def test_go_query_exists(self) -> None:
        query_path = Path(__file__).parent.parent / "vibe" / "repomap" / "queries" / "go.scm"
        assert query_path.exists(), "Go query file should exist"


class TestDiscovery:
    """Tests for the file discovery module."""

    def test_discovers_python_files(self, tmp_path: Path) -> None:
        from vibe.repomap.discovery import discover_files

        # Create test files
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "utils.py").write_text("def util(): pass")
        (tmp_path / "readme.txt").write_text("readme")

        result = discover_files(tmp_path)

        assert len(result.files) == 2
        assert any("main.py" in f for f in result.files)
        assert any("utils.py" in f for f in result.files)
        assert result.skipped_by_extension >= 1

    def test_respects_gitignore(self, tmp_path: Path) -> None:
        from vibe.repomap.discovery import discover_files

        # Create gitignore
        (tmp_path / ".gitignore").write_text("ignored/\n*.generated.py\n")

        # Create files
        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "ignored").mkdir()
        (tmp_path / "ignored" / "secret.py").write_text("secret")
        (tmp_path / "file.generated.py").write_text("generated")

        result = discover_files(tmp_path, respect_gitignore=True)

        assert len(result.files) == 1
        assert any("main.py" in f for f in result.files)
        assert result.skipped_by_gitignore >= 1

    def test_excludes_default_directories(self, tmp_path: Path) -> None:
        from vibe.repomap.discovery import discover_files

        # Create default excluded dirs
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "dep.js").write_text("module")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "cache.py").write_text("cache")
        (tmp_path / "main.py").write_text("print('hello')")

        result = discover_files(tmp_path)

        assert len(result.files) == 1
        assert any("main.py" in f for f in result.files)
        assert result.skipped_by_default >= 1

    def test_additional_excludes(self, tmp_path: Path) -> None:
        from vibe.repomap.discovery import discover_files

        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "custom_exclude").mkdir()
        (tmp_path / "custom_exclude" / "file.py").write_text("excluded")

        result = discover_files(tmp_path, additional_excludes=["custom_exclude"])

        assert len(result.files) == 1
        assert any("main.py" in f for f in result.files)

    def test_skips_symlinks_by_default(self, tmp_path: Path) -> None:
        from vibe.repomap.discovery import discover_files

        (tmp_path / "main.py").write_text("print('hello')")
        (tmp_path / "target.py").write_text("target_content")
        # Create a symlink
        (tmp_path / "link.py").symlink_to(tmp_path / "target.py")

        result = discover_files(tmp_path, follow_symlinks=False)

        # Should find main.py and target.py, but skip the symlink
        assert len(result.files) == 2
        assert result.skipped_symlinks == 1

    def test_skips_binary_files(self, tmp_path: Path) -> None:
        from vibe.repomap.discovery import discover_files

        (tmp_path / "main.py").write_text("print('hello')")
        # Write a binary file with null bytes
        (tmp_path / "binary.py").write_bytes(b"binary\x00content")

        result = discover_files(tmp_path, skip_binary=True)

        assert len(result.files) == 1
        assert any("main.py" in f for f in result.files)
        assert result.skipped_binary == 1


class TestRepoMapResult:
    """Tests for RepoMapResult diagnostics."""

    def test_get_repo_map_with_diagnostics(self, tmp_path: Path) -> None:
        file1 = tmp_path / "module.py"
        file1.write_text("class TestClass:\n    pass\n")

        rm = RepoMap(root=str(tmp_path), map_tokens=1024)
        result = rm.get_repo_map_with_diagnostics(
            chat_files=[],
            other_files=[str(file1)],
            mentioned_fnames=set(),
            mentioned_idents=set(),
        )

        assert result.files_processed == 1
        assert result.files_skipped == 0
        assert len(result.errors) == 0
        assert result.status_string() == "Active"

    def test_status_string_with_errors(self, tmp_path: Path) -> None:
        from vibe.repomap.core import RepoMapResult
        from vibe.repomap.tags import ExtractionError

        result = RepoMapResult(
            content="some content",
            files_processed=5,
            errors=[ExtractionError("test.py", "ParseError", "test")],
        )

        assert "1 errors" in result.status_string()

    def test_status_string_empty(self) -> None:
        from vibe.repomap.core import RepoMapResult

        result = RepoMapResult(content="")
        assert result.status_string() == "Empty"
