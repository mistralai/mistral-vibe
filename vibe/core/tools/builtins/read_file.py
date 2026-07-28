from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, final

from humanize import naturalsize
from pydantic import BaseModel, Field

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
from vibe.core.tools.utils import resolve_file_tool_permission
from vibe.core.types import ToolStreamEvent
from vibe.core.utils import VIBE_WARNING_TAG
from vibe.utils.io import read_lines_safe_async
from vibe.utils.tool_presentation import ToolEffectKind

if TYPE_CHECKING:
    from vibe.core.types import ToolResultEvent

_KB = 1024
DEFAULT_LINE_LIMIT = 2000
MAX_BYTES = 50 * _KB


def _add_line_numbers(lines: list[str], *, start: int) -> str:
    return "\n".join(
        f"{str(n).rjust(9)}\u2192{line}" for n, line in enumerate(lines, start=start)
    )


def _warning(message: str) -> str:
    return f"<{VIBE_WARNING_TAG}>{message}</{VIBE_WARNING_TAG}>"


def _display_relative(path: Path, base: Path) -> Path:
    try:
        return path.relative_to(base)
    except ValueError:
        return path


class ReadFileArgs(BaseModel):
    file_path: str = Field(description="The absolute path to the file to read")
    offset: int | None = Field(
        default=None,
        ge=1,
        description="The line number to start reading from (1-indexed)",
    )
    limit: int = Field(
        default=DEFAULT_LINE_LIMIT,
        gt=0,
        description="The maximum number of lines to read",
    )


class ReadFileResult(BaseModel):
    file_path: str
    content: str
    num_lines: int
    start_line: int
    requested_offset: int | None = None
    requested_limit: int = DEFAULT_LINE_LIMIT
    total_lines: int | None = None
    was_truncated: bool = False


class ReadFileConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS
    sensitive_patterns: list[str] = Field(
        default=["**/.env", "**/.env.*"],
        description="File patterns that trigger ASK even when permission is ALWAYS.",
    )
    max_read_bytes: int = Field(
        default=MAX_BYTES,
        gt=0,
        description="Maximum selected/output bytes to return in one call.",
    )


class ReadFileState(BaseToolState):
    injected_agents_md: set[str] = Field(default_factory=set)


class ReadFile(
    BaseTool[ReadFileArgs, ReadFileResult, ReadFileConfig, ReadFileState],
    ToolUIData[ReadFileArgs, ReadFileResult],
):
    effect_kind = ToolEffectKind.FILE_READ

    def resolve_permission(self, args: ReadFileArgs) -> PermissionContext | None:
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

    def _find_undiscovered_agents_md(self, file_path: Path) -> list[tuple[Path, str]]:
        docs = self.harness_files.find_subdirectory_agents_md(file_path)
        return [
            (d, c)
            for d, c in docs
            if str(d.resolve()) not in self.state.injected_agents_md
        ]

    def get_result_extra(self, result: ReadFileResult) -> str | None:
        new_docs = self._find_undiscovered_agents_md(Path(result.file_path))
        if not new_docs:
            return None
        for d, _ in new_docs:
            self.state.injected_agents_md.add(str(d.resolve()))
        sections = [
            f"Contents of {d}/AGENTS.md (project instructions for this directory):\n\n{c.strip()}"
            for d, c in new_docs
        ]
        return f"<{VIBE_WARNING_TAG}>\n{'\n\n'.join(sections)}\n</{VIBE_WARNING_TAG}>"

    async def _read_file(
        self, args: ReadFileArgs, file_path: Path, tool_io: ToolIOPort | None = None
    ) -> tuple[list[str], int | None, bool]:
        start_line = args.offset or 1
        try:
            if tool_io is not None and tool_io.supports_read:
                result = await tool_io.read_lines(
                    file_path,
                    start_line=start_line,
                    limit=args.limit,
                    max_bytes=self.config.max_read_bytes,
                )
            else:
                result = await read_lines_safe_async(
                    file_path,
                    start_line=start_line,
                    limit=args.limit,
                    max_bytes=self.config.max_read_bytes,
                )
        except Exception as exc:
            raise ToolError(f"Error reading {file_path}: {exc}") from exc
        return result.lines, result.total_lines, result.was_truncated

    @final
    async def run(
        self, args: ReadFileArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ReadFileResult, None]:
        file_path = self._resolve_path(args.file_path)

        start_line = args.offset or 1

        selected, total_lines, was_truncated = await self._read_file(
            args, file_path, ctx.tool_io if ctx is not None else None
        )

        if selected:
            content = _add_line_numbers(selected, start=start_line)
        elif total_lines == 0:
            content = _warning("Warning: the file exists but the contents are empty.")
        elif total_lines is None:
            content = _warning(f"Warning: no content returned for offset {start_line}.")
        else:
            content = _warning(
                f"Warning: the file exists but is shorter than the provided "
                f"offset ({start_line}). The file has {total_lines} lines."
            )

        size = len(content.encode("utf-8"))
        if size > self.config.max_read_bytes:
            raise ToolError(
                f"Output ({naturalsize(size, binary=True)}) exceeds maximum "
                f"allowed size ({naturalsize(self.config.max_read_bytes, binary=True)}). "
                f"Use offset and limit to read a smaller portion of the file."
            )

        if ctx is not None:
            new_docs = self._find_undiscovered_agents_md(file_path)
            if new_docs:
                parts = [
                    f"{_display_relative(d, self.cwd)}/AGENTS.md" for d, _ in new_docs
                ]
                yield ToolStreamEvent(
                    tool_name=self.get_name(),
                    tool_call_id=ctx.tool_call_id,
                    message=f"Discovered {', '.join(parts)}",
                )

        yield ReadFileResult(
            file_path=str(file_path),
            content=content,
            num_lines=len(selected),
            start_line=start_line,
            requested_offset=args.offset,
            requested_limit=args.limit,
            total_lines=total_lines,
            was_truncated=was_truncated,
        )

    def _resolve_path(self, raw_path: str) -> Path:
        if not raw_path.strip():
            raise ToolError("file_path cannot be empty")

        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = self.cwd / path
        path = path.resolve()

        if not path.exists():
            raise ToolError(f"File not found at: {path}")
        if path.is_dir():
            raise ToolError(f"Path is a directory, not a file: {path}")
        return path

    @classmethod
    def format_call_display(cls, args: ReadFileArgs) -> ToolCallDisplay:
        suffix = "(scratchpad)" if is_scratchpad_display_path(args.file_path) else ""
        message = args.file_path
        extras: list[str] = []
        if args.offset:
            extras.append(f"from line {args.offset}")
        if args.limit != DEFAULT_LINE_LIMIT:
            extras.append(f"limit {args.limit} lines")
        if extras:
            message += f" ({', '.join(extras)})"
        return ToolCallDisplay(
            summary=f"Reading {message}",
            suffix=suffix,
            verb="Reading",
            message=message,
            settled_verb="Read",
            settled_message=message,
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, ReadFileResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        path_obj = Path(event.result.file_path)
        n = event.result.num_lines
        word = "line" if n == 1 else "lines"
        message = f"{n} {word} from {path_obj.name}"
        suffix_parts: list[str] = []
        if is_scratchpad_display_path(event.result.file_path):
            suffix_parts.append("(scratchpad)")
        if event.result.was_truncated or (
            event.result.total_lines is not None
            and event.result.start_line + event.result.num_lines - 1
            < event.result.total_lines
        ):
            suffix_parts.append("(truncated)")

        return ToolResultDisplay(
            success=True, verb="Read", message=message, suffix=" ".join(suffix_parts)
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Reading file"
