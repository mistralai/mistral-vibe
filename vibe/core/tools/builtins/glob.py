"""Glob tool for finding files by pattern.

This tool allows the LLM to find files matching glob patterns.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, final

from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData

if TYPE_CHECKING:
    from vibe.core.types import ToolCallEvent, ToolResultEvent


class GlobArgs(BaseModel):
    """Arguments for glob file search."""

    pattern: str = Field(
        description="Glob pattern to match files (e.g., '**/*.py', 'src/**/*.ts', '*.md')."
    )
    path: str = Field(
        default=".",
        description="Base directory to search from. Defaults to current working directory.",
    )
    include_hidden: bool = Field(
        default=False,
        description="Include hidden files and directories (starting with '.').",
    )


class GlobMatch(BaseModel):
    """A single file match."""

    path: str
    name: str
    is_dir: bool
    size: int | None = None  # None for directories


class GlobResult(BaseModel):
    """Result of glob search."""

    matches: list[GlobMatch]
    total_matches: int
    was_truncated: bool = Field(
        description="True if output was limited by max_matches."
    )
    pattern: str
    base_path: str


class GlobToolConfig(BaseToolConfig):
    """Configuration for the glob tool."""

    permission: ToolPermission = ToolPermission.ALWAYS

    max_matches: int = Field(
        default=500,
        description="Maximum number of matches to return.",
    )
    exclude_patterns: list[str] = Field(
        default=[
            ".git",
            ".venv",
            "venv",
            ".env",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".tox",
            ".nox",
            "dist",
            "build",
            "*.egg-info",
            ".idea",
            ".vscode",
        ],
        description="Patterns to exclude from search results.",
    )


class GlobState(BaseToolState):
    """State for glob tool."""

    search_history: list[str] = Field(default_factory=list)


class Glob(
    BaseTool[GlobArgs, GlobResult, GlobToolConfig, GlobState],
    ToolUIData[GlobArgs, GlobResult],
):
    """Find files matching a glob pattern."""

    description: ClassVar[str] = (
        "Find files matching a glob pattern. "
        "Supports patterns like '**/*.py' (all Python files), "
        "'src/**/*.ts' (TypeScript in src), '*.md' (markdown in current dir). "
        "Use ** for recursive matching."
    )

    @classmethod
    def get_name(cls) -> str:
        return "glob"

    @final
    async def run(self, args: GlobArgs) -> GlobResult:
        base_path = self._resolve_path(args.path)
        self._validate_path(base_path)

        self.state.search_history.append(args.pattern)

        matches = self._find_matches(base_path, args.pattern, args.include_hidden)

        was_truncated = len(matches) > self.config.max_matches
        truncated_matches = matches[: self.config.max_matches]

        return GlobResult(
            matches=truncated_matches,
            total_matches=len(matches),
            was_truncated=was_truncated,
            pattern=args.pattern,
            base_path=str(base_path),
        )

    def _resolve_path(self, path: str) -> Path:
        """Resolve the path relative to workdir."""
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.config.effective_workdir / p
        return p.resolve()

    def _validate_path(self, path: Path) -> None:
        """Validate the base path exists and is a directory."""
        if not path.exists():
            raise ToolError(f"Path does not exist: {path}")
        if not path.is_dir():
            raise ToolError(f"Path is not a directory: {path}")

    def _should_exclude(self, path: Path, include_hidden: bool) -> bool:
        """Check if a path should be excluded."""
        name = path.name

        # Skip hidden files/dirs unless requested
        if not include_hidden and name.startswith("."):
            return True

        # Check exclude patterns
        for pattern in self.config.exclude_patterns:
            if fnmatch.fnmatch(name, pattern):
                return True

        return False

    def _find_matches(
        self, base_path: Path, pattern: str, include_hidden: bool
    ) -> list[GlobMatch]:
        """Find all files matching the pattern."""
        matches: list[GlobMatch] = []

        try:
            # Use rglob for ** patterns, glob otherwise
            if "**" in pattern:
                iterator = base_path.glob(pattern)
            else:
                iterator = base_path.glob(pattern)

            for path in iterator:
                # Check exclusions on each path component
                skip = False
                for part in path.relative_to(base_path).parts:
                    test_path = Path(part)
                    if self._should_exclude(test_path, include_hidden):
                        skip = True
                        break

                if skip:
                    continue

                try:
                    is_dir = path.is_dir()
                    size = None if is_dir else path.stat().st_size

                    # Get relative path for cleaner output
                    try:
                        rel_path = path.relative_to(self.config.effective_workdir)
                    except ValueError:
                        rel_path = path

                    matches.append(
                        GlobMatch(
                            path=str(rel_path),
                            name=path.name,
                            is_dir=is_dir,
                            size=size,
                        )
                    )
                except (OSError, PermissionError):
                    # Skip files we can't access
                    continue

                # Early exit if we have way too many matches
                if len(matches) > self.config.max_matches * 2:
                    break

        except Exception as exc:
            raise ToolError(f"Error searching with pattern '{pattern}': {exc}") from exc

        # Sort: directories first, then by path
        matches.sort(key=lambda m: (not m.is_dir, m.path.lower()))

        return matches

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        args = event.args
        if not isinstance(args, GlobArgs):
            return ToolCallDisplay(summary="glob")

        summary = f"glob: '{args.pattern}'"
        if args.path != ".":
            summary += f" in {args.path}"

        return ToolCallDisplay(
            summary=summary,
            details={
                "pattern": args.pattern,
                "path": args.path,
                "include_hidden": args.include_hidden,
            },
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, GlobResult):
            return ToolResultDisplay(
                success=False,
                message=event.error or event.skip_reason or "No result",
            )

        result = event.result
        message = f"Found {result.total_matches} match{'es' if result.total_matches != 1 else ''}"
        if result.was_truncated:
            message += f" (showing first {len(result.matches)})"

        # Format matches for display
        match_lines = []
        for m in result.matches[:50]:  # Limit display
            prefix = "\ud83d\udcc1 " if m.is_dir else "\ud83d\udcc4 "
            size_str = f" ({_format_size(m.size)})" if m.size is not None else ""
            match_lines.append(f"{prefix}{m.path}{size_str}")

        if len(result.matches) > 50:
            match_lines.append(f"... and {len(result.matches) - 50} more")

        return ToolResultDisplay(
            success=True,
            message=message,
            warnings=["Results truncated due to match limit"] if result.was_truncated else [],
            details={
                "total_matches": result.total_matches,
                "matches": "\n".join(match_lines),
            },
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Finding files"


def _format_size(size: int) -> str:
    """Format file size in human-readable form."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}" if unit != "B" else f"{size}{unit}"
        size /= 1024
    return f"{size:.1f}TB"
