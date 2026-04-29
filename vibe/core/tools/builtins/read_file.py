from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, NamedTuple, final

import anyio
from pydantic import BaseModel, Field

from vibe.core.config.harness_files import get_harness_files_manager
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
from vibe.core.types import ToolStreamEvent
from vibe.core.utils import VIBE_WARNING_TAG
from vibe.core.utils.io import decode_safe

if TYPE_CHECKING:
    from vibe.core.types import ToolResultEvent


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
    offset: int = 0
    lines_read: int
    was_truncated: bool = Field(
        description="True if the reading was stopped due to the max_read_bytes limit."
    )


class ReadFileToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    sensitive_patterns: list[str] = Field(
        default=["**/.env", "**/.env.*"],
        description="File patterns that trigger ASK even when permission is ALWAYS.",
    )

    max_read_bytes: int = Field(
        default=64_000, description="Maximum total bytes to read from a file in one go."
    )


class ReadFileState(BaseToolState):
    injected_agents_md: set[str] = Field(default_factory=set)


class ReadFile(
    BaseTool[ReadFileArgs, ReadFileResult, ReadFileToolConfig, ReadFileState],
    ToolUIData[ReadFileArgs, ReadFileResult],
):
    description: ClassVar[str] = (
        "Read a text file (encoding detected safely), returning content from a "
        "specific line range. Reading is capped by a byte limit for safety."
    )

    @final
    async def run(
        self, args: ReadFileArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ReadFileResult, None]:
        file_path = self._prepare_and_validate_path(args)

        read_result = await self._read_file(args, file_path)

        yield ReadFileResult(
            path=str(file_path),
            content="".join(read_result.lines),
            offset=args.offset,
            lines_read=len(read_result.lines),
            was_truncated=read_result.was_truncated,
        )

    def resolve_permission(self, args: ReadFileArgs) -> PermissionContext | None:
        return resolve_file_tool_permission(
            args.path,
            tool_name=self.get_name(),
            allowlist=self.config.allowlist,
            denylist=self.config.denylist,
            config_permission=self.config.permission,
            sensitive_patterns=self.config.sensitive_patterns,
        )

    def get_result_extra(self, result: ReadFileResult) -> str | None:
        try:
            mgr = get_harness_files_manager()
        except RuntimeError:
            return None
        docs = mgr.find_subdirectory_agents_md(Path(result.path))
        new_docs = [
            (d, c)
            for d, c in docs
            if str(d.resolve()) not in self.state.injected_agents_md
        ]
        if not new_docs:
            return None
        for d, _ in new_docs:
            self.state.injected_agents_md.add(str(d.resolve()))
        sections = [
            f"Contents of {d}/AGENTS.md (project instructions for this directory):\n\n{c.strip()}"
            for d, c in new_docs
        ]
        return f"<{VIBE_WARNING_TAG}>\n{'\n\n'.join(sections)}\n</{VIBE_WARNING_TAG}>"

    def _prepare_and_validate_path(self, args: ReadFileArgs) -> Path:
        self._validate_inputs(args)

        file_path = Path(args.path).expanduser()
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path

        self._validate_path(file_path)
        return file_path

    async def _read_file(self, args: ReadFileArgs, file_path: Path) -> _ReadResult:
        try:
            raw_lines: list[bytes] = []
            bytes_read = 0
            was_truncated = False

            async with await anyio.Path(file_path).open("rb") as f:
                line_index = 0
                while raw_line := await f.readline():
                    if line_index < args.offset:
                        line_index += 1
                        continue

                    if args.limit is not None and len(raw_lines) >= args.limit:
                        break

                    line_bytes = len(raw_line)
                    if bytes_read + line_bytes > self.config.max_read_bytes:
                        was_truncated = True
                        break

                    raw_lines.append(raw_line)
                    bytes_read += line_bytes
                    line_index += 1
        except OSError as exc:
            raise ToolError(f"Error reading {file_path}: {exc}") from exc

        lines_to_return = decode_safe(b"".join(raw_lines)).text.splitlines(
            keepends=True
        )
        return _ReadResult(
            lines=lines_to_return, bytes_read=bytes_read, was_truncated=was_truncated
        )

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
                f"Security error: Cannot read path '{file_path}' outside of the project directory '{Path.cwd()}'."
            )
        except FileNotFoundError:
            raise ToolError(f"File not found at: {file_path}")

        if not resolved_path.exists():
            raise ToolError(f"File not found at: {file_path}")
        if resolved_path.is_dir():
            raise ToolError(f"Path is a directory, not a file: {file_path}")

    @classmethod
    def format_call_display(cls, args: ReadFileArgs) -> ToolCallDisplay:
        tag = " (scratchpad)" if is_scratchpad_path(args.path) else ""
        summary = f"Reading {args.path}"
        if args.offset > 0 or args.limit is not None:
            parts = []
            if args.offset > 0:
                parts.append(f"from line {args.offset}")
            if args.limit is not None:
                parts.append(f"limit {args.limit} lines")
            summary += f" ({', '.join(parts)})"
        return ToolCallDisplay(summary=f"{summary}{tag}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, ReadFileResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        path_obj = Path(event.result.path)
        tag = " (scratchpad)" if is_scratchpad_path(event.result.path) else ""
        message = f"Read {event.result.lines_read} line{'' if event.result.lines_read <= 1 else 's'} from {path_obj.name}{tag}"
        if event.result.was_truncated:
            message += " (truncated)"

        return ToolResultDisplay(
            success=True,
            message=message,
            warnings=["File was truncated due to size limit"]
            if event.result.was_truncated
            else [],
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Reading file"
