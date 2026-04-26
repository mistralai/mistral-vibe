"""Tests for vibe/core/plugins/builtin/lsp/registry.py"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from vibe.core.plugins.builtin.lsp.registry import (
    LspConfig,
    LSP_REGISTRY,
    detect_languages_in_dir,
    language_for_extension,
    language_for_path,
)


class TestLspConfig:
    """Tests for LspConfig dataclass."""

    def test_is_available_returns_true_when_command_exists(self):
        cfg = LspConfig(
            language="test",
            extensions=frozenset({".test"}),
            command=["python"],
            language_id="test",
        )
        assert cfg.is_available() is True

    def test_is_available_returns_false_when_command_not_found(self):
        cfg = LspConfig(
            language="nonexistent",
            extensions=frozenset({".nonexistent"}),
            command=["this-command-does-not-exist-12345"],
            language_id="nonexistent",
        )
        assert cfg.is_available() is False


class TestLanguageForExtension:
    """Tests for language_for_extension function."""

    @pytest.mark.parametrize(
        "ext,expected",
        [
            (".py", "python"),
            (".pyi", "python"),
            (".ts", "typescript"),
            (".tsx", "typescript"),
            (".js", "typescript"),
            (".jsx", "typescript"),
            (".java", "java"),
        ],
    )
    def test_language_for_extension_returns_correct_language(self, ext, expected):
        result = language_for_extension(ext)
        assert result == expected

    def test_language_for_extension_case_insensitive(self):
        assert language_for_extension(".PY") == "python"
        assert language_for_extension(".Py") == "python"
        assert language_for_extension(".TS") == "typescript"

    def test_language_for_extension_returns_none_for_unknown(self):
        assert language_for_extension(".unknown") is None
        assert language_for_extension("") is None


class TestLanguageForPath:
    """Tests for language_for_path function."""

    def test_language_for_path_returns_language_from_suffix(self):
        assert language_for_path("/path/to/file.py") == "python"
        assert language_for_path("/path/to/file.ts") == "typescript"
        assert language_for_path("/path/to/file.java") == "java"

    def test_language_for_path_returns_none_for_unknown_extension(self):
        assert language_for_path("/path/to/file.txt") is None


class TestDetectLanguagesInDir:
    """Tests for detect_languages_in_dir function."""

    def test_detects_python_in_pyproject_dir(self, tmp_path):
        (tmp_path / "main.py").touch()
        result = detect_languages_in_dir(str(tmp_path))
        assert "python" in result

    def test_detects_python_in_requirements_dir(self, tmp_path):
        (tmp_path / "main.py").touch()
        result = detect_languages_in_dir(str(tmp_path))
        assert "python" in result

    def test_detects_typescript_in_package_json_dir(self, tmp_path):
        (tmp_path / "main.ts").touch()
        result = detect_languages_in_dir(str(tmp_path))
        assert "typescript" in result

    def test_detects_multiple_languages(self, tmp_path):
        (tmp_path / "main.py").touch()
        (tmp_path / "main.ts").touch()
        result = detect_languages_in_dir(str(tmp_path))
        assert "python" in result
        assert "typescript" in result

    def test_ignores_git_directory(self, tmp_path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").touch()
        result = detect_languages_in_dir(str(tmp_path))
        assert "git" not in result

    def test_ignores_node_modules(self, tmp_path):
        node_modules = tmp_path / "node_modules"
        node_modules.mkdir()
        (node_modules / "package.json").touch()
        result = detect_languages_in_dir(str(tmp_path))
        assert "typescript" not in result

    def test_ignores_pycache(self, tmp_path):
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "test.pyc").touch()
        result = detect_languages_in_dir(str(tmp_path))
        assert len(result) == 0

    def test_respects_max_files_limit(self, tmp_path):
        for i in range(1500):
            (tmp_path / f"file{i}.py").touch()
        result = detect_languages_in_dir(str(tmp_path), max_files=1000)
        assert "python" in result

    def test_returns_empty_set_for_empty_directory(self, tmp_path):
        result = detect_languages_in_dir(str(tmp_path))
        assert result == set()

    def test_detects_java_in_pom_xml_dir(self, tmp_path):
        (tmp_path / "pom.xml").touch()
        result = detect_languages_in_dir(str(tmp_path))
        assert "java" in result

    def test_detects_java_in_build_gradle_dir(self, tmp_path):
        (tmp_path / "build.gradle").touch()
        result = detect_languages_in_dir(str(tmp_path))
        assert "java" in result


class TestLspRegistry:
    """Tests for LSP_REGISTRY."""

    def test_python_config_exists(self):
        assert "python" in LSP_REGISTRY
        cfg = LSP_REGISTRY["python"]
        assert cfg.language == "python"
        assert ".py" in cfg.extensions

    def test_typescript_config_exists(self):
        assert "typescript" in LSP_REGISTRY
        cfg = LSP_REGISTRY["typescript"]
        assert cfg.language == "typescript"
        assert ".ts" in cfg.extensions

    def test_java_config_exists(self):
        assert "java" in LSP_REGISTRY
        cfg = LSP_REGISTRY["java"]
        assert cfg.language == "java"
        assert ".java" in cfg.extensions