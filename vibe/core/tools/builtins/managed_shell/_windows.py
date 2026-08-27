from __future__ import annotations

from dataclasses import dataclass
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, cast

from vibe.core.tools.builtins.managed_shell.backend import (
    UNKNOWN_EXIT_CODE,
    ManagedShellBackendCapabilities,
    ManagedShellBackendError,
    ManagedTerminal,
)
from vibe.utils.platform import get_windows_bash_path

WINDOWS_POWERSHELL_DEFAULT_SHELLS: tuple[str, ...] = ("pwsh.exe", "powershell.exe")

WINDOWS_CONPTY_BACKEND = "ConPTY"
WINDOWS_WINPTY_BACKEND = "WinPTY"
_WINPTY_POLL_SECONDS = 0.01
_TASKKILL_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class WindowsShellSpec:
    executable: str
    family: str


def pywinpty_available() -> bool:
    try:
        importlib.import_module("winpty")
    except ImportError:
        return False
    return True


def _resolve_executable(candidate: str) -> str | None:
    expanded = Path(candidate).expanduser()
    path_like = (
        os.sep in candidate
        or (os.altsep is not None and os.altsep in candidate)
        or ":" in candidate
    )
    if path_like:
        if expanded.is_file():
            return str(expanded)
        return None
    return shutil.which(candidate)


def resolve_git_bash_shell_spec(
    requested: str | None, configured: str | None
) -> WindowsShellSpec:
    source = requested or configured
    if source:
        resolved = _resolve_executable(source)
        if resolved:
            family = _shell_family_from_executable(resolved)
            if family != "bash":
                raise ManagedShellBackendError(
                    f"Git Bash shell override must resolve to bash.exe, got {source}"
                )
            return WindowsShellSpec(executable=resolved, family=family)
        kind = "requested" if requested else "configured"
        raise ManagedShellBackendError(f"{kind} shell is not executable: {source}")

    if bash := get_windows_bash_path():
        return WindowsShellSpec(executable=bash, family="bash")

    raise ManagedShellBackendError("no Git Bash shell found; expected bash.exe")


def resolve_git_bash_shell(requested: str | None, configured: str | None) -> str:
    return resolve_git_bash_shell_spec(requested, configured).executable


def git_bash_shell_available() -> bool:
    try:
        resolve_git_bash_shell(None, None)
    except ManagedShellBackendError:
        return False
    return True


def resolve_powershell_shell_spec(
    requested: str | None, configured: str | None
) -> WindowsShellSpec:
    source = requested or configured
    if source:
        resolved = _resolve_executable(source)
        if resolved:
            family = _shell_family_from_executable(resolved)
            if family != "powershell":
                raise ManagedShellBackendError(
                    "PowerShell shell override must resolve to pwsh.exe "
                    f"or powershell.exe, got {source}"
                )
            return WindowsShellSpec(executable=resolved, family=family)
        kind = "requested" if requested else "configured"
        raise ManagedShellBackendError(f"{kind} shell is not executable: {source}")

    for candidate in WINDOWS_POWERSHELL_DEFAULT_SHELLS:
        if resolved := _resolve_executable(candidate):
            return WindowsShellSpec(
                executable=resolved, family=_shell_family_from_executable(candidate)
            )

    raise ManagedShellBackendError(
        "no PowerShell shell found; expected pwsh.exe or powershell.exe"
    )


def resolve_windows_shell_spec(
    requested: str | None, configured: str | None
) -> WindowsShellSpec:
    return resolve_powershell_shell_spec(requested, configured)


def resolve_powershell_shell(requested: str | None, configured: str | None) -> str:
    return resolve_powershell_shell_spec(requested, configured).executable


def resolve_windows_shell(requested: str | None, configured: str | None) -> str:
    return resolve_powershell_shell(requested, configured)


def build_windows_shell_argv(shell: str, command: str) -> list[str]:
    family = _shell_family_from_executable(shell)
    match family:
        case "powershell":
            return [shell, "-NoLogo", "-NoProfile", "-Command", command]
        case "bash":
            return [shell, "-c", command]
        case _:
            return [shell, command]


def _build_windows_environment(env: dict[str, str]) -> str:
    for key, value in env.items():
        if "\0" in key or "\0" in value:
            raise ManagedShellBackendError(
                f"environment variable {key!r} contains an embedded NUL"
            )
        if "=" in key:
            raise ManagedShellBackendError(
                f"environment variable name {key!r} contains '='"
            )
    return "\0".join(f"{key}={value}" for key, value in env.items()) + "\0"


def _shell_family_from_executable(executable: str) -> str:
    name = Path(executable).name.lower()
    if name in {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
        return "powershell"
    if name in {"cmd", "cmd.exe"}:
        return "cmd"
    if name in {"bash", "bash.exe"}:
        return "bash"
    return "custom"


def _load_winpty() -> tuple[Any, Any]:
    try:
        winpty = importlib.import_module("winpty")
        enums = importlib.import_module("winpty.enums")
    except ImportError as exc:
        raise ManagedShellBackendError(
            "managed Windows shell requires pywinpty"
        ) from exc
    return winpty, enums


class WindowsManagedTerminal:
    def __init__(self, process: Any, pty_backend: str) -> None:
        self._process = process
        self._read_buffer = bytearray()
        self._final_returncode: int | None = None
        self.pid = cast(int | None, getattr(process, "pid", None))
        self.pty_backend = pty_backend

    @property
    def returncode(self) -> int | None:
        if self._final_returncode is not None:
            return self._final_returncode
        if self._process is None:
            return None
        exit_status = self._process.get_exitstatus()
        if isinstance(exit_status, int):
            return exit_status
        return None

    def poll(self) -> int | None:
        if self._process is None:
            return self._final_returncode
        is_alive = bool(self._process.isalive())
        if is_alive:
            return None
        self._final_returncode = self.returncode
        if self._final_returncode is None:
            self._final_returncode = UNKNOWN_EXIT_CODE
        return self._final_returncode

    def wait(self, timeout: float | None = None) -> int | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            returncode = self.poll()
            if returncode is not None:
                return returncode
            if deadline is not None and time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("windows shell", timeout or 0)
            time.sleep(0.05)

    def wait_readable(self, timeout_seconds: float) -> bool:
        if self._read_buffer:
            return True
        if self._process is None:
            return False

        deadline = time.monotonic() + max(timeout_seconds, 0)
        while True:
            try:
                output = cast(str, self._process.read(blocking=False))
            except Exception:
                try:
                    reached_eof = bool(self._process.iseof())
                except Exception:
                    reached_eof = False
                if not reached_eof and self.poll() is None:
                    raise
                if self.poll() is not None:
                    break
                # EOF reached while the process still reports alive: fall through
                # to the timeout wait below so the reader polls for exit instead
                # of spinning on repeated EOF reads.
                output = ""
            if output:
                self._read_buffer.extend(output.encode("utf-8"))
                return True
            if self.poll() is not None:
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(_WINPTY_POLL_SECONDS, remaining))
        return False

    def read(self, size: int) -> bytes:
        if not self._read_buffer and not self.wait_readable(0):
            return b""
        chunk = bytes(self._read_buffer[:size])
        del self._read_buffer[:size]
        return chunk

    def write(self, data: bytes) -> int:
        if self._process is None:
            raise EOFError("PTY is closed")
        text = data.decode("utf-8", errors="replace")
        # pywinpty/ConPTY may report 0 bytes even when the write succeeded, so a
        # non-raising write is treated as having accepted the whole payload.
        self._process.write(text)
        return len(data)

    def close(self) -> None:
        if self._process is None:
            return
        try:
            self._final_returncode = self.returncode
        except Exception:
            self._final_returncode = None
        if self._final_returncode is None:
            self._final_returncode = UNKNOWN_EXIT_CODE
        process = self._process
        self._process = None
        try:
            process.cancel_io()
        except Exception:
            pass

    def cancel_io(self) -> None:
        if self._process is None:
            return
        try:
            self._process.cancel_io()
        except Exception:
            pass


class WindowsManagedShellBackend:
    capabilities: ManagedShellBackendCapabilities

    def __init__(self, *, shell_family: str = "powershell") -> None:
        self.shell_family = shell_family
        self.capabilities = ManagedShellBackendCapabilities(
            interactive_tty=True,
            control_sequences=True,
            process_group_kill=True,
            shell_family=shell_family,
        )

    def resolve_shell(self, requested: str | None, configured: str | None) -> str:
        match self.shell_family:
            case "git_bash":
                return resolve_git_bash_shell(requested, configured)
            case "powershell" | "windows":
                return resolve_powershell_shell(requested, configured)
            case _:
                raise ManagedShellBackendError(
                    f"unknown Windows managed shell family: {self.shell_family}"
                )

    def start_terminal(
        self, *, shell: str, command: str, cwd: Path, env: dict[str, str]
    ) -> WindowsManagedTerminal:
        winpty, enums = _load_winpty()
        argv = build_windows_shell_argv(shell, command)
        environment = _build_windows_environment(env)
        errors: list[str] = []
        for backend_name, backend_value in (
            (WINDOWS_CONPTY_BACKEND, enums.Backend.ConPTY),
            (WINDOWS_WINPTY_BACKEND, enums.Backend.WinPTY),
        ):
            try:
                process = winpty.PTY(120, 40, backend=backend_value)
                command_line = (
                    " " + subprocess.list2cmdline(argv[1:]) if len(argv) > 1 else None
                )
                spawned = process.spawn(
                    argv[0], cmdline=command_line, cwd=str(cwd), env=environment
                )
                if not spawned:
                    raise ManagedShellBackendError("PTY process did not start")
            except Exception as exc:
                errors.append(f"{backend_name}: {exc}")
                continue
            return WindowsManagedTerminal(process=process, pty_backend=backend_name)

        raise ManagedShellBackendError(
            "failed to start Windows PTY backend: " + "; ".join(errors)
        )

    def request_termination(self, terminal: ManagedTerminal) -> None:
        self.force_terminate_terminal(
            terminal, timeout_seconds=_TASKKILL_TIMEOUT_SECONDS
        )

    def force_terminate_terminal(
        self, terminal: ManagedTerminal, *, timeout_seconds: float
    ) -> None:
        if terminal.poll() is not None:
            return
        windows_terminal = cast(WindowsManagedTerminal, terminal)
        self._taskkill_process_tree(windows_terminal, timeout_seconds=timeout_seconds)

    @staticmethod
    def _taskkill_process_tree(
        terminal: WindowsManagedTerminal, *, timeout_seconds: float
    ) -> None:
        if terminal.pid is None:
            raise ManagedShellBackendError(
                "Windows PTY does not expose a process ID for forced termination"
            )
        command = ["taskkill", "/PID", str(terminal.pid), "/T", "/F"]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=max(timeout_seconds, 1.0),
            )
        except subprocess.TimeoutExpired as exc:
            raise ManagedShellBackendError("taskkill timed out") from exc
        finally:
            terminal.cancel_io()
        if result.returncode != 0 and terminal.poll() is None:
            detail = result.stderr.strip() or result.stdout.strip()
            raise ManagedShellBackendError(
                f"taskkill failed with exit code {result.returncode}: {detail}"
            )
