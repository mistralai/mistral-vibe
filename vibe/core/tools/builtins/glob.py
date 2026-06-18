from __future__ import annotations

from collections.abc import AsyncGenerator
import glob as globlib
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

if TYPE_CHECKING:
    from vibe.core.types import ToolResultEvent

_DEFAULT_EXCLUDED_DIRS = frozenset({
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
    ".nox",
    "dist",
    "build",
    ".idea",
    ".vscode",
})


class GlobToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    sensitive_patterns: list[str] = Field(
        default=["**/.env", "**/.env.*"],
        description="File patterns that trigger ASK even when permission is ALWAYS.",
    )
    default_max_results: int = Field(
        default=200, description="Default maximum number of matches to return."
    )
    excluded_dirs: list[str] = Field(
        default_factory=lambda: sorted(_DEFAULT_EXCLUDED_DIRS),
        description="Directory names pruned from results.",
    )


class GlobArgs(BaseModel):
    pattern: str = Field(
        description="Glob pattern to match against file paths, e.g. '**/*.py' or 'src/*.ts'."
    )
    path: str = Field(
        default=".",
        description="Base directory to search from. Defaults to the current directory.",
    )
    max_results: int | None = Field(
        default=None, description="Override the default maximum number of matches."
    )


class GlobResult(BaseModel):
    matches: list[str] = Field(description="Matching paths, newest first.")
    match_count: int
    was_truncated: bool = Field(
        description="True if the result was cut short by max_results."
    )


class Glob(
    BaseTool[GlobArgs, GlobResult, GlobToolConfig, BaseToolState],
    ToolUIData[GlobArgs, GlobResult],
):
    description: ClassVar[str] = (
        "Find files by glob pattern (e.g. '**/*.py'), returning paths sorted by "
        "modification time (newest first). Faster and more reliable than shelling out "
        "to find/ls. Use this to locate files when you know the name shape but not the path."
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
        if not args.pattern.strip():
            raise ToolError("Empty glob pattern provided.")

        base = Path(args.path).expanduser()
        if not base.is_absolute():
            base = Path.cwd() / base
        if not base.exists():
            raise ToolError(f"Path does not exist: {args.path}")

        max_results = args.max_results or self.config.default_max_results
        matches = self._collect_matches(args.pattern, base)

        was_truncated = len(matches) > max_results
        yield GlobResult(
            matches=[str(p) for p in matches[:max_results]],
            match_count=min(len(matches), max_results),
            was_truncated=was_truncated,
        )

    def _collect_matches(self, pattern: str, base: Path) -> list[Path]:
        excluded = set(self.config.excluded_dirs)
        found: list[Path] = []
        for rel in globlib.glob(pattern, root_dir=base, recursive=True):
            candidate = (base / rel).resolve()
            if excluded.isdisjoint(candidate.parts):
                found.append(candidate)
        found.sort(key=self._mtime, reverse=True)
        return found

    @staticmethod
    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    @classmethod
    def format_call_display(cls, args: GlobArgs) -> ToolCallDisplay:
        summary = f"Globbing '{args.pattern}'"
        if args.path != ".":
            summary += f" in {args.path}"
        return ToolCallDisplay(summary=summary)

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, GlobResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        message = f"Found {event.result.match_count} files"
        suffix = "(truncated)" if event.result.was_truncated else ""
        return ToolResultDisplay(success=True, message=message, suffix=suffix)

    @classmethod
    def get_status_text(cls) -> str:
        return "Finding files"
