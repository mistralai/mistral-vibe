from __future__ import annotations

from collections.abc import Callable
import importlib
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import cast

import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, InvokeContext, ToolPermission
from vibe.core.tools.builtins import (
    bash as bash_module,
    experimental_bash as experimental_bash_module,
    git_bash as git_bash_module,
    windows_shell as windows_shell_module,
)
from vibe.core.tools.builtins.experimental_bash import (
    BashOutputArgs,
    BashOutputConfig,
    BashOutputResult,
    BashSessionsArgs,
    BashSessionsConfig,
    BashSessionsResult,
    BashStdinArgs,
    BashStdinConfig,
    ExperimentalBashArgs,
    ExperimentalBashToolConfig,
    SessionNotFoundError,
    TerminalSessionManager,
)
from vibe.core.tools.builtins.git_bash import (
    ExperimentalGitBash,
    GitBash,
    GitBashArgs,
    GitBashLogFile,
    GitBashOutput,
    GitBashSessions,
    GitBashStdin,
    GitBashToolConfig,
)
from vibe.core.tools.builtins.managed_shell import _windows
from vibe.core.tools.builtins.managed_shell.backend import (
    ManagedShellBackend,
    windows_managed_shell_supported,
)
from vibe.core.tools.builtins.windows_shell import (
    ExperimentalWindowsShell,
    WindowsShell,
    WindowsShellArgs,
    WindowsShellLogFile,
    WindowsShellOutput,
    WindowsShellSessions,
    WindowsShellStdin,
    WindowsShellToolConfig,
    _split_windows_command_parts,
)
from vibe.core.tools.io_port import ShellCommandRequest, ShellCommandResult, ToolIOPort
from vibe.core.tools.permissions import PermissionContext
from vibe.core.tools.terminal_runtime import TerminalRuntime
from vibe.core.tools.ui import ToolUIDataAdapter
from vibe.core.types import ToolCallEvent, ToolResultEvent
from vibe.core.utils import is_windows
from vibe.core.workspace import Workspace
from vibe.utils import paths


class _UnusedBackend:
    def request_termination(self, terminal) -> None:
        pass

    def force_terminate_terminal(self, terminal, *, timeout_seconds: float) -> None:
        pass


class _FakeTerminalToolIO:
    supports_terminal = True

    def __init__(self) -> None:
        self.requests: list[ShellCommandRequest] = []

    async def run_shell(self, request: ShellCommandRequest) -> ShellCommandResult:
        self.requests.append(request)
        return ShellCommandResult(
            stdout="client stdout", stderr="client stderr", returncode=0
        )


class _FakeWinPty:
    def __init__(
        self, outputs: list[str], *, alive: bool = True, exit_status: int | None = 0
    ) -> None:
        self.pid = 42
        self.outputs = outputs
        self.alive = alive
        self.exit_status = exit_status
        self.read_blocking_values: list[bool] = []
        self.writes: list[str] = []
        self.cancel_io_calls = 0

    def read(self, *, blocking: bool) -> str:
        self.read_blocking_values.append(blocking)
        return self.outputs.pop(0) if self.outputs else ""

    def isalive(self) -> bool:
        return self.alive

    def get_exitstatus(self) -> int | None:
        return None if self.alive else self.exit_status

    def write(self, value: str) -> int:
        self.writes.append(value)
        return len(value)

    def cancel_io(self) -> None:
        self.cancel_io_calls += 1


def test_windows_shell_resolver_prefers_pwsh_then_powershell(monkeypatch):
    calls: list[str] = []
    resolved = {"powershell.exe": "C:/Windows/System32/powershell.exe"}

    def fake_resolve(candidate: str) -> str | None:
        calls.append(candidate)
        return resolved.get(candidate)

    monkeypatch.setattr(_windows, "_resolve_executable", fake_resolve)

    spec = _windows.resolve_windows_shell_spec(None, None)

    assert spec.executable == "C:/Windows/System32/powershell.exe"
    assert spec.family == "powershell"
    assert calls == ["pwsh.exe", "powershell.exe"]


def test_git_bash_normalizes_msys_drive_paths_before_workdir_check(
    tmp_path, monkeypatch
):
    # Compared as paths, not strings: str(WindowsPath) renders backslashes.
    seen_paths: list[Path] = []
    inside = {tmp_path, Path("C:/repo/file.txt")}

    def is_within_workdir(path: str, **_kwargs) -> bool:
        seen_paths.append(Path(path))
        return Path(path) in inside

    monkeypatch.setattr(experimental_bash_module, "is_windows", lambda: True)
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    monkeypatch.setattr(
        experimental_bash_module, "is_path_within_workdir", is_within_workdir
    )

    outside_dirs = experimental_bash_module._collect_outside_dirs(
        ["cat /c/repo/file.txt"],
        command_cwd=tmp_path,
        workspace=Workspace.for_session(tmp_path),
        scratchpad_dir=None,
    )

    assert outside_dirs == set()
    assert Path("C:/repo/file.txt") in seen_paths


def test_managed_git_bash_env_uses_posix_defaults(monkeypatch):
    monkeypatch.delenv("TERM", raising=False)
    manager = TerminalSessionManager(
        backend=cast(ManagedShellBackend, _UnusedBackend()),
        shell_family="git_bash",
        session_prefix="git_bash",
    )

    env = manager._build_env(None)

    assert env["TERM"] == "xterm-256color"
    assert env["GIT_PAGER"] == "cat"
    assert env["PAGER"] == "cat"
    assert env["LESS"] == "-FX"
    assert env["DEBIAN_FRONTEND"] == "noninteractive"


@pytest.mark.parametrize("shell_family", ["powershell", "windows"])
def test_managed_powershell_env_uses_windows_defaults(shell_family, monkeypatch):
    for name in ("TERM", "COLUMNS", "LINES", "LESS", "DEBIAN_FRONTEND"):
        monkeypatch.delenv(name, raising=False)
    manager = TerminalSessionManager(
        backend=cast(ManagedShellBackend, _UnusedBackend()),
        shell_family=shell_family,
        session_prefix="powershell",
    )

    env = manager._build_env({"PAGER": "custom-pager"})

    assert env["GIT_PAGER"] == "more"
    assert env["PAGER"] == "custom-pager"
    assert "TERM" not in env
    assert "COLUMNS" not in env
    assert "LINES" not in env
    assert "LESS" not in env
    assert "DEBIAN_FRONTEND" not in env


def test_windows_shell_resolver_rejects_cmd_override(monkeypatch):
    monkeypatch.setattr(
        _windows, "_resolve_executable", lambda candidate: "C:/Windows/System32/cmd.exe"
    )

    with pytest.raises(
        _windows.ManagedShellBackendError, match="PowerShell shell override"
    ):
        _windows.resolve_windows_shell_spec("cmd.exe", None)


def test_git_bash_resolver_uses_windows_bash_path(monkeypatch):
    monkeypatch.setattr(
        _windows, "get_windows_bash_path", lambda: "C:/Program Files/Git/bin/bash.exe"
    )

    spec = _windows.resolve_git_bash_shell_spec(None, None)

    assert spec.executable == "C:/Program Files/Git/bin/bash.exe"
    assert spec.family == "bash"


def test_git_bash_resolver_rejects_powershell_override(monkeypatch):
    monkeypatch.setattr(
        _windows,
        "_resolve_executable",
        lambda candidate: "C:/Program Files/PowerShell/7/pwsh.exe",
    )

    with pytest.raises(_windows.ManagedShellBackendError, match="Git Bash"):
        _windows.resolve_git_bash_shell_spec("pwsh.exe", None)


@pytest.mark.parametrize(
    ("shell", "expected"),
    [
        (
            "C:/Program Files/PowerShell/7/pwsh.exe",
            [
                "C:/Program Files/PowerShell/7/pwsh.exe",
                "-NoLogo",
                "-NoProfile",
                "-Command",
                "echo hello",
            ],
        ),
        (
            "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
            [
                "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-Command",
                "echo hello",
            ],
        ),
        (
            "C:/Program Files/Git/bin/bash.exe",
            ["C:/Program Files/Git/bin/bash.exe", "-c", "echo hello"],
        ),
    ],
)
def test_windows_shell_argv_builder(shell: str, expected: list[str]):
    assert _windows.build_windows_shell_argv(shell, "echo hello") == expected


def test_windows_shell_tokenizer_preserves_backslash_paths():
    tokens = experimental_bash_module._split_command_tokens(
        r"type C:\Users\victim\secret.txt", preserve_backslashes=True
    )

    assert tokens == ["type", r"C:\Users\victim\secret.txt"]


def test_pywinpty_availability_handles_missing_import(monkeypatch):
    real_import_module = importlib.import_module

    def fake_import_module(name: str):
        if name == "winpty":
            raise ImportError("missing")
        return real_import_module(name)

    monkeypatch.setattr(_windows.importlib, "import_module", fake_import_module)

    assert _windows.pywinpty_available() is False


def test_windows_managed_terminal_polls_winpty_without_socket_select():
    process = _FakeWinPty(["hello"])
    terminal = _windows.WindowsManagedTerminal(process, _windows.WINDOWS_CONPTY_BACKEND)

    assert terminal.wait_readable(0)
    assert terminal.read(2) == b"he"
    assert terminal.read(10) == b"llo"
    assert process.read_blocking_values == [False]


def test_windows_managed_terminal_stops_polling_after_process_exit():
    process = _FakeWinPty([], alive=False)
    terminal = _windows.WindowsManagedTerminal(process, _windows.WINDOWS_WINPTY_BACKEND)

    assert not terminal.wait_readable(0)
    assert terminal.poll() == 0
    assert process.read_blocking_values == [False]


def test_windows_managed_terminal_reports_unknown_exit_status_as_failure():
    process = _FakeWinPty([], alive=False, exit_status=None)
    terminal = _windows.WindowsManagedTerminal(process, _windows.WINDOWS_WINPTY_BACKEND)

    assert terminal.poll() == 1
    assert terminal.returncode == 1
    assert terminal.wait() == 1

    terminal.close()

    assert terminal.poll() == 1
    assert terminal.returncode == 1


class _ZeroWriteWinPty:
    def __init__(self) -> None:
        self.pid = 7
        self.writes: list[str] = []

    def write(self, value: str) -> int:
        self.writes.append(value)
        return 0

    def isalive(self) -> bool:
        return True

    def get_exitstatus(self) -> int | None:
        return None


def test_windows_managed_terminal_write_reports_full_length_despite_zero_return():
    process = _ZeroWriteWinPty()
    terminal = _windows.WindowsManagedTerminal(process, _windows.WINDOWS_CONPTY_BACKEND)

    assert terminal.write(b"echo hi\n") == len(b"echo hi\n")
    assert process.writes == ["echo hi\n"]


def test_windows_managed_terminal_write_reports_input_byte_count_for_non_utf8():
    process = _ZeroWriteWinPty()
    terminal = _windows.WindowsManagedTerminal(process, _windows.WINDOWS_CONPTY_BACKEND)

    payload = b"\xff\xfe\x00"
    assert terminal.write(payload) == len(payload)


class _EofWhileAliveWinPty:
    def __init__(self, exit_after_reads: int) -> None:
        self.pid = 9
        self.exit_after_reads = exit_after_reads
        self.read_calls = 0

    def read(self, *, blocking: bool) -> str:
        self.read_calls += 1
        raise OSError("eof")

    def iseof(self) -> bool:
        return True

    def isalive(self) -> bool:
        return self.read_calls < self.exit_after_reads

    def get_exitstatus(self) -> int | None:
        return 0


def test_windows_managed_terminal_wait_readable_polls_for_exit_on_eof():
    process = _EofWhileAliveWinPty(exit_after_reads=3)
    terminal = _windows.WindowsManagedTerminal(process, _windows.WINDOWS_CONPTY_BACKEND)

    assert terminal.wait_readable(1.0) is False
    # Must poll for exit rather than returning after a single EOF read (spin).
    assert process.read_calls >= 2


def test_build_windows_environment_joins_pairs():
    assert _windows._build_windows_environment({"A": "1", "B": "2"}) == "A=1\0B=2\0"


def test_build_windows_environment_rejects_embedded_nul():
    with pytest.raises(_windows.ManagedShellBackendError, match="NUL"):
        _windows._build_windows_environment({"FOO": "a\0BAR=b"})


def test_build_windows_environment_rejects_equals_in_key():
    with pytest.raises(_windows.ManagedShellBackendError, match="'='"):
        _windows._build_windows_environment({"A=B": "1"})


def test_windows_backend_requests_process_tree_termination(monkeypatch):
    process = _FakeWinPty([])
    terminal = _windows.WindowsManagedTerminal(process, _windows.WINDOWS_CONPTY_BACKEND)
    calls: list[list[str]] = []

    def fake_taskkill(command, **kwargs):
        calls.append(command)
        process.alive = False
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(_windows.subprocess, "run", fake_taskkill)

    _windows.WindowsManagedShellBackend().request_termination(terminal)

    assert process.writes == []
    assert calls == [["taskkill", "/PID", "42", "/T", "/F"]]
    assert process.cancel_io_calls == 1


def test_windows_backend_force_termination_uses_taskkill(monkeypatch):
    process = _FakeWinPty([])
    terminal = _windows.WindowsManagedTerminal(process, _windows.WINDOWS_CONPTY_BACKEND)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_taskkill(command, **kwargs):
        calls.append((command, kwargs))
        process.alive = False
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(_windows.subprocess, "run", fake_taskkill)

    _windows.WindowsManagedShellBackend().force_terminate_terminal(
        terminal, timeout_seconds=0
    )

    assert process.writes == []
    assert calls == [
        (
            ["taskkill", "/PID", "42", "/T", "/F"],
            {"check": False, "capture_output": True, "text": True, "timeout": 1.0},
        )
    ]
    assert process.cancel_io_calls == 1


def test_windows_backend_reports_failed_taskkill(monkeypatch):
    process = _FakeWinPty([])
    terminal = _windows.WindowsManagedTerminal(process, _windows.WINDOWS_CONPTY_BACKEND)
    monkeypatch.setattr(
        _windows.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 5, stdout="", stderr="Access is denied"
        ),
    )

    with pytest.raises(_windows.ManagedShellBackendError, match="Access is denied"):
        _windows.WindowsManagedShellBackend().force_terminate_terminal(
            terminal, timeout_seconds=0
        )

    assert process.cancel_io_calls == 1


def test_windows_backend_starts_low_level_winpty_for_nonblocking_reads(
    monkeypatch, tmp_path
):
    created: list[object] = []

    class FakeRawWinPty:
        pid = 42

        def __init__(self, columns: int, rows: int, *, backend: object) -> None:
            self.columns = columns
            self.rows = rows
            self.backend = backend
            self.spawn_args: tuple[str, str | None, str, str] | None = None
            created.append(self)

        def spawn(
            self, executable: str, *, cmdline: str | None, cwd: str, env: str
        ) -> bool:
            self.spawn_args = (executable, cmdline, cwd, env)
            return True

    class FakeWinPtyModule:
        PTY = FakeRawWinPty

    class FakeBackend:
        ConPTY = "conpty"
        WinPTY = "winpty"

    class FakeEnums:
        Backend = FakeBackend

    monkeypatch.setattr(_windows, "_load_winpty", lambda: (FakeWinPtyModule, FakeEnums))
    backend = _windows.WindowsManagedShellBackend()

    terminal = backend.start_terminal(
        shell="pwsh.exe", command="echo hello", cwd=tmp_path, env={"CUSTOM": "value"}
    )

    assert terminal.pty_backend == _windows.WINDOWS_CONPTY_BACKEND
    assert len(created) == 1
    process = created[0]
    assert isinstance(process, FakeRawWinPty)
    assert (process.columns, process.rows, process.backend) == (120, 40, "conpty")
    assert process.spawn_args == (
        "pwsh.exe",
        ' -NoLogo -NoProfile -Command "echo hello"',
        str(tmp_path),
        "CUSTOM=value\0",
    )


def test_windows_shell_manager_missing_session_error_uses_windows_name():
    manager = TerminalSessionManager(
        backend=cast(ManagedShellBackend, _UnusedBackend()),
        shell_family="powershell",
        session_prefix="powershell",
    )

    with pytest.raises(SessionNotFoundError) as exc:
        manager.info("powershell_missing")

    message = str(exc.value)
    assert "unknown powershell session" in message
    assert "bash" not in message


def test_windows_shell_stdin_translates_text_newlines_to_enter():
    tool = WindowsShellStdin(
        config_getter=lambda: BashStdinConfig(), state=BaseToolState()
    )

    payload = tool._build_payload(
        BashStdinArgs(session_id="powershell_1", text="first\nsecond\r\n")
    )

    assert payload == b"first\rsecond\r"


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (BashStdinArgs(session_id="powershell_1", control=["ctrl_c"]), b"\x03"),
        (BashStdinArgs(session_id="powershell_1", bytes_base64="AAE="), b"\x00\x01"),
    ],
)
def test_windows_shell_stdin_preserves_non_text_payloads(args, expected):
    tool = WindowsShellStdin(
        config_getter=lambda: BashStdinConfig(), state=BaseToolState()
    )

    assert tool._build_payload(args) == expected


def test_windows_shell_inherited_ui_text_does_not_say_bash():
    assert WindowsShellOutput.get_status_text() == "Polling powershell session"
    assert WindowsShellStdin.get_status_text() == "Sending powershell input"
    assert WindowsShellSessions.get_status_text() == "Managing powershell sessions"
    assert WindowsShellLogFile.get_status_text() == "Working with powershell log"

    adapter = ToolUIDataAdapter(WindowsShellSessions)
    call = adapter.get_call_display(
        ToolCallEvent(
            tool_call_id="call",
            tool_name="powershell_sessions",
            tool_class=WindowsShellSessions,
            args=BashSessionsArgs(action="kill", session_id="powershell_1"),
        )
    )
    inspected = adapter.get_result_display(
        ToolResultEvent(
            tool_call_id="call",
            tool_name="powershell_sessions",
            tool_class=WindowsShellSessions,
            result=BashSessionsResult(action="inspect"),
        )
    )
    reset = adapter.get_result_display(
        ToolResultEvent(
            tool_call_id="call",
            tool_name="powershell_sessions",
            tool_class=WindowsShellSessions,
            result=BashSessionsResult(action="reset", sessions=[]),
        )
    )

    assert call.summary == "Killing one powershell session powershell_1"
    assert call.verb == "Killing"
    assert call.message == "one powershell session powershell_1"
    assert inspected.verb == "Inspected"
    assert inspected.message == "powershell session"
    assert reset.verb == "Reset"
    assert reset.message == "all powershell sessions; stopped 0 sessions"
    assert "bash" not in inspected.message
    assert "bash" not in reset.message


def test_windows_shell_permission_requires_approval_for_outside_cwd(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    monkeypatch.chdir(workdir)
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["dir"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command="dir", cwd=str(outside))
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


def test_windows_shell_splits_single_ampersand_outside_quotes():
    assert _split_windows_command_parts("dir & del secrets.txt") == [
        "dir",
        "del secrets.txt",
    ]
    assert _split_windows_command_parts('echo "a & b" & dir') == ['echo "a & b"', "dir"]
    assert _split_windows_command_parts("echo a ^& b & dir") == ["echo a ^& b", "dir"]


def test_windows_shell_splits_newlines_outside_quotes():
    assert _split_windows_command_parts("dir\ndel secrets.txt") == [
        "dir",
        "del secrets.txt",
    ]
    assert _split_windows_command_parts('echo "a\nb"\ndir') == ['echo "a\nb"', "dir"]


def test_windows_shell_extracts_nested_powershell_commands():
    assert _split_windows_command_parts("echo $(Get-Date; Remove-Item secret.txt)") == [
        "echo $(Get-Date; Remove-Item secret.txt)",
        "Get-Date",
        "Remove-Item secret.txt",
    ]
    assert _split_windows_command_parts('echo "prefix $(Get-Date)"; Get-Location') == [
        'echo "prefix $(Get-Date)"',
        "Get-Location",
        "Get-Date",
    ]
    assert _split_windows_command_parts("echo '$(Remove-Item secret.txt)'") == [
        "echo '$(Remove-Item secret.txt)'"
    ]
    assert _split_windows_command_parts("& { Get-Date; cmd /k }") == [
        "{ Get-Date; cmd /k }",
        "Get-Date",
        "cmd /k",
    ]
    assert _split_windows_command_parts("& (Get-Date; cmd /k)") == [
        "(Get-Date; cmd /k)",
        "Get-Date",
        "cmd /k",
    ]
    assert _split_windows_command_parts("echo @{Name = 'safe'}") == [
        "echo @{Name = 'safe'}"
    ]


def test_windows_shell_nested_commands_are_all_allowlisted():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["echo", "get-date"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command="echo $(Get-Date)"))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ALWAYS


def test_windows_shell_nested_denylisted_command_is_rejected():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            allowlist=["echo"], denylist=["remove-item"]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r"echo $(Remove-Item C:\outside\secret.txt)")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER
    assert "Remove-Item" in (permission.reason or "")


@pytest.mark.parametrize(
    ("pattern", "command"),
    [
        ("remove-item", "rm victim.txt"),
        ("remove-item", "del victim.txt"),
        ("remove-item", "erase victim.txt"),
        ("remove-item", "ri victim.txt"),
        ("remove-item", "rd victim.txt"),
        ("get-content", "gc file.txt"),
        ("get-content", "cat file.txt"),
        ("get-content", "type file.txt"),
        ("get-childitem", "gci ."),
        ("get-childitem", "dir ."),
        ("get-childitem", "ls ."),
    ],
)
def test_windows_shell_denylist_resolves_powershell_aliases(pattern, command):
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            permission=ToolPermission.ALWAYS, denylist=[pattern]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER


@pytest.mark.parametrize(
    ("pattern", "command"),
    [
        ("rm", "Remove-Item victim.txt"),
        ("gc", "Get-Content file.txt"),
        ("gci", "Get-ChildItem ."),
    ],
)
def test_windows_shell_denylist_canonicalizes_alias_patterns(pattern, command):
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            permission=ToolPermission.ALWAYS, denylist=[pattern]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER


def test_windows_shell_denylist_resolves_alias_after_call_operator():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            permission=ToolPermission.ALWAYS, denylist=["remove-item"]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command="& rm victim.txt"))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER


def test_windows_shell_alias_expansion_does_not_apply_to_explicit_executable():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            permission=ToolPermission.ALWAYS, denylist=["remove-item"]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r"C:\tools\rm.exe victim.txt")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ALWAYS


@pytest.mark.parametrize(
    ("pattern", "command"),
    [("get-content", "gc file.txt"), ("gc", "Get-Content file.txt")],
)
def test_windows_shell_allowlist_resolves_powershell_aliases(pattern, command):
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=[pattern]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ALWAYS


def test_windows_shell_sensitive_pattern_resolves_powershell_alias():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            permission=ToolPermission.ALWAYS, sensitive_patterns=["remove-item"]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command="rm victim.txt"))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


@pytest.mark.parametrize("command", ["& { cmd /k }", "& (cmd /k)"])
def test_windows_shell_wrapped_denylisted_command_is_rejected(command):
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            permission=ToolPermission.ALWAYS, denylist=["cmd /k"]
        ),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER
    assert "cmd /k" in (permission.reason or "")


def test_windows_shell_nested_unknown_command_requires_permission():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["echo"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command="echo $(Remove-Item secret.txt)")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        required.label == "Remove-Item *"
        for required in permission.required_permissions
    )


def test_windows_shell_nested_allowlisted_command_checks_outside_path(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    outside_file = outside / "secret.txt"
    workdir.mkdir()
    outside.mkdir()
    monkeypatch.chdir(workdir)
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["echo", "remove-item"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=f'echo $(Remove-Item "{outside_file}")')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


def test_windows_shell_single_ampersand_chain_requires_permission():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["dir"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command="dir & del secrets.txt")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        required.label == "del *" for required in permission.required_permissions
    )


def test_windows_shell_file_command_requires_approval_for_outside_path(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    outside_file = outside / "secret.txt"
    workdir.mkdir()
    outside.mkdir()
    outside_file.write_text("secret", encoding="utf-8")
    monkeypatch.chdir(workdir)
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["type"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=f'type "{outside_file}"')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


def test_windows_shell_path_qualified_executable_does_not_match_basename_allowlist():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["tree"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=r".\tree.exe"))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


def test_windows_shell_path_qualified_executable_checks_outside_path():
    executable = r"C:\Temp\tree.exe"
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=[executable]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=executable))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        r"C:\Temp" in required.label for required in permission.required_permissions
    )


def test_windows_shell_attached_path_parameter_requires_approval():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["get-content"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r"Get-Content -Path:C:\outside\secret.txt")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        r"C:\outside" in required.label for required in permission.required_permissions
    )


def test_windows_shell_expands_powershell_home_before_path_check(tmp_path, monkeypatch):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("USERPROFILE", str(outside))
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["type"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r'type "$HOME\secret.txt"')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


def test_windows_shell_expands_braced_environment_names_with_special_characters(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("ProgramFiles(x86)", str(outside))
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["get-content"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r'Get-Content "${env:ProgramFiles(x86)}\secret.txt"')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


def test_windows_shell_unsupported_braced_variable_requires_approval(monkeypatch):
    monkeypatch.setenv("destination", "inside")
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["get-content"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r'Get-Content "${global:destination}\secret.txt"')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        required.label == r"dynamic PowerShell path (${global:destination}\secret.txt)"
        for required in permission.required_permissions
    )


def test_windows_shell_redirection_requires_approval_for_outside_path():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["echo"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r"echo pwned > C:\outside\profile.ps1")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        r"C:\outside" in required.label for required in permission.required_permissions
    )


def test_windows_shell_quoted_redirection_text_is_not_treated_as_a_target():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["echo"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r'echo "not > C:\outside\profile.ps1"')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ALWAYS


@pytest.mark.parametrize(
    ("pattern", "command"),
    [
        ("git diff", r"git diff --no-index C:\outside\a.txt C:\outside\b.txt"),
        ("get-content", r"Get-Content FileSystem::C:\outside\secret.txt"),
        ("out-file", r"Out-File -FilePath C:\outside\secret.txt"),
    ],
)
def test_windows_shell_all_allowlisted_commands_check_explicit_outside_paths(
    pattern, command
):
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=[pattern]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        r"C:\outside" in required.label for required in permission.required_permissions
    )


@pytest.mark.parametrize(
    "command",
    [
        r"Get-Content Registry::HKEY_LOCAL_MACHINE\Software",
        "Get-Content /outside/secret.txt",
    ],
)
def test_windows_shell_ambiguous_provider_and_root_paths_require_approval(command):
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["get-content"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK


def test_windows_shell_allowlisted_command_checks_dynamic_path(tmp_path, monkeypatch):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("USERPROFILE", str(outside))
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=["out-file"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command="Out-File -FilePath $HOME")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


@pytest.mark.parametrize(
    ("pattern", "command_template"),
    [
        ("git diff", r"git diff --no-index .\a.txt .\b.txt"),
        ("get-content", 'Get-Content "FileSystem::{inside}"'),
        ("out-file", 'Out-File -FilePath "{inside}"'),
        ("tree", "tree /f"),
    ],
)
def test_windows_shell_allowlisted_command_keeps_in_workdir_paths_allowed(
    pattern, command_template, tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    command = command_template.format(inside=workdir / "inside.txt")
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(allowlist=[pattern]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ALWAYS


def test_windows_shell_path_qualified_denylist_matches_basename():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(denylist=["cmd /k"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r"C:\Windows\System32\cmd.exe /k echo hi")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER
    assert "cmd /k" in (permission.reason or "")


def test_windows_shell_path_qualified_standalone_denylist_matches_stem():
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(denylist_standalone=["notepad"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        WindowsShellArgs(command=r"C:\Windows\System32\notepad.exe")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER


def test_experimental_windows_shell_permission_requires_approval_for_outside_cwd(
    tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    monkeypatch.chdir(workdir)
    tool = ExperimentalWindowsShell(
        config_getter=lambda: ExperimentalBashToolConfig(allowlist=["dir"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(
        ExperimentalBashArgs(command="dir", cwd=str(outside))
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


@pytest.mark.parametrize(
    "command_template",
    [
        "dir {directory}",
        "gci {directory}",
        "ls {directory}",
        "type {file}",
        "cat {file}",
        "gc {file}",
        "findstr needle {file}",
        "DIR {directory}",
    ],
)
def test_windows_shell_read_command_requires_approval_for_outside_path(
    command_template, tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    outside_file = outside / "secret.txt"
    outside_file.write_text("needle", encoding="utf-8")
    monkeypatch.chdir(workdir)
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(), state=BaseToolState()
    )
    command = command_template.format(directory=outside, file=outside_file)

    permission = tool.resolve_permission(WindowsShellArgs(command=command))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


def test_windows_shell_config_uses_native_defaults_with_git_bash(monkeypatch):
    monkeypatch.setattr(experimental_bash_module, "is_windows", lambda: True)
    monkeypatch.setattr(bash_module, "uses_posix_shell", lambda: True)

    config = WindowsShellToolConfig()

    assert "dir" in config.allowlist
    assert "ls" not in config.allowlist
    assert "cmd /k" in config.denylist
    assert "cmd" in config.denylist_standalone


def test_git_bash_config_uses_posix_defaults_on_native_windows(monkeypatch):
    monkeypatch.setattr(experimental_bash_module, "is_windows", lambda: True)
    monkeypatch.setattr(bash_module, "uses_posix_shell", lambda: False)

    config = GitBashToolConfig()

    assert "ls" in config.allowlist
    assert "dir" not in config.allowlist
    assert "bash -i" in config.denylist
    assert "cmd /k" not in config.denylist


def test_git_bash_permission_uses_posix_guardrails_on_native_windows():
    tool = GitBash(
        config_getter=lambda: GitBashToolConfig(denylist=["rm"]), state=BaseToolState()
    )

    permission = tool.resolve_permission(GitBashArgs(command="rm -rf build"))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER
    assert "rm" in (permission.reason or "")


def test_experimental_git_bash_does_not_reject_native_windows(monkeypatch):
    monkeypatch.setattr(experimental_bash_module, "is_windows", lambda: True)
    tool = ExperimentalGitBash(
        config_getter=lambda: GitBashToolConfig(allowlist=["echo"]),
        state=BaseToolState(),
    )

    permission = tool.resolve_permission(ExperimentalBashArgs(command="echo hi"))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ALWAYS


def test_git_bash_inherited_ui_text_uses_git_bash_name():
    assert GitBashOutput.get_status_text() == "Polling git_bash session"
    assert GitBashStdin.get_status_text() == "Sending git_bash input"
    assert GitBashSessions.get_status_text() == "Managing git_bash sessions"
    assert GitBashLogFile.get_status_text() == "Working with git_bash log"


@pytest.mark.asyncio
async def test_git_bash_fallback_projects_native_shell_to_client_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shell = "C:/Program Files/Git/bin/bash.exe"
    monkeypatch.setattr(
        git_bash_module, "resolve_git_bash_shell", lambda requested, configured: shell
    )
    tool_io = _FakeTerminalToolIO()
    tool = GitBash(
        config_getter=lambda: GitBashToolConfig(
            default_timeout=17, max_output_bytes=1234
        ),
        state=BaseToolState(),
        cwd=tmp_path,
    )

    result = await collect_result(
        tool.run(
            GitBashArgs(command="printf hello", env={"CUSTOM": "value"}),
            InvokeContext(
                tool_call_id="git-call",
                session_id="session-1",
                tool_io=cast(ToolIOPort, tool_io),
            ),
        )
    )

    assert result.stdout == "client stdout"
    assert result.stderr == "client stderr"
    assert tool_io.requests == [
        ShellCommandRequest(
            session_id="session-1",
            tool_call_id="git-call",
            command=shell,
            args=["-c", "printf hello"],
            env={
                "CI": "true",
                "NONINTERACTIVE": "1",
                "NO_TTY": "1",
                "TERM": "dumb",
                "GIT_PAGER": "cat",
                "PAGER": "cat",
                "LESS": "-FX",
                "CUSTOM": "value",
            },
            cwd=tmp_path.resolve(),
            timeout=17,
            max_output_bytes=1234,
        )
    ]


@pytest.mark.asyncio
async def test_powershell_fallback_projects_native_shell_to_client_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shell = "C:/Program Files/PowerShell/7/pwsh.exe"
    monkeypatch.setattr(
        windows_shell_module,
        "resolve_powershell_shell",
        lambda requested, configured: shell,
    )
    tool_io = _FakeTerminalToolIO()
    tool = WindowsShell(
        config_getter=lambda: WindowsShellToolConfig(
            default_timeout=23, max_output_bytes=4321
        ),
        state=BaseToolState(),
        cwd=tmp_path,
    )

    result = await collect_result(
        tool.run(
            WindowsShellArgs(command="Write-Output hello", env={"CUSTOM": "value"}),
            InvokeContext(
                tool_call_id="powershell-call",
                session_id="session-2",
                tool_io=cast(ToolIOPort, tool_io),
            ),
        )
    )

    assert result.stdout == "client stdout"
    assert result.stderr == "client stderr"
    assert tool_io.requests == [
        ShellCommandRequest(
            session_id="session-2",
            tool_call_id="powershell-call",
            command=shell,
            args=["-NoLogo", "-NoProfile", "-Command", "Write-Output hello"],
            env={
                "CI": "true",
                "NONINTERACTIVE": "1",
                "NO_TTY": "1",
                "GIT_PAGER": "more",
                "PAGER": "more",
                "CUSTOM": "value",
            },
            cwd=tmp_path.resolve(),
            timeout=23,
            max_output_bytes=4321,
        )
    ]


windows_only = pytest.mark.skipif(
    not is_windows(), reason="Windows shell integration requires native Windows"
)


@pytest.fixture
def native_windows_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


def _managed_tool(
    terminal_runtime: TerminalRuntime | None = None,
) -> ExperimentalWindowsShell:
    return ExperimentalWindowsShell(
        config_getter=lambda: ExperimentalBashToolConfig(default_timeout=5),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )


def _native_windows_process_exists(pid: int) -> bool:
    shell = _windows.resolve_powershell_shell(None, None)
    result = subprocess.run(
        [
            shell,
            "-NoLogo",
            "-NoProfile",
            "-Command",
            (
                f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) "
                "{ exit 0 }; exit 1"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.returncode == 0


def _native_windows_process_exits(pid: int, timeout_seconds: float = 3) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _native_windows_process_exists(pid):
            return True
        time.sleep(0.05)
    return not _native_windows_process_exists(pid)


async def _wait_for_windows_output(
    output_tool: WindowsShellOutput,
    *,
    session_id: str,
    cursor: int,
    predicate: Callable[[BashOutputResult, str], bool],
    timeout_seconds: float = 6,
) -> tuple[BashOutputResult, str]:
    deadline = time.monotonic() + timeout_seconds
    output = ""
    while time.monotonic() < deadline:
        result = await collect_result(
            output_tool.run(
                BashOutputArgs(session_id=session_id, cursor=cursor, wait_seconds=0.25)
            )
        )
        output += result.output
        cursor = result.next_cursor
        if predicate(result, output):
            return result, output
    pytest.fail(f"Windows shell did not become ready; output={output!r}")


@pytest.mark.asyncio
@windows_only
async def test_windows_shell_managed_echo_hello(native_windows_platform):
    assert windows_managed_shell_supported()
    tool = _managed_tool()

    result = await collect_result(
        tool.run(ExperimentalBashArgs(command="echo hello", timeout_seconds=5))
    )

    assert result.status == "completed"
    assert result.exit_code == 0
    assert "hello" in result.output.lower()
    assert result.shell
    assert result.session_id.startswith("powershell_")
    assert "bash" not in result.session_id
    assert "bash" not in result.output_path.lower()


@pytest.mark.asyncio
@windows_only
async def test_windows_shell_background_poll_and_kill(native_windows_platform):
    terminal_runtime = TerminalRuntime()
    tool = _managed_tool(terminal_runtime)
    output_tool = WindowsShellOutput(
        config_getter=lambda: BashOutputConfig(max_poll_seconds=3),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    sessions_tool = WindowsShellSessions(
        config_getter=lambda: BashSessionsConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    started = await collect_result(
        tool.run(
            ExperimentalBashArgs(
                command='Write-Output "READY"; Start-Sleep -Seconds 30',
                background=True,
                timeout_seconds=1,
            )
        )
    )

    try:
        _, output = await _wait_for_windows_output(
            output_tool,
            session_id=started.session_id,
            cursor=0,
            predicate=lambda _result, output: "READY" in output,
        )
        assert "READY" in output
    finally:
        killed = await collect_result(
            sessions_tool.run(
                BashSessionsArgs(action="kill", session_id=started.session_id)
            )
        )
        assert killed.session is not None
        assert killed.session.session_id.startswith("powershell_")


@pytest.mark.asyncio
@windows_only
async def test_windows_shell_kill_terminates_spawned_child(native_windows_platform):
    terminal_runtime = TerminalRuntime()
    tool = _managed_tool(terminal_runtime)
    output_tool = WindowsShellOutput(
        config_getter=lambda: BashOutputConfig(max_poll_seconds=3),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    sessions_tool = WindowsShellSessions(
        config_getter=lambda: BashSessionsConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    started = await collect_result(
        tool.run(
            ExperimentalBashArgs(
                command=(
                    "$child = Start-Process -FilePath "
                    '"$env:SystemRoot\\System32\\ping.exe" '
                    "-ArgumentList '127.0.0.1','-n','30' -PassThru; "
                    'Write-Output "CHILD_PID=$($child.Id)"; '
                    "Wait-Process -Id $child.Id"
                ),
                background=True,
                timeout_seconds=1,
            )
        )
    )
    child_pid: int | None = None
    killed = False

    try:
        _, output = await _wait_for_windows_output(
            output_tool,
            session_id=started.session_id,
            cursor=0,
            predicate=lambda _result, output: "CHILD_PID=" in output,
        )
        match = re.search(r"CHILD_PID=(\d+)", output)
        assert match is not None
        child_pid = int(match.group(1))
        assert _native_windows_process_exists(child_pid)

        await collect_result(
            sessions_tool.run(
                BashSessionsArgs(action="kill", session_id=started.session_id)
            )
        )
        killed = True

        assert _native_windows_process_exits(child_pid)
    finally:
        if not killed:
            await collect_result(
                sessions_tool.run(
                    BashSessionsArgs(action="kill", session_id=started.session_id)
                )
            )
        if child_pid is not None and _native_windows_process_exists(child_pid):
            subprocess.run(
                ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )


@pytest.mark.asyncio
@pytest.mark.timeout(20)
@windows_only
async def test_windows_shell_stdin_drives_powershell_read_host(native_windows_platform):
    terminal_runtime = TerminalRuntime()
    tool = _managed_tool(terminal_runtime)
    stdin_tool = WindowsShellStdin(
        config_getter=lambda: BashStdinConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    output_tool = WindowsShellOutput(
        config_getter=lambda: BashOutputConfig(max_poll_seconds=3),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    sessions_tool = WindowsShellSessions(
        config_getter=lambda: BashSessionsConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    started = await collect_result(
        tool.run(
            ExperimentalBashArgs(
                command=(
                    'Write-Output "READY"; '
                    '$value = Read-Host; Write-Output "answer=$value"'
                ),
                background=True,
                timeout_seconds=1,
            )
        )
    )

    try:
        ready, _ = await _wait_for_windows_output(
            output_tool,
            session_id=started.session_id,
            cursor=0,
            predicate=lambda _result, output: "READY" in output,
        )
        await collect_result(
            stdin_tool.run(BashStdinArgs(session_id=started.session_id, text="value\n"))
        )
        _, output = await _wait_for_windows_output(
            output_tool,
            session_id=started.session_id,
            cursor=ready.next_cursor,
            predicate=lambda _result, output: "answer=value" in output,
        )
        assert "answer=value" in output
    finally:
        await collect_result(
            sessions_tool.run(
                BashSessionsArgs(action="kill", session_id=started.session_id)
            )
        )


@pytest.mark.asyncio
@windows_only
async def test_windows_shell_ctrl_c_interrupts_foreground_process(
    native_windows_platform,
):
    terminal_runtime = TerminalRuntime()
    tool = _managed_tool(terminal_runtime)
    stdin_tool = WindowsShellStdin(
        config_getter=lambda: BashStdinConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    output_tool = WindowsShellOutput(
        config_getter=lambda: BashOutputConfig(max_poll_seconds=3),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    sessions_tool = WindowsShellSessions(
        config_getter=lambda: BashSessionsConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    started = await collect_result(
        tool.run(
            ExperimentalBashArgs(
                command='Write-Output "READY"; Start-Sleep -Seconds 30',
                background=True,
                timeout_seconds=1,
            )
        )
    )

    try:
        ready, _ = await _wait_for_windows_output(
            output_tool,
            session_id=started.session_id,
            cursor=0,
            predicate=lambda _result, output: "READY" in output,
        )
        await collect_result(
            stdin_tool.run(
                BashStdinArgs(session_id=started.session_id, control=["ctrl_c"])
            )
        )
        output, _ = await _wait_for_windows_output(
            output_tool,
            session_id=started.session_id,
            cursor=ready.next_cursor,
            predicate=lambda result, _output: result.status != "running",
        )
        assert output.status in {"completed", "killed"}
    finally:
        await collect_result(
            sessions_tool.run(
                BashSessionsArgs(action="kill", session_id=started.session_id)
            )
        )


@pytest.mark.asyncio
@windows_only
async def test_windows_shell_reset_clears_logs(native_windows_platform):
    sessions_tool = WindowsShellSessions(
        config_getter=lambda: BashSessionsConfig(), state=BaseToolState()
    )

    result = await collect_result(
        sessions_tool.run(BashSessionsArgs(action="reset", clear_logs=True))
    )

    assert result.action == "reset"
