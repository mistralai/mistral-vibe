from __future__ import annotations

from pathlib import Path

from acp import ReadTextFileRequest

from vibe import VIBE_ROOT
from vibe.acp.tools.base import AcpToolState, BaseAcpTool
from vibe.core.tools.base import ToolError
from vibe.core.tools.builtins.read_file import (
    ReadFile as CoreReadFileTool,
    ReadFileArgs,
    ReadFileResult,
    ReadFileState,
    _ReadResult,
)

ReadFileResult = ReadFileResult


class AcpReadFileState(ReadFileState, AcpToolState):
    pass


class ReadFile(CoreReadFileTool, BaseAcpTool[AcpReadFileState]):
    state: AcpReadFileState
    prompt_path = VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "read_file.md"

    @classmethod
    def _get_tool_state_class(cls) -> type[AcpReadFileState]:
        return AcpReadFileState

    async def _read_file(self, args: ReadFileArgs, file_path: Path) -> _ReadResult:
        connection, session_id, _ = self._load_state()

        line = args.offset + 1 if args.offset > 0 else None
        limit = args.limit

        read_request = ReadTextFileRequest(
            sessionId=session_id, path=str(file_path), line=line, limit=limit
        )

        await self._send_in_progress_session_update()

        try:
            response = await connection.readTextFile(read_request)
        except Exception as e:
            raise ToolError(f"Error reading {file_path}: {e}") from e

        content_lines = response.content.splitlines(keepends=True)
        lines_read = len(content_lines)
        bytes_read = sum(len(line.encode("utf-8")) for line in content_lines)

        was_truncated = args.limit is not None and lines_read >= args.limit

        return _ReadResult(
            lines=content_lines, bytes_read=bytes_read, was_truncated=was_truncated
        )
