from __future__ import annotations

from pathlib import Path

import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, InvokeContext
from vibe.core.tools.builtins.bash import Bash, BashArgs, BashToolConfig
from vibe.core.tools.builtins.edit import Edit, EditArgs, EditConfig
from vibe.core.tools.builtins.read_file import (
    ReadFile,
    ReadFileArgs,
    ReadFileConfig,
    ReadFileState,
)
from vibe.core.tools.builtins.write_file import (
    WriteFile,
    WriteFileArgs,
    WriteFileConfig,
)
from vibe.core.tools.io_port import ShellCommandRequest, ShellCommandResult
from vibe.utils.io import BoundedReadResult, ReadSafeResult, normalize_newlines


class FakeToolIO:
    supports_read = True
    supports_write = True
    supports_terminal = True

    def __init__(self, files: dict[Path, str] | None = None) -> None:
        self.files = files or {}
        self.shell_requests: list[ShellCommandRequest] = []

    async def read_lines(
        self, path: Path, *, start_line: int, limit: int, max_bytes: int
    ) -> BoundedReadResult:
        del max_bytes
        lines = self.files[path].splitlines()
        selected = lines[start_line - 1 : start_line - 1 + limit]
        return BoundedReadResult(selected, len(lines), len(selected) < len(lines))

    async def read_text(self, path: Path) -> ReadSafeResult:
        text, newline = normalize_newlines(self.files[path])
        return ReadSafeResult(text, "utf-8", newline)

    async def write_text(
        self,
        path: Path,
        content: str,
        *,
        encoding: str = "utf-8",
        newline: str | None = None,
    ) -> None:
        del encoding
        self.files[path] = content.replace("\n", newline) if newline else content

    async def run_shell(self, request: ShellCommandRequest) -> ShellCommandResult:
        self.shell_requests.append(request)
        return ShellCommandResult(stdout="host output", stderr="", returncode=0)


@pytest.mark.asyncio
async def test_canonical_file_tools_use_host_io(tmp_path: Path) -> None:
    read_path = (tmp_path / "read.txt").resolve()
    edit_path = (tmp_path / "edit.txt").resolve()
    write_path = (tmp_path / "write.txt").resolve()
    read_path.touch()
    edit_path.touch()
    tool_io = FakeToolIO({read_path: "host line", edit_path: "before\r\n"})
    context = InvokeContext(tool_call_id="call", tool_io=tool_io)

    read = ReadFile(lambda: ReadFileConfig(), ReadFileState())
    read_result = await collect_result(
        read.run(ReadFileArgs(file_path=str(read_path)), context)
    )
    assert "host line" in read_result.content

    write = WriteFile(lambda: WriteFileConfig(), BaseToolState())
    await collect_result(
        write.run(WriteFileArgs(file_path=str(write_path), content="created"), context)
    )
    assert tool_io.files[write_path] == "created"

    edit = Edit(lambda: EditConfig(), BaseToolState())
    await collect_result(
        edit.run(
            EditArgs(file_path=str(edit_path), old_string="before", new_string="after"),
            context,
        )
    )
    assert tool_io.files[edit_path] == "after\r\n"


@pytest.mark.asyncio
async def test_canonical_bash_uses_host_terminal() -> None:
    tool_io = FakeToolIO()
    bash = Bash(lambda: BashToolConfig(), BaseToolState())

    result = await collect_result(
        bash.run(
            BashArgs(command="echo local"),
            InvokeContext(
                tool_call_id="shell-1", session_id="session-1", tool_io=tool_io
            ),
        )
    )

    assert result.stdout == "host output"
    assert tool_io.shell_requests[0].tool_call_id == "shell-1"
    assert tool_io.shell_requests[0].session_id == "session-1"
