from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import ClassVar, final

import anyio
from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.tools.utils import resolve_file_tool_permission
from vibe.core.types import ToolResultEvent, ToolStreamEvent


class WriteFileArgs(BaseModel):
    path: str
    content: str
    overwrite: bool = Field(
        default=False, description="Must be set to true to overwrite an existing file."
    )


class WriteFileResult(BaseModel):
    path: str
    bytes_written: int
    file_existed: bool
    content: str


class WriteFileConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    max_write_bytes: int = 64_000
    create_parent_dirs: bool = True


class WriteFile(
    BaseTool[WriteFileArgs, WriteFileResult, WriteFileConfig, BaseToolState],
    ToolUIData[WriteFileArgs, WriteFileResult],
):
    description: ClassVar[str] = (
        "Create or overwrite a UTF-8 file. Fails if file exists unless 'overwrite=True'."
    )

    @classmethod
    def format_call_display(cls, args: WriteFileArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"Writing {args.path}{' (overwrite)' if args.overwrite else ''}",
            content=args.content,
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, WriteFileResult):
            action = "Overwritten" if event.result.file_existed else "Created"
            return ToolResultDisplay(
                success=True, message=f"{action} {Path(event.result.path).name}"
            )

        return ToolResultDisplay(success=True, message="File written")

    @classmethod
    def get_status_text(cls) -> str:
        return "Writing file"

    def resolve_permission(self, args: WriteFileArgs) -> ToolPermission | None:
        return resolve_file_tool_permission(
            args.path,
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
        )

    @final
    async def run(
        self, args: WriteFileArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | WriteFileResult, None]:
        file_path, file_existed, content_bytes = self._prepare_and_validate_path(args)

        await self._write_file(args, file_path)

        yield WriteFileResult(
            path=str(file_path),
            bytes_written=content_bytes,
            file_existed=file_existed,
            content=args.content,
        )

    def _prepare_and_validate_path(self, args: WriteFileArgs) -> tuple[Path, bool, int]:
        if not args.path.strip():
            raise ToolError("Path cannot be empty")

        content_bytes = len(args.content.encode("utf-8"))
        if content_bytes > self.config.max_write_bytes:
            raise ToolError(
                f"Content exceeds {self.config.max_write_bytes} bytes limit"
            )

        file_path = Path(args.path).expanduser()
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        file_path = file_path.resolve()

        file_existed = file_path.exists()

        if file_existed and not args.overwrite:
            raise ToolError(
                f"File '{file_path}' exists. Set overwrite=True to replace."
            )

        if self.config.create_parent_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        elif not file_path.parent.exists():
            raise ToolError(f"Parent directory does not exist: {file_path.parent}")

        return file_path, file_existed, content_bytes

    async def _write_file(self, args: WriteFileArgs, file_path: Path) -> None:
        try:
            async with await anyio.Path(file_path).open(
                mode="w", encoding="utf-8"
            ) as f:
                await f.write(args.content)
        except Exception as e:
            raise ToolError(f"Error writing {file_path}: {e}") from e
