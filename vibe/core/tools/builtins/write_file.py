from __future__ import annotations

from pathlib import Path
from typing import ClassVar, final

import aiofiles
from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import ToolCallEvent, ToolResultEvent


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


class WriteFileState(BaseToolState):
    recently_written_files: list[str] = Field(default_factory=list)


class WriteFile(
    BaseTool[WriteFileArgs, WriteFileResult, WriteFileConfig, WriteFileState],
    ToolUIData[WriteFileArgs, WriteFileResult],
):
    description: ClassVar[str] = (
        "Create or overwrite a UTF-8 file. Fails if file exists unless 'overwrite=True'."
    )

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        if not isinstance(event.args, WriteFileArgs):
            return ToolCallDisplay(summary="Invalid arguments")

        args = event.args
        file_ext = Path(args.path).suffix.lstrip(".")

        return ToolCallDisplay(
            summary=f"Writing {args.path}{' (overwrite)' if args.overwrite else ''}",
            content=args.content,
            details={
                "path": args.path,
                "overwrite": args.overwrite,
                "file_extension": file_ext,
                "content": args.content,
            },
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if isinstance(event.result, WriteFileResult):
            action = "Overwritten" if event.result.file_existed else "Created"
            return ToolResultDisplay(
                success=True,
                message=f"{action} {Path(event.result.path).name}",
                details={
                    "bytes_written": event.result.bytes_written,
                    "path": event.result.path,
                    "content": event.result.content,
                },
            )

        return ToolResultDisplay(success=True, message="File written")

    @classmethod
    def get_status_text(cls) -> str:
        return "Writing file"

    def check_allowlist_denylist(self, args: WriteFileArgs) -> ToolPermission | None:
        import fnmatch

        file_path = Path(args.path).expanduser()
        if not file_path.is_absolute():
            file_path = self.config.effective_workdir / file_path
        file_str = str(file_path)

        for pattern in self.config.denylist:
            if fnmatch.fnmatch(file_str, pattern):
                return ToolPermission.NEVER

        for pattern in self.config.allowlist:
            if fnmatch.fnmatch(file_str, pattern):
                return ToolPermission.ALWAYS

        return None

    @final
    async def run(self, args: WriteFileArgs) -> WriteFileResult:
        file_path, file_existed, content_bytes = self._prepare_and_validate_path(args)

        await self._write_file(args, file_path)

        BUFFER_SIZE = 10
        self.state.recently_written_files.append(str(file_path))
        if len(self.state.recently_written_files) > BUFFER_SIZE:
            self.state.recently_written_files.pop(0)

        return WriteFileResult(
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
            file_path = self.config.effective_workdir / file_path
        file_path = file_path.resolve()

        try:
            file_path.relative_to(self.config.effective_workdir.resolve())
        except ValueError:
            raise ToolError(f"Cannot write outside project directory: {file_path}")

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
            async with aiofiles.open(file_path, mode="w", encoding="utf-8") as f:
                await f.write(args.content)
        except Exception as e:
            raise ToolError(f"Error writing {file_path}: {e}") from e
