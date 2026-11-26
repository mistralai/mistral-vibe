"""Terminal backend contract shared by the Session process manager."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
import time
from typing import Literal, Protocol

type ProcessCommandEnvironment = Literal["unix", "git_bash", "powershell"]
type PtyBackend = Literal["posix", "ConPTY", "WinPTY"]


class TerminalBackendError(Exception):
    pass


type TerminalStartStage = Literal["shell_resolution", "unsupported_platform"]


class TerminalStartError(TerminalBackendError):
    """The backend could not start a process, for a reason worth naming."""

    def __init__(self, stage: TerminalStartStage, message: str) -> None:
        super().__init__(message)
        self.stage: TerminalStartStage = stage


class WorkingDirectoryError(TerminalBackendError):
    """The working directory requested for a process is not a directory.

    Raised by a backend that can only check it where the process runs.
    """


class ManagedTerminal(Protocol):
    @property
    def pid(self) -> int | None: ...

    @property
    def pty_backend(self) -> PtyBackend: ...

    @property
    def returncode(self) -> int | None: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int | None: ...

    def group_is_alive(self) -> bool:
        """Whether the root or any process left in its group still runs."""
        ...

    def wait_for_group_exit(self, timeout: float) -> bool:
        """Wait until :meth:`group_is_alive` is false; False on timeout."""
        ...

    def wait_readable(self, timeout_seconds: float) -> bool: ...

    def read(self, size: int) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def close(self) -> None: ...


class TerminalBackend(Protocol):
    command_environment: ProcessCommandEnvironment

    def resolve_shell(self, configured: str | None, env: dict[str, str]) -> str | None:
        """The shell to start processes with.

        None when the backend resolves it where the process runs, at start.
        """
        ...

    def start_terminal(
        self, *, shell: str | None, command: str, cwd: Path, env: dict[str, str]
    ) -> ManagedTerminal: ...

    def stop_terminals(
        self, terminals: Sequence[ManagedTerminal], grace_seconds: float
    ) -> list[bool]:
        """Stop each terminal's process and what it left behind.

        Asks them to end, forces what is left after ``grace_seconds``, and
        waits up to as long again. Returns, for each terminal, whether nothing
        of its process was left.
        """
        ...

    def close(self) -> None:
        """Release what the backend holds once its processes are stopped."""
        ...


def stop_terminals_by_signals(
    terminals: Sequence[ManagedTerminal],
    signals: Sequence[Callable[[ManagedTerminal], None]],
    grace_seconds: float,
) -> list[bool]:
    """Stop terminals by sending each signal in turn to what is still running.

    Waits up to ``grace_seconds`` for the groups to exit after each signal. A
    terminal a signal could not reach is given up at once: it counts as
    stopped only if its root has exited.
    """
    failed: set[int] = set()
    live = list(range(len(terminals)))
    for send in signals:
        for index in live:
            try:
                send(terminals[index])
            except Exception:
                failed.add(index)
        deadline = time.monotonic() + grace_seconds
        live = [index for index in live if index not in failed]
        live = [index for index in live if _group_is_alive(terminals[index])]
        while live and time.monotonic() < deadline:
            time.sleep(0.02)
            live = [index for index in live if _group_is_alive(terminals[index])]
    return [
        index not in live and (index not in failed or _root_exited(terminals[index]))
        for index in range(len(terminals))
    ]


def _group_is_alive(terminal: ManagedTerminal) -> bool:
    try:
        return terminal.group_is_alive()
    except Exception:
        return not _root_exited(terminal)


def _root_exited(terminal: ManagedTerminal) -> bool:
    try:
        return terminal.poll() is not None
    except Exception:
        return False


__all__ = [
    "ManagedTerminal",
    "ProcessCommandEnvironment",
    "PtyBackend",
    "TerminalBackend",
    "TerminalBackendError",
    "TerminalStartError",
    "TerminalStartStage",
    "WorkingDirectoryError",
    "stop_terminals_by_signals",
]
