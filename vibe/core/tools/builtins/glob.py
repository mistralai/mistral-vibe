from __future__ import annotations

from collections.abc import AsyncGenerator
import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.permissions import PermissionContext
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.tools.utils import resolve_file_tool_permission
from vibe.core.types import ToolStreamEvent
from vibe.core.utils.io import read_safe

if TYPE_CHECKING:
    from vibe.core.types import ToolResultEvent


class GlobToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    sensitive_patterns: list[str] = Field(
        default=["**/.env", "**/.env.*"],
        description="File patterns that trigger ASK even when permission is ALWAYS.",
    )
    default_max_results: int = Field(
        default=200, description="Default maximum number of files to return."
    )
    exclude_patterns: list[str] = Field(
        default=[
            ".venv/",
            "venv/",
            ".env/",
            "env/",
            "node_modules/",
            ".git/",
            "__pycache__/",
            ".pytest_cache/",
            ".mypy_cache/",
            ".tox/",
            ".nox/",
            ".coverage/",
            "htmlcov/",
            "dist/",
            "build/",
            ".idea/",
            ".vscode/",
            "*.egg-info",
            "*.pyc",
            "*.pyo",
            "*.pyd",
            ".DS_Store",
            "Thumbs.db",
        ],
        description="List of glob patterns to exclude from results (dirs should end with /).",
    )
    codeignore_file: str = Field(
        default=".vibeignore",
        description="Name of the file to read for additional exclusion patterns.",
    )


class GlobArgs(BaseModel):
    pattern: str
    path: str = "."
    max_results: int | None = Field(
        default=None, description="Override the default maximum number of results."
    )


class GlobResult(BaseModel):
    files: list[str] = Field(description="Matched file paths relative to cwd.")
    total_count: int
    was_truncated: bool = Field(
        description="True if results were cut short by max_results."
    )


class Glob(
    BaseTool[GlobArgs, GlobResult, GlobToolConfig, BaseToolState],
    ToolUIData[GlobArgs, GlobResult],
):
    description: ClassVar[str] = (
        "Find files by name pattern using glob matching. "
        "Returns file paths relative to the working directory, sorted alphabetically."
    )

    def resolve_permission(self, args: GlobArgs) -> PermissionContext | None:
        return resolve_file_tool_permission(
            args.path,
            tool_name=self.get_name(),
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
            sensitive_patterns=self.config.sensitive_patterns,
        )

    async def run(
        self, args: GlobArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | GlobResult, None]:
        self._validate_args(args)

        root = Path(args.path).expanduser()
        if not root.is_absolute():
            root = Path.cwd() / root
        root = root.resolve()

        exclude_patterns = self._collect_exclude_patterns()
        max_results = args.max_results or self.config.default_max_results

        matched: list[str] = []
        total = 0

        for match in sorted(root.glob(args.pattern)):
            if not match.is_file():
                continue
            rel = str(match.relative_to(root))
            if self._is_excluded(rel, exclude_patterns):
                continue
            total += 1
            if len(matched) < max_results:
                matched.append(rel)

        yield GlobResult(
            files=matched, total_count=total, was_truncated=total > max_results
        )

    def _validate_args(self, args: GlobArgs) -> None:
        if not args.pattern.strip():
            raise ToolError("Empty glob pattern provided.")

        path_obj = Path(args.path).expanduser()
        if not path_obj.is_absolute():
            path_obj = Path.cwd() / path_obj

        if not path_obj.exists():
            raise ToolError(f"Path does not exist: {args.path}")

        if path_obj.exists() and not path_obj.is_dir():
            raise ToolError(f"Path is not a directory: {args.path}")

    def _collect_exclude_patterns(self) -> list[str]:
        patterns = list(self.config.exclude_patterns)

        codeignore_path = Path.cwd() / self.config.codeignore_file
        if codeignore_path.is_file():
            patterns.extend(self._load_codeignore_patterns(codeignore_path))

        return patterns

    def _load_codeignore_patterns(self, codeignore_path: Path) -> list[str]:
        patterns: list[str] = []
        try:
            content = read_safe(codeignore_path)
            for line in content.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    patterns.append(stripped)
        except OSError:
            pass
        return patterns

    def _is_excluded(self, rel_path: str, exclude_patterns: list[str]) -> bool:
        parts = Path(rel_path).parts
        for pattern in exclude_patterns:
            if pattern.endswith("/"):
                # Directory pattern - check if any path component matches
                dir_name = pattern.rstrip("/")
                if any(fnmatch.fnmatch(part, dir_name) for part in parts[:-1]):
                    return True
                # Also check if a directory component matches exactly
                if any(part == dir_name for part in parts[:-1]):
                    return True
            else:
                # File pattern - match against the full relative path or filename
                filename = parts[-1] if parts else rel_path
                if fnmatch.fnmatch(filename, pattern):
                    return True
                if fnmatch.fnmatch(rel_path, pattern):
                    return True
        return False

    @classmethod
    def format_call_display(cls, args: GlobArgs) -> ToolCallDisplay:
        summary = f"Globbing '{args.pattern}'"
        if args.path != ".":
            summary += f" in {args.path}"
        if args.max_results:
            summary += f" (max {args.max_results} results)"
        return ToolCallDisplay(summary=summary)

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, GlobResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        message = f"Found {event.result.total_count} files"
        if event.result.was_truncated:
            message += f" (showing {len(event.result.files)})"

        warnings: list[str] = []
        if event.result.was_truncated:
            warnings.append("Results were truncated due to max_results limit")

        return ToolResultDisplay(success=True, message=message, warnings=warnings)

    @classmethod
    def get_status_text(cls) -> str:
        return "Finding files"
