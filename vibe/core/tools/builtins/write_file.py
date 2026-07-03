from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import final

import anyio
from pydantic import BaseModel, Field

from vibe.core.rewind.manager import FileSnapshot
from vibe.core.scratchpad import is_scratchpad_path
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
from vibe.core.types import ToolResultEvent, ToolStreamEvent


class WriteFileArgs(BaseModel):
    file_path: str = Field(
        description="The absolute path to the file to write (must be absolute, not relative)"
    )
    content: str = Field(description="The content to write to the file")


class WriteFileResult(BaseModel):
    file_path: str
    bytes_written: int
    content: str


class WriteFileConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    sensitive_patterns: list[str] = Field(
        default=["**/.env", "**/.env.*"],
        description="File patterns that trigger ASK even when permission is ALWAYS.",
    )
    max_write_bytes: int = 64_000
    create_parent_dirs: bool = True


class WriteFile(
    BaseTool[WriteFileArgs, WriteFileResult, WriteFileConfig, BaseToolState],
    ToolUIData[WriteFileArgs, WriteFileResult],
):
    @classmethod
    def format_call_display(cls, args: WriteFileArgs) -> ToolCallDisplay:
        suffix = "(scratchpad)" if is_scratchpad_path(args.file_path) else ""
        return ToolCallDisplay(
            summary=f"Writing {args.file_path}", content=args.content, suffix=suffix
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, WriteFileResult):
            suffix = (
                "(scratchpad)" if is_scratchpad_path(event.result.file_path) else ""
            )
            return ToolResultDisplay(
                success=True,
                message=f"Created {Path(event.result.file_path).name}",
                suffix=suffix,
            )

        return ToolResultDisplay(success=True, message="File written")

    @classmethod
    def get_status_text(cls) -> str:
        return "Writing file"

    def get_file_snapshot(self, args: WriteFileArgs) -> FileSnapshot | None:
        return self.get_file_snapshot_for_path(args.file_path)

    def resolve_permission(self, args: WriteFileArgs) -> PermissionContext | None:
        return resolve_file_tool_permission(
            args.file_path,
            tool_name=self.get_name(),
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
            sensitive_patterns=self.config.sensitive_patterns,
        )

    @final
    async def run(
        self, args: WriteFileArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | WriteFileResult, None]:
        file_path, content_bytes = self._prepare_and_validate_path(args)

        await self._write_file(args, file_path)

        yield WriteFileResult(
            file_path=str(file_path), bytes_written=content_bytes, content=args.content
        )

    def _prepare_and_validate_path(self, args: WriteFileArgs) -> tuple[Path, int]:
        if not args.file_path.strip():
            raise ToolError("Path cannot be empty")

        content_bytes = len(args.content.encode("utf-8"))
        if content_bytes > self.config.max_write_bytes:
            raise ToolError(
                f"Content exceeds {self.config.max_write_bytes} bytes limit"
            )

        file_path = Path(args.file_path).expanduser()
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        file_path = file_path.resolve()

        if file_path.exists():
            raise ToolError(
                f"File '{file_path}' already exists. Use edit to modify it."
            )

        if self.config.create_parent_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        elif not file_path.parent.exists():
            raise ToolError(f"Parent directory does not exist: {file_path.parent}")

        return file_path, content_bytes

    async def _write_file(self, args: WriteFileArgs, file_path: Path) -> None:
        try:
            async with await anyio.Path(file_path).open(
                mode="x", encoding="utf-8"
            ) as f:
                await f.write(args.content)
        except FileExistsError as e:
            raise ToolError(
                f"File '{file_path}' already exists. Use edit to modify it."
            ) from e
        except Exception as e:
            raise ToolError(f"Error writing {file_path}: {e}") from e
