"""File discovery with .gitignore support."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os

import pathspec


# Default patterns to always exclude (in addition to .gitignore)
_DEFAULT_EXCLUDES = frozenset({
    # Version control
    ".git",
    ".hg",
    ".svn",
    # Dependencies
    "node_modules",
    "vendor",
    "third_party",
    "third-party",
    # Python
    "__pycache__",
    ".pyc",
    "*.egg-info",
    ".eggs",
    "venv",
    "env",
    ".venv",
    ".env",
    "site-packages",
    # Build artifacts
    "dist",
    "build",
    "out",
    "target",
    "_build",
    # IDE
    ".idea",
    ".vscode",
    # Test environments
    "test_env",
    ".tox",
    ".nox",
    # Generated files
    "*.min.js",
    "*.bundle.js",
    "*_generated.*",
    "*.pb.go",
    "*.pb.py",
})

# Supported file extensions for repomap
# Tree-sitter supports many languages - this list determines what files are indexed
SUPPORTED_EXTENSIONS = frozenset({
    # Currently with query files
    ".py",   # Python
    ".go",   # Go
    ".js",   # JavaScript
    ".ts",   # TypeScript
    ".jsx",  # React JSX
    ".tsx",  # React TSX
    # C/C++ family
    ".c",    # C
    ".h",    # C header
    ".cpp",  # C++
    ".cc",   # C++ (alternative)
    ".cxx",  # C++ (alternative)
    ".hpp",  # C++ header
    ".hxx",  # C++ header (alternative)
    # JVM languages
    ".java", # Java
    ".kt",   # Kotlin
    ".kts",  # Kotlin script
    ".scala", # Scala
    ".groovy", # Groovy
    ".clj",  # Clojure
    ".cljs", # ClojureScript
    # .NET languages
    ".cs",   # C#
    ".fs",   # F#
    ".vb",   # Visual Basic
    # Systems languages
    ".rs",   # Rust
    ".swift", # Swift
    # Scripting languages
    ".rb",   # Ruby
    ".php",  # PHP
    ".lua",  # Lua
    ".pl",   # Perl
    ".pm",   # Perl module
    ".sh",   # Shell/Bash
    ".bash", # Bash
    ".zsh",  # Zsh
    # Functional languages
    ".hs",   # Haskell
    ".ex",   # Elixir
    ".exs",  # Elixir script
    ".erl",  # Erlang
    ".ml",   # OCaml
    ".mli",  # OCaml interface
    # Other popular languages
    ".r",    # R
    ".R",    # R (alternative)
    ".sql",  # SQL
    ".dart", # Dart
    ".elm",  # Elm
    ".vue",  # Vue.js
    ".svelte", # Svelte
    # Additional modern languages
    ".zig",  # Zig
    ".nim",  # Nim
    ".jl",   # Julia
    ".v",    # V
    ".odin", # Odin
    ".sol",  # Solidity
    ".cairo", # Cairo (StarkNet)
    ".gleam", # Gleam
    ".purs", # PureScript
    ".nix",  # Nix
    ".rkt",  # Racket
    ".ss",   # Scheme
    ".gd",   # GDScript (Godot)
    ".hx",   # Haxe
    ".m",    # Objective-C
    ".mm",   # Objective-C++
})


@dataclass
class DiscoveryResult:
    """Result of file discovery."""

    files: list[str] = field(default_factory=list)
    skipped_by_gitignore: int = 0
    skipped_by_default: int = 0
    skipped_by_extension: int = 0
    skipped_symlinks: int = 0
    skipped_binary: int = 0
    errors: list[str] = field(default_factory=list)


def _is_binary_file(path: Path, check_bytes: int = 8192) -> bool:
    """Check if a file is binary by looking for null bytes in first N bytes."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(check_bytes)
            return b"\x00" in chunk
    except (OSError, IOError):
        return False


def _load_gitignore_spec(root: Path) -> pathspec.PathSpec | None:
    """Load and compile .gitignore patterns from a directory tree."""
    patterns: list[str] = []

    # Walk up to find .gitignore files (nested ones take precedence)
    gitignore_files: list[Path] = []

    # Check root and all parent directories for .gitignore
    current = root
    while True:
        gitignore = current / ".gitignore"
        if gitignore.is_file():
            gitignore_files.append(gitignore)
        parent = current.parent
        if parent == current:
            break
        current = parent

    # Reverse so parent patterns come first (child overrides)
    gitignore_files.reverse()

    # Also check for nested .gitignore files within root
    try:
        for nested_gitignore in root.rglob(".gitignore"):
            if nested_gitignore not in gitignore_files:
                gitignore_files.append(nested_gitignore)
    except (OSError, PermissionError):
        pass

    for gitignore in gitignore_files:
        try:
            with open(gitignore, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        patterns.append(line)
        except (OSError, PermissionError):
            continue

    if not patterns:
        return None

    return pathspec.PathSpec.from_lines("gitignore", patterns)


def _matches_default_excludes(path: Path, root: Path) -> bool:
    """Check if a path matches default exclusion patterns."""
    # Check directory names in path
    for part in path.relative_to(root).parts:
        if part in _DEFAULT_EXCLUDES:
            return True
        if part.startswith("."):
            return True

    # Check file patterns
    name = path.name
    for pattern in _DEFAULT_EXCLUDES:
        if "*" in pattern:
            # Simple glob match
            if pattern.startswith("*") and name.endswith(pattern[1:]):
                return True
            if pattern.endswith("*") and name.startswith(pattern[:-1]):
                return True
        elif name == pattern:
            return True

    return False


def discover_files(
    root: str | Path,
    extensions: frozenset[str] | None = None,
    additional_excludes: list[str] | None = None,
    respect_gitignore: bool = True,
    follow_symlinks: bool = False,
    skip_binary: bool = True,
) -> DiscoveryResult:
    """Discover source files in a directory tree.

    Args:
        root: Root directory to scan
        extensions: Set of file extensions to include (default: SUPPORTED_EXTENSIONS)
        additional_excludes: Additional directory names to exclude
        respect_gitignore: Whether to respect .gitignore files
        follow_symlinks: Whether to follow symlinks (default: False for safety)
        skip_binary: Whether to skip binary files (default: True)

    Returns:
        DiscoveryResult with discovered files and statistics
    """
    root = Path(root).resolve()
    extensions = extensions or SUPPORTED_EXTENSIONS
    additional_excludes_set = frozenset(additional_excludes or [])

    result = DiscoveryResult()

    # Track canonical paths to detect symlink-induced duplicates
    seen_canonical: set[str] = set()

    # Load gitignore patterns
    gitignore_spec: pathspec.PathSpec | None = None
    if respect_gitignore:
        try:
            gitignore_spec = _load_gitignore_spec(root)
        except Exception as e:
            result.errors.append(f"Failed to load .gitignore: {e}")

    try:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
            current = Path(dirpath)

            # Filter directories in-place to prevent descending
            original_count = len(dirnames)
            filtered_dirs = []
            for d in dirnames:
                subdir = current / d
                # Skip symlink directories unless explicitly following
                if subdir.is_symlink():
                    if not follow_symlinks:
                        result.skipped_symlinks += 1
                        continue
                    # Check for cycles via canonical path
                    try:
                        canonical = str(subdir.resolve())
                        if canonical in seen_canonical:
                            result.skipped_symlinks += 1
                            continue
                        seen_canonical.add(canonical)
                    except (OSError, RuntimeError):
                        result.skipped_symlinks += 1
                        continue

                if (
                    not d.startswith(".")
                    and d not in _DEFAULT_EXCLUDES
                    and d not in additional_excludes_set
                    and "site-packages" not in str(subdir)
                ):
                    filtered_dirs.append(d)

            dirnames[:] = filtered_dirs

            # Check gitignore for directories
            if gitignore_spec:
                kept_dirs = []
                for d in dirnames:
                    rel_path = (current / d).relative_to(root)
                    # Add trailing slash for directory matching
                    if not gitignore_spec.match_file(str(rel_path) + "/"):
                        kept_dirs.append(d)
                    else:
                        result.skipped_by_gitignore += 1
                dirnames[:] = kept_dirs

            result.skipped_by_default += original_count - len(dirnames)

            # Process files
            for fname in filenames:
                fpath = current / fname

                # Skip symlinks unless following
                if fpath.is_symlink():
                    if not follow_symlinks:
                        result.skipped_symlinks += 1
                        continue
                    # Check for duplicates via canonical path
                    try:
                        canonical = str(fpath.resolve())
                        if canonical in seen_canonical:
                            result.skipped_symlinks += 1
                            continue
                        seen_canonical.add(canonical)
                    except (OSError, RuntimeError):
                        result.skipped_symlinks += 1
                        continue

                ext = fpath.suffix

                # Check extension
                if ext not in extensions:
                    result.skipped_by_extension += 1
                    continue

                # Check default excludes
                if _matches_default_excludes(fpath, root):
                    result.skipped_by_default += 1
                    continue

                # Check gitignore
                if gitignore_spec:
                    rel_path = fpath.relative_to(root)
                    if gitignore_spec.match_file(str(rel_path)):
                        result.skipped_by_gitignore += 1
                        continue

                # Check for binary content
                if skip_binary and _is_binary_file(fpath):
                    result.skipped_binary += 1
                    continue

                result.files.append(str(fpath))

    except (OSError, PermissionError) as e:
        result.errors.append(f"Walk error: {e}")

    return result
