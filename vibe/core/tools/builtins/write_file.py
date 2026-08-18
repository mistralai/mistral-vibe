from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import final

import anyio
from pydantic import BaseModel, Field

from vibe.core.checkpoints import FileSnapshot
from vibe.core.scratchpad import is_scratchpad_display_path
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.io_port import ToolIOPort
from vibe.core.tools.permissions import PermissionContext
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.tools.utils import (
    DEFAULT_SENSITIVE_PATTERNS,
    ToolPath,
    display_file_path,
    resolve_file_tool_permission,
    resolve_tool_path,
)
from vibe.core.types import ToolResultEvent, ToolStreamEvent
from vibe.utils.tool_presentation import ToolEffectKind


class WriteFileArgs(BaseModel):
    file_path: ToolPath = Field(
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
        default_factory=lambda: list(DEFAULT_SENSITIVE_PATTERNS),
        description="File patterns that trigger ASK even when permission is ALWAYS.",
    )
    max_write_bytes: int = 64_000
    create_parent_dirs: bool = True


class WriteFile(
    BaseTool[WriteFileArgs, WriteFileResult, WriteFileConfig, BaseToolState],
    ToolUIData[WriteFileArgs, WriteFileResult],
):
    effect_kind = ToolEffectKind.FILE_WRITE

    @classmethod
    def format_call_display(cls, args: WriteFileArgs) -> ToolCallDisplay:
        suffix = "(scratchpad)" if is_scratchpad_display_path(args.file_path) else ""
        shown = display_file_path(args.file_path)
        return ToolCallDisplay(
            summary=f"Writing {shown}",
            content=args.content,
            suffix=suffix,
            verb="Creating",
            message=shown,
            settled_verb="Created",
            settled_message=shown,
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, WriteFileResult):
            suffix = (
                "(scratchpad)"
                if is_scratchpad_display_path(event.result.file_path)
                else ""
            )
            return ToolResultDisplay(
                success=True,
                verb="Created",
                message=display_file_path(event.result.file_path),
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
            cwd=self.cwd,
            project_roots=self.harness_files.project_roots,
            scratchpad_dir=self.scratchpad_dir,
        )

    @final
    async def run(
        self, args: WriteFileArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | WriteFileResult, None]:
        file_path, content_bytes = self._prepare_and_validate_path(args)

        await self._write_file(
            args, file_path, ctx.tool_io if ctx is not None else None
        )

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

        file_path = resolve_tool_path(args.file_path, self.cwd)

        if file_path.exists():
            raise ToolError(
                f"File '{file_path}' already exists. Use edit to modify it."
            )

        if self.config.create_parent_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        elif not file_path.parent.exists():
            raise ToolError(f"Parent directory does not exist: {file_path.parent}")

        return file_path, content_bytes

    async def _write_file(
        self, args: WriteFileArgs, file_path: Path, tool_io: ToolIOPort | None = None
    ) -> None:
        try:
            if tool_io is not None and tool_io.supports_write:
                await tool_io.write_text(file_path, args.content)
                return
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
