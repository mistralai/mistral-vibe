"""Native Windows pseudo-terminal backend using pywinpty."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import cast

from mistralai_vibe_local_harness.vibe._processes._backend import (
    ManagedTerminal,
    ProcessCommandEnvironment,
    PtyBackend,
    TerminalBackendError,
)

_POWERSHELL_SHELLS = ("pwsh.exe", "powershell.exe")


class WindowsTerminal:
    def __init__(self, process: object, backend: PtyBackend) -> None:
        self._process = process
        self._backend = backend
        self._read_buffer = bytearray()
        self._returncode: int | None = None

    @property
    def pid(self) -> int | None:
        value = getattr(self._process, "pid", None)
        return value if isinstance(value, int) else None

    @property
    def pty_backend(self) -> PtyBackend:
        return cast(PtyBackend, self._backend)

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def poll(self) -> int | None:
        if self._returncode is not None:
            return self._returncode
        if bool(self._process.isalive()):  # type: ignore[attr-defined]
            return None
        value = self._process.get_exitstatus()  # type: ignore[attr-defined]
        self._returncode = value if isinstance(value, int) else 1
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if (returncode := self.poll()) is not None:
                return returncode
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("Windows PTY", timeout or 0)
            time.sleep(0.05)

    def wait_readable(self, timeout_seconds: float) -> bool:
        if self._read_buffer:
            return True
        deadline = time.monotonic() + max(timeout_seconds, 0)
        while True:
            try:
                output = self._process.read(blocking=False)  # type: ignore[attr-defined]
            except Exception:
                try:
                    reached_eof = bool(self._process.iseof())  # type: ignore[attr-defined]
                except Exception:
                    reached_eof = False
                if not reached_eof and self.poll() is None:
                    raise
                if self.poll() is not None:
                    return False
                output = ""
            if isinstance(output, str) and output:
                self._read_buffer.extend(output.encode("utf-8"))
                return True
            if self.poll() is not None or time.monotonic() >= deadline:
                return False
            time.sleep(min(0.01, max(0, deadline - time.monotonic())))

    def read(self, size: int) -> bytes:
        if not self._read_buffer and not self.wait_readable(0):
            return b""
        chunk = bytes(self._read_buffer[:size])
        del self._read_buffer[:size]
        return chunk

    def write(self, data: bytes) -> int:
        text = data.decode("utf-8")
        self._process.write(text)  # type: ignore[attr-defined]
        return len(data)

    def close(self) -> None:
        try:
            self._process.cancel_io()  # type: ignore[attr-defined]
        except Exception:
            pass


class WindowsTerminalBackend:
    def __init__(self, command_environment: str) -> None:
        if command_environment not in {"git_bash", "powershell"}:
            raise ValueError("Windows terminal requires Git Bash or PowerShell")
        self.command_environment: ProcessCommandEnvironment = cast(
            ProcessCommandEnvironment, command_environment
        )

    def resolve_shell(self, configured: str | None, env: dict[str, str]) -> str:
        if configured is not None:
            resolved = _resolve_executable(configured, env)
            if resolved is None or _shell_family(resolved) != self.command_environment:
                raise TerminalBackendError(
                    "configured shell is missing or has the wrong family"
                )
            return resolved
        if self.command_environment == "git_bash":
            if resolved := _find_git_bash(env):
                return resolved
            raise TerminalBackendError("no Git Bash shell found")
        for candidate in _POWERSHELL_SHELLS:
            if resolved := shutil.which(
                candidate, path=_windows_env(env, "PATH") or ""
            ):
                return resolved
        raise TerminalBackendError("no PowerShell shell found")

    def start_terminal(
        self, *, shell: str, command: str, cwd: Path, env: dict[str, str]
    ) -> WindowsTerminal:
        winpty = importlib.import_module("winpty")
        enums = importlib.import_module("winpty.enums")
        argv = (
            [shell, "-c", command]
            if self.command_environment == "git_bash"
            else [shell, "-NoLogo", "-NoProfile", "-Command", command]
        )
        environment = "\0".join(f"{key}={value}" for key, value in env.items()) + "\0"
        for backend_name, backend_value in (
            ("ConPTY", enums.Backend.ConPTY),
            ("WinPTY", enums.Backend.WinPTY),
        ):
            try:
                process = winpty.PTY(120, 40, backend=backend_value)
            except Exception:
                continue
            command_line = " " + subprocess.list2cmdline(argv[1:])
            try:
                spawned = process.spawn(
                    argv[0], cmdline=command_line, cwd=str(cwd), env=environment
                )
            except Exception as error:
                process.cancel_io()
                raise TerminalBackendError("Windows PTY spawn failed") from error
            if spawned:
                return WindowsTerminal(process, cast(PtyBackend, backend_name))
            process.cancel_io()
        raise TerminalBackendError("no Windows PTY backend could start the process")

    def request_termination(self, terminal: ManagedTerminal) -> None:
        self.force_termination(terminal)

    def force_termination(self, terminal: ManagedTerminal) -> None:
        if not isinstance(terminal, WindowsTerminal):
            raise TerminalBackendError("Windows backend received a foreign terminal")
        if terminal.poll() is not None:
            return
        if terminal.pid is None:
            raise TerminalBackendError("Windows PTY has no process ID")
        result = subprocess.run(
            ["taskkill", "/PID", str(terminal.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            timeout=2,
        )
        if result.returncode != 0 and terminal.poll() is None:
            raise TerminalBackendError(
                "taskkill did not terminate the Windows PTY root"
            )


def _resolve_executable(candidate: str, env: dict[str, str]) -> str | None:
    expanded = Path(candidate).expanduser()
    path_like = (
        os.sep in candidate
        or (os.altsep is not None and os.altsep in candidate)
        or ":" in candidate
    )
    if path_like:
        return str(expanded) if expanded.is_file() else None
    return shutil.which(candidate, path=_windows_env(env, "PATH") or "")


def _find_git_bash(env: dict[str, str]) -> str | None:
    search_path = _windows_env(env, "PATH") or ""
    if candidate := shutil.which("bash.exe", path=search_path):
        normalized = candidate.replace("\\", "/").lower()
        if (
            "/system32/bash.exe" not in normalized
            and "/windowsapps/bash.exe" not in normalized
        ):
            return candidate
    git = shutil.which("git.exe", path=search_path)
    if git is not None:
        git_root = Path(git).parent.parent
        for relative in ("bin/bash.exe", "usr/bin/bash.exe"):
            candidate = git_root / relative
            if candidate.is_file():
                return str(candidate)
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = _windows_env(env, variable)
        if root is None:
            continue
        for relative in ("Git/bin/bash.exe", "Programs/Git/bin/bash.exe"):
            candidate = Path(root) / relative
            if candidate.is_file():
                return str(candidate)
    return None


def _windows_env(env: dict[str, str], name: str) -> str | None:
    folded = name.casefold()
    return next((value for key, value in env.items() if key.casefold() == folded), None)


def _shell_family(path: str) -> str:
    name = Path(path).name.casefold()
    if name in {"bash", "bash.exe"}:
        return "git_bash"
    if name in {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
        return "powershell"
    return "unknown"
