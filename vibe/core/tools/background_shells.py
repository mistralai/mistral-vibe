from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import itertools

from vibe.core.utils import kill_async_subprocess
from vibe.core.utils.io import decode_safe

_MAX_BUFFER_BYTES = 1_000_000


@dataclass
class BackgroundShell:
    id: str
    command: str
    proc: asyncio.subprocess.Process
    _stdout: list[str] = field(default_factory=list)
    _stderr: list[str] = field(default_factory=list)
    _stdout_cursor: int = 0
    _stderr_cursor: int = 0
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.proc.returncode is None

    @property
    def returncode(self) -> int | None:
        return self.proc.returncode

    def append_stdout(self, text: str) -> None:
        self._append(self._stdout, text)

    def append_stderr(self, text: str) -> None:
        self._append(self._stderr, text)

    @staticmethod
    def _append(buffer: list[str], text: str) -> None:
        buffer.append(text)
        total = sum(len(chunk) for chunk in buffer)
        while total > _MAX_BUFFER_BYTES and len(buffer) > 1:
            total -= len(buffer.pop(0))

    def drain_new_output(self) -> tuple[str, str]:
        stdout = "".join(self._stdout)[self._stdout_cursor :]
        stderr = "".join(self._stderr)[self._stderr_cursor :]
        self._stdout_cursor += len(stdout)
        self._stderr_cursor += len(stderr)
        return stdout, stderr

    async def wait(self) -> int:
        await self.proc.wait()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        return self.proc.returncode or 0

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self.running:
            await kill_async_subprocess(self.proc)


class BackgroundShellRegistry:
    def __init__(self) -> None:
        self._shells: dict[str, BackgroundShell] = {}
        self._counter = itertools.count(1)

    def register(
        self, command: str, proc: asyncio.subprocess.Process
    ) -> BackgroundShell:
        shell_id = f"bg_{next(self._counter)}"
        shell = BackgroundShell(id=shell_id, command=command, proc=proc)
        shell._tasks = [
            asyncio.create_task(self._pump(proc.stdout, shell.append_stdout)),
            asyncio.create_task(self._pump(proc.stderr, shell.append_stderr)),
        ]
        self._shells[shell_id] = shell
        return shell

    @staticmethod
    async def _pump(
        stream: asyncio.StreamReader | None, sink: Callable[[str], None]
    ) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            sink(decode_safe(chunk, from_subprocess=True).text)

    def get(self, shell_id: str) -> BackgroundShell | None:
        return self._shells.get(shell_id)

    def list_ids(self) -> list[str]:
        return list(self._shells)

    async def kill(self, shell_id: str) -> bool:
        shell = self._shells.get(shell_id)
        if shell is None:
            return False
        await shell.aclose()
        return True

    def remove(self, shell_id: str) -> None:
        self._shells.pop(shell_id, None)

    async def aclose_all(self) -> None:
        for shell in list(self._shells.values()):
            await shell.aclose()
        self._shells.clear()


_registry: BackgroundShellRegistry | None = None


def get_background_shell_registry() -> BackgroundShellRegistry:
    global _registry
    if _registry is None:
        _registry = BackgroundShellRegistry()
    return _registry
