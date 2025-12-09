from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, NamedTuple, final

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

if TYPE_CHECKING:
    from vibe.core.types import ToolCallEvent, ToolResultEvent


class _ReadResult(NamedTuple):
    lines: list[str]
    bytes_read: int
    was_truncated: bool


class ReadFileArgs(BaseModel):
    path: str
    offset: int = Field(
        default=0,
        description="Line number to start reading from (0-indexed, inclusive).",
    )
    limit: int | None = Field(
        default=None, description="Maximum number of lines to read."
    )


class ReadFileResult(BaseModel):
    path: str
    content: str
    lines_read: int
    was_truncated: bool = Field(
        description="True if the reading was stopped due to the max_read_bytes limit."
    )


class ReadFileToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS

    max_read_bytes: int = Field(
        default=64_000, description="Maximum total bytes to read from a file in one go."
    )
    max_state_history: int = Field(
        default=10, description="Number of recently read files to remember in state."
    )


class ReadFileState(BaseToolState):
    recently_read_files: list[str] = Field(default_factory=list)


class ReadFile(
    BaseTool[ReadFileArgs, ReadFileResult, ReadFileToolConfig, ReadFileState],
    ToolUIData[ReadFileArgs, ReadFileResult],
):
    description: ClassVar[str] = (
        "Read a UTF-8 file, returning content from a specific line range. "
        "Reading is capped by a byte limit for safety."
    )

    @final
    async def run(self, args: ReadFileArgs) -> ReadFileResult:
        file_path = self._prepare_and_validate_path(args)

        read_result = await self._read_file(args, file_path)

        self._update_state_history(file_path)

        return ReadFileResult(
            path=str(file_path),
            content="".join(read_result.lines),
            lines_read=len(read_result.lines),
            was_truncated=read_result.was_truncated,
        )

    def check_allowlist_denylist(self, args: ReadFileArgs) -> ToolPermission | None:
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

    def _prepare_and_validate_path(self, args: ReadFileArgs) -> Path:
        self._validate_inputs(args)

        file_path = Path(args.path).expanduser()
        if not file_path.is_absolute():
            file_path = self.config.effective_workdir / file_path

        self._validate_path(file_path)
        return file_path

    async def _read_file(self, args: ReadFileArgs, file_path: Path) -> _ReadResult:
        try:
            lines_to_return: list[str] = []
            bytes_read = 0
            was_truncated = False

            async with aiofiles.open(file_path, encoding="utf-8", errors="ignore") as f:
                line_index = 0
                async for line in f:
                    if line_index < args.offset:
                        line_index += 1
                        continue

                    if args.limit is not None and len(lines_to_return) >= args.limit:
                        break

                    line_bytes = len(line.encode("utf-8"))
                    if bytes_read + line_bytes > self.config.max_read_bytes:
                        was_truncated = True
                        break

                    lines_to_return.append(line)
                    bytes_read += line_bytes
                    line_index += 1

            return _ReadResult(
                lines=lines_to_return,
                bytes_read=bytes_read,
                was_truncated=was_truncated,
            )

        except OSError as exc:
            raise ToolError(f"Error reading {file_path}: {exc}") from exc

    def _validate_inputs(self, args: ReadFileArgs) -> None:
        if not args.path.strip():
            raise ToolError("Path cannot be empty")
        if args.offset < 0:
            raise ToolError("Offset cannot be negative")
        if args.limit is not None and args.limit <= 0:
            raise ToolError("Limit, if provided, must be a positive number")

    def _validate_path(self, file_path: Path) -> None:
        try:
            resolved_path = file_path.resolve()
        except ValueError:
            raise ToolError(
                f"Security error: Cannot read path '{file_path}' outside of the project directory '{self.config.effective_workdir}'."
            )
        except FileNotFoundError:
            raise ToolError(f"File not found at: {file_path}")

        if not resolved_path.exists():
            raise ToolError(f"File not found at: {file_path}")
        if resolved_path.is_dir():
            raise ToolError(f"Path is a directory, not a file: {file_path}")

    def _update_state_history(self, file_path: Path) -> None:
        self.state.recently_read_files.append(str(file_path.resolve()))
        if len(self.state.recently_read_files) > self.config.max_state_history:
            self.state.recently_read_files.pop(0)

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        if not isinstance(event.args, ReadFileArgs):
            return ToolCallDisplay(summary="read_file")

        summary = f"read_file: {event.args.path}"
        if event.args.offset > 0 or event.args.limit is not None:
            parts = []
            if event.args.offset > 0:
                parts.append(f"from line {event.args.offset}")
            if event.args.limit is not None:
                parts.append(f"limit {event.args.limit} lines")
            summary += f" ({', '.join(parts)})"

        return ToolCallDisplay(
            summary=summary,
            details={
                "path": event.args.path,
                "offset": event.args.offset,
                "limit": event.args.limit,
            },
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, ReadFileResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        path_obj = Path(event.result.path)
        message = f"Read {event.result.lines_read} line{'' if event.result.lines_read <= 1 else 's'} from {path_obj.name}"
        if event.result.was_truncated:
            message += " (truncated)"

        return ToolResultDisplay(
            success=True,
            message=message,
            warnings=["File was truncated due to size limit"]
            if event.result.was_truncated
            else [],
            details={
                "path": str(event.result.path),
                "lines_read": event.result.lines_read,
                "was_truncated": event.result.was_truncated,
                "content": event.result.content,
                "file_extension": path_obj.suffix.lstrip(".")
                if path_obj.suffix
                else "text",
            },
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Reading file"
