"""Hierarchical directory analyzer with rollup statistics.

Analyzes directory structure and computes:
- File counts and LOC per directory
- Language breakdown
- Test/generated file detection
- Recursive rollup statistics
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from .schemas import (
    DirectoryRollup,
    FileMetrics,
    FileType,
    Language,
    detect_primary_language,
)


# =============================================================================
# Language Detection
# =============================================================================

_EXTENSION_TO_LANGUAGE: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".pyi": Language.PYTHON,
    ".pyw": Language.PYTHON,
    ".js": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".mts": Language.TYPESCRIPT,
    ".cts": Language.TYPESCRIPT,
    ".go": Language.GO,
    ".rs": Language.RUST,
    ".java": Language.JAVA,
    ".cpp": Language.CPP,
    ".cc": Language.CPP,
    ".cxx": Language.CPP,
    ".hpp": Language.CPP,
    ".h": Language.C,
    ".c": Language.C,
    ".rb": Language.RUBY,
    ".sh": Language.SHELL,
    ".bash": Language.SHELL,
    ".zsh": Language.SHELL,
    ".yaml": Language.YAML,
    ".yml": Language.YAML,
    ".json": Language.JSON,
    ".md": Language.MARKDOWN,
    ".markdown": Language.MARKDOWN,
}

# =============================================================================
# File Type Detection
# =============================================================================

_TEST_PATH_PATTERNS = frozenset({
    "test",
    "tests",
    "__tests__",
    "spec",
    "specs",
    "test_",
    "_test",
    ".test",
    ".spec",
})

_GENERATED_PATTERNS = frozenset({
    "_generated",
    ".generated",
    "_pb2",
    ".pb",
    ".min.",
    ".bundle.",
    "vendor",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
})

_CONFIG_EXTENSIONS = frozenset({
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".config",
    ".env",
})

_DOC_EXTENSIONS = frozenset({
    ".md",
    ".rst",
    ".txt",
    ".adoc",
})


def detect_language(path: Path) -> Language:
    """Detect programming language from file extension."""
    suffix = path.suffix.lower()
    return _EXTENSION_TO_LANGUAGE.get(suffix, Language.OTHER)


def detect_file_type(path: Path) -> FileType:
    """Detect file type (source, test, generated, config, doc)."""
    path_str = str(path).lower()
    stem = path.stem.lower()
    suffix = path.suffix.lower()

    # Check for generated files
    if any(pattern in path_str for pattern in _GENERATED_PATTERNS):
        return FileType.GENERATED

    # Check for test files
    if any(pattern in path_str for pattern in _TEST_PATH_PATTERNS):
        return FileType.TEST
    if stem.startswith("test_") or stem.endswith("_test") or stem.endswith("_spec"):
        return FileType.TEST

    # Check for config files
    if suffix in _CONFIG_EXTENSIONS:
        return FileType.CONFIG

    # Check for documentation
    if suffix in _DOC_EXTENSIONS:
        return FileType.DOC

    # Default to source
    return FileType.SOURCE


def is_test_directory(path: Path) -> bool:
    """Check if a directory is a test directory."""
    name = path.name.lower()
    return name in _TEST_PATH_PATTERNS or name.startswith("test")


def is_generated_directory(path: Path) -> bool:
    """Check if a directory contains generated content."""
    name = path.name.lower()
    return name in {"node_modules", "vendor", "dist", "build", "__pycache__", ".git"}


# =============================================================================
# Line Counting
# =============================================================================

# Comment patterns by language
_SINGLE_LINE_COMMENTS: dict[Language, list[str]] = {
    Language.PYTHON: ["#"],
    Language.JAVASCRIPT: ["//"],
    Language.TYPESCRIPT: ["//"],
    Language.GO: ["//"],
    Language.RUST: ["//"],
    Language.JAVA: ["//"],
    Language.CPP: ["//"],
    Language.C: ["//"],
    Language.RUBY: ["#"],
    Language.SHELL: ["#"],
    Language.YAML: ["#"],
}

_MULTI_LINE_COMMENTS: dict[Language, list[tuple[str, str]]] = {
    Language.PYTHON: [('"""', '"""'), ("'''", "'''")],
    Language.JAVASCRIPT: [("/*", "*/")],
    Language.TYPESCRIPT: [("/*", "*/")],
    Language.GO: [("/*", "*/")],
    Language.RUST: [("/*", "*/")],
    Language.JAVA: [("/*", "*/")],
    Language.CPP: [("/*", "*/")],
    Language.C: [("/*", "*/")],
}


def count_lines(content: str, language: Language) -> tuple[int, int, int]:
    """Count lines of code, blank lines, and comment lines.

    Returns:
        Tuple of (code_lines, blank_lines, comment_lines)
    """
    lines = content.splitlines()
    code_lines = 0
    blank_lines = 0
    comment_lines = 0

    single_comment_prefixes = _SINGLE_LINE_COMMENTS.get(language, [])
    in_multiline = False

    for line in lines:
        stripped = line.strip()

        if not stripped:
            blank_lines += 1
            continue

        # Check if we're in a multiline comment
        if in_multiline:
            comment_lines += 1
            # Check for end of multiline comment
            for _, end in _MULTI_LINE_COMMENTS.get(language, []):
                if end in stripped:
                    in_multiline = False
                    break
            continue

        # Check for start of multiline comment
        for start, end in _MULTI_LINE_COMMENTS.get(language, []):
            if stripped.startswith(start):
                comment_lines += 1
                if end not in stripped[len(start) :]:
                    in_multiline = True
                break
        else:
            # Check for single-line comment
            is_comment = any(
                stripped.startswith(prefix) for prefix in single_comment_prefixes
            )
            if is_comment:
                comment_lines += 1
            else:
                code_lines += 1

    return code_lines, blank_lines, comment_lines


# =============================================================================
# File Metrics
# =============================================================================


def analyze_file(path: Path, root: Path) -> FileMetrics | None:
    """Analyze a single file and return metrics."""
    if not path.is_file():
        return None

    try:
        stat = path.stat()
        rel_path = str(path.relative_to(root))
    except (OSError, ValueError):
        return None

    language = detect_language(path)
    file_type = detect_file_type(path)

    # Read file for line counting (only for source code)
    code_lines = 0
    blank_lines = 0
    comment_lines = 0

    if language != Language.OTHER and file_type in (FileType.SOURCE, FileType.TEST):
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            code_lines, blank_lines, comment_lines = count_lines(content, language)
        except (OSError, UnicodeDecodeError):
            pass

    # Compute content hash
    try:
        content_bytes = path.read_bytes()
        import hashlib

        content_hash = hashlib.sha256(content_bytes).hexdigest()[:16]
    except OSError:
        content_hash = ""

    return FileMetrics(
        path=rel_path,
        language=language,
        file_type=file_type,
        lines_of_code=code_lines,
        blank_lines=blank_lines,
        comment_lines=comment_lines,
        size_bytes=stat.st_size,
        hash=content_hash,
    )


# =============================================================================
# Directory Analyzer
# =============================================================================


class DirectoryAnalyzer:
    """Analyzes directory structure with rollup statistics."""

    def __init__(
        self,
        root: Path,
        exclude_patterns: set[str] | None = None,
        include_hidden: bool = False,
        max_depth: int = -1,
    ):
        self.root = root.resolve()
        self.exclude_patterns = exclude_patterns or {
            ".git",
            ".hg",
            ".svn",
            "node_modules",
            "__pycache__",
            ".tox",
            ".nox",
            "venv",
            ".venv",
            "env",
            ".env",
            "site-packages",
            "dist",
            "build",
            ".idea",
            ".vscode",
        }
        self.include_hidden = include_hidden
        self.max_depth = max_depth

    def analyze(self) -> DirectoryRollup:
        """Analyze the root directory and build rollup tree."""
        return self._analyze_directory(self.root, depth=0)

    def _should_exclude(self, path: Path) -> bool:
        """Check if a path should be excluded."""
        name = path.name

        # Exclude hidden files/directories unless requested
        if not self.include_hidden and name.startswith("."):
            return True

        # Check exclude patterns
        if name in self.exclude_patterns:
            return True

        return False

    def _analyze_directory(self, dir_path: Path, depth: int) -> DirectoryRollup:
        """Recursively analyze a directory."""
        rel_path = (
            str(dir_path.relative_to(self.root)) if dir_path != self.root else "."
        )

        rollup = DirectoryRollup(
            path=rel_path,
            name=dir_path.name or self.root.name,
            is_test_directory=is_test_directory(dir_path),
            is_generated=is_generated_directory(dir_path),
        )

        # Collect files and subdirectories
        files: list[FileMetrics] = []
        subdirs: list[Path] = []

        try:
            for entry in sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name)):
                if self._should_exclude(entry):
                    continue

                if entry.is_file():
                    if file_metrics := analyze_file(entry, self.root):
                        files.append(file_metrics)
                elif entry.is_dir():
                    subdirs.append(entry)
        except PermissionError:
            pass

        # Process files
        rollup.files = files
        rollup.file_count = len(files)

        # Compute language breakdown for this directory
        lang_loc: dict[str, int] = defaultdict(int)
        total_loc = 0

        for f in files:
            if f.language != Language.OTHER:
                lang_loc[f.language.value] += f.lines_of_code
            total_loc += f.lines_of_code

        rollup.language_breakdown = dict(lang_loc)
        rollup.total_loc = total_loc
        rollup.primary_language = detect_primary_language(lang_loc)

        # Process subdirectories (if not at max depth)
        if self.max_depth < 0 or depth < self.max_depth:
            for subdir in subdirs:
                sub_rollup = self._analyze_directory(subdir, depth + 1)
                rollup.subdirectories.append(sub_rollup)

                # Aggregate language breakdown from subdirectories
                for lang, loc in sub_rollup.language_breakdown.items():
                    lang_loc[lang] += loc

            # Update with aggregated breakdown
            rollup.language_breakdown = dict(lang_loc)

            # Re-detect primary language with subdirectory data
            if lang_loc:
                rollup.primary_language = detect_primary_language(lang_loc)

        return rollup


def analyze_directory(
    root: Path,
    exclude_patterns: set[str] | None = None,
    include_hidden: bool = False,
    max_depth: int = -1,
) -> DirectoryRollup:
    """Convenience function to analyze a directory.

    Args:
        root: Root directory to analyze
        exclude_patterns: Set of directory/file names to exclude
        include_hidden: Whether to include hidden files/directories
        max_depth: Maximum depth to traverse (-1 for unlimited)

    Returns:
        DirectoryRollup with hierarchical statistics
    """
    analyzer = DirectoryAnalyzer(
        root=root,
        exclude_patterns=exclude_patterns,
        include_hidden=include_hidden,
        max_depth=max_depth,
    )
    return analyzer.analyze()
