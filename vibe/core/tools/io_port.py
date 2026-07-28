from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from vibe.utils.io import BoundedReadResult, ReadSafeResult


@dataclass(frozen=True, slots=True)
class ShellCommandRequest:
    session_id: str
    tool_call_id: str
    command: str
    cwd: Path
    timeout: float
    max_output_bytes: int
    args: list[str] | None = None
    env: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class ShellCommandResult:
    stdout: str
    stderr: str
    returncode: int


class ToolIOPort(Protocol):
    @property
    def supports_read(self) -> bool: ...

    @property
    def supports_write(self) -> bool: ...

    @property
    def supports_terminal(self) -> bool: ...

    async def read_lines(
        self, path: Path, *, start_line: int, limit: int, max_bytes: int
    ) -> BoundedReadResult: ...

    async def read_text(self, path: Path) -> ReadSafeResult: ...

    async def write_text(
        self,
        path: Path,
        content: str,
        *,
        encoding: str = "utf-8",
        newline: str | None = None,
    ) -> None: ...

    async def run_shell(self, request: ShellCommandRequest) -> ShellCommandResult: ...
