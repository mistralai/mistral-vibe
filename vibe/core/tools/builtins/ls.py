from __future__ import annotations

from collections.abc import AsyncGenerator
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


class LsToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    sensitive_patterns: list[str] = Field(
        default=["**/.env", "**/.env.*"],
        description="File patterns that trigger ASK even when permission is ALWAYS.",
    )
    exclude_patterns: list[str] = Field(
        default=[
            ".git",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".DS_Store",
            "Thumbs.db",
        ],
        description="Names to exclude from listing.",
    )


class LsArgs(BaseModel):
    path: str = Field(default=".", description="Directory to list.")


class LsEntry(BaseModel):
    name: str = Field(description="File or directory name.")
    type: str = Field(description="'file' or 'directory'.")
    size: int | None = Field(default=None, description="Size in bytes (files only).")


class LsResult(BaseModel):
    entries: list[LsEntry] = Field(description="Directory contents.")
    total_count: int = Field(description="Number of entries returned.")


class Ls(
    BaseTool[LsArgs, LsResult, LsToolConfig, BaseToolState],
    ToolUIData[LsArgs, LsResult],
):
    description: ClassVar[str] = (
        "List directory contents with file type and size. "
        "Shows immediate contents only, does not recurse into subdirectories. "
        "For finding files recursively by pattern, use the glob tool instead."
    )

    def resolve_permission(self, args: LsArgs) -> PermissionContext | None:
        return resolve_file_tool_permission(
            args.path,
            tool_name=self.get_name(),
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
            sensitive_patterns=self.config.sensitive_patterns,
        )

    async def run(
        self, args: LsArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | LsResult, None]:
        root = self._resolve_path(args.path)
        exclude = set(self.config.exclude_patterns)

        dirs: list[LsEntry] = []
        files: list[LsEntry] = []

        for child in sorted(root.iterdir()):
            if child.name in exclude:
                continue

            if child.is_dir():
                dirs.append(LsEntry(name=child.name, type="directory"))
            else:
                try:
                    size = child.stat().st_size
                except OSError:
                    size = None
                files.append(LsEntry(name=child.name, type="file", size=size))

        entries = dirs + files
        yield LsResult(entries=entries, total_count=len(entries))

    def _resolve_path(self, path_str: str) -> Path:
        path = Path(path_str).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path

        if not path.exists():
            raise ToolError(f"Path does not exist: {path_str}")
        if not path.is_dir():
            raise ToolError(f"Path is not a directory: {path_str}")

        return path

    @classmethod
    def format_call_display(cls, args: LsArgs) -> ToolCallDisplay:
        match args.path:
            case ".":
                summary = "Listing current directory"
            case _:
                summary = f"Listing {args.path}"
        return ToolCallDisplay(summary=summary)

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, LsResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        dir_count = sum(1 for e in event.result.entries if e.type == "directory")
        file_count = sum(1 for e in event.result.entries if e.type == "file")
        message = (
            f"{event.result.total_count} entries "
            f"({dir_count} directories, {file_count} files)"
        )

        return ToolResultDisplay(success=True, message=message)

    @classmethod
    def get_status_text(cls) -> str:
        return "Listing directory"
