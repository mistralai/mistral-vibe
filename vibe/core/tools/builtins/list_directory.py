"""List directory tool for viewing directory contents.

This tool allows the LLM to see the contents of a directory with metadata.
"""

from __future__ import annotations

import fnmatch
from datetime import datetime, timezone
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


class ListDirectoryArgs(BaseModel):
    """Arguments for listing directory contents."""

    path: str = Field(
        default=".",
        description="Directory path to list. Defaults to current working directory.",
    )
    include_hidden: bool = Field(
        default=False,
        description="Include hidden files and directories (starting with '.').",
    )
    recursive: bool = Field(
        default=False,
        description="Recursively list subdirectories (limited depth).",
    )
    max_depth: int = Field(
        default=2,
        description="Maximum depth for recursive listing (only used if recursive=True).",
    )


class DirectoryEntry(BaseModel):
    """A single directory entry."""

    name: str
    path: str
    is_dir: bool
    size: int | None = None  # None for directories
    modified: str | None = None  # ISO format timestamp
    children_count: int | None = None  # For directories: number of children


class ListDirectoryResult(BaseModel):
    """Result of directory listing."""

    path: str
    entries: list[DirectoryEntry]
    total_entries: int
    was_truncated: bool


class ListDirectoryConfig(BaseToolConfig):
    """Configuration for list_directory tool."""

    permission: ToolPermission = ToolPermission.ALWAYS

    max_entries: int = Field(
        default=200,
        description="Maximum number of entries to return.",
    )
    exclude_patterns: list[str] = Field(
        default=[
            ".git",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            "*.pyc",
        ],
        description="Patterns to exclude from listing.",
    )


class ListDirectoryState(BaseToolState):
    """State for list_directory tool."""

    recently_listed: list[str] = Field(default_factory=list)


class ListDirectory(
    BaseTool[ListDirectoryArgs, ListDirectoryResult, ListDirectoryConfig, ListDirectoryState],
    ToolUIData[ListDirectoryArgs, ListDirectoryResult],
):
    """List contents of a directory with file metadata."""

    description: ClassVar[str] = (
        "List the contents of a directory, showing files and subdirectories "
        "with size and modification time. Use recursive=True to see nested contents."
    )

    @classmethod
    def get_name(cls) -> str:
        return "list_directory"

    @final
    async def run(self, args: ListDirectoryArgs) -> ListDirectoryResult:
        dir_path = self._resolve_path(args.path)
        self._validate_path(dir_path)

        # Track in state
        self.state.recently_listed.append(str(dir_path))
        if len(self.state.recently_listed) > 10:
            self.state.recently_listed.pop(0)

        entries = self._list_entries(
            dir_path,
            args.include_hidden,
            args.recursive,
            args.max_depth,
            current_depth=0,
        )

        was_truncated = len(entries) > self.config.max_entries
        truncated_entries = entries[: self.config.max_entries]

        return ListDirectoryResult(
            path=str(dir_path),
            entries=truncated_entries,
            total_entries=len(entries),
            was_truncated=was_truncated,
        )

    def _resolve_path(self, path: str) -> Path:
        """Resolve the path relative to workdir."""
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.config.effective_workdir / p
        return p.resolve()

    def _validate_path(self, path: Path) -> None:
        """Validate the path exists and is a directory."""
        if not path.exists():
            raise ToolError(f"Path does not exist: {path}")
        if not path.is_dir():
            raise ToolError(f"Path is not a directory: {path}")

    def _should_exclude(self, name: str, include_hidden: bool) -> bool:
        """Check if an entry should be excluded."""
        if not include_hidden and name.startswith("."):
            return True

        for pattern in self.config.exclude_patterns:
            if fnmatch.fnmatch(name, pattern):
                return True

        return False

    def _list_entries(
        self,
        dir_path: Path,
        include_hidden: bool,
        recursive: bool,
        max_depth: int,
        current_depth: int,
    ) -> list[DirectoryEntry]:
        """List directory entries."""
        entries: list[DirectoryEntry] = []

        try:
            items = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            return entries

        for item in items:
            if self._should_exclude(item.name, include_hidden):
                continue

            try:
                stat = item.stat()
                is_dir = item.is_dir()

                # Get relative path
                try:
                    rel_path = item.relative_to(self.config.effective_workdir)
                except ValueError:
                    rel_path = item

                # Count children for directories
                children_count = None
                if is_dir:
                    try:
                        children_count = sum(1 for _ in item.iterdir())
                    except PermissionError:
                        children_count = None

                entry = DirectoryEntry(
                    name=item.name,
                    path=str(rel_path),
                    is_dir=is_dir,
                    size=None if is_dir else stat.st_size,
                    modified=datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                    children_count=children_count,
                )
                entries.append(entry)

                # Recurse into subdirectories
                if is_dir and recursive and current_depth < max_depth:
                    sub_entries = self._list_entries(
                        item,
                        include_hidden,
                        recursive,
                        max_depth,
                        current_depth + 1,
                    )
                    entries.extend(sub_entries)

                # Safety limit
                if len(entries) > self.config.max_entries * 2:
                    break

            except (OSError, PermissionError):
                continue

        return entries

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        args = event.args
        if not isinstance(args, ListDirectoryArgs):
            return ToolCallDisplay(summary="list_directory")

        summary = f"ls: {args.path}"
        if args.recursive:
            summary += f" (recursive, depth={args.max_depth})"

        return ToolCallDisplay(
            summary=summary,
            details={
                "path": args.path,
                "include_hidden": args.include_hidden,
                "recursive": args.recursive,
            },
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, ListDirectoryResult):
            return ToolResultDisplay(
                success=False,
                message=event.error or event.skip_reason or "No result",
            )

        result = event.result
        message = f"Listed {result.total_entries} entries in {Path(result.path).name}/"
        if result.was_truncated:
            message += f" (showing first {len(result.entries)})"

        # Format entries for display
        lines = []
        for e in result.entries[:30]:  # Limit display
            if e.is_dir:
                child_info = f" ({e.children_count} items)" if e.children_count is not None else ""
                lines.append(f"\ud83d\udcc1 {e.path}/{child_info}")
            else:
                size_str = _format_size(e.size) if e.size is not None else "?"
                lines.append(f"\ud83d\udcc4 {e.path} ({size_str})")

        if len(result.entries) > 30:
            lines.append(f"... and {len(result.entries) - 30} more")

        return ToolResultDisplay(
            success=True,
            message=message,
            warnings=["Results truncated"] if result.was_truncated else [],
            details={
                "entries": "\n".join(lines),
                "total": result.total_entries,
            },
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Listing directory"


def _format_size(size: int) -> str:
    """Format file size in human-readable form."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}" if unit != "B" else f"{size}{unit}"
        size /= 1024
    return f"{size:.1f}TB"
