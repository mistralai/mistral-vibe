from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from typing import cast

from pydantic import ValidationError
import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, InvokeContext, ToolError, ToolPermission
import vibe.core.tools.builtins.bash as bash_module
from vibe.core.tools.builtins.bash import (
    Bash,
    BashArgs,
    BashToolConfig,
    CapturedShellResult,
    _get_default_denylist,
    _get_default_denylist_standalone,
    default_read_only_commands,
)
from vibe.core.tools.builtins.experimental_bash import (
    BashLogFile,
    BashLogFileArgs,
    BashLogFileConfig,
    BashLogFileResult,
    BashOutput,
    BashOutputArgs,
    BashOutputConfig,
    BashOutputResult,
    BashSessions,
    BashSessionsArgs,
    BashSessionsConfig,
    BashSessionsResult,
    BashStdin,
    BashStdinArgs,
    BashStdinConfig,
    BashStdinResult,
    ExperimentalBash,
    ExperimentalBashArgs,
    ExperimentalBashResult,
    ExperimentalBashToolConfig,
    ManagedShellError,
    OutputChunk,
    SessionInfo,
    TerminalSession,
    TerminalSessionManager,
    _ForegroundStream,
)
from vibe.core.tools.builtins.managed_shell.backend import (
    UNKNOWN_EXIT_CODE,
    ManagedShellBackend,
    ManagedShellBackendError,
)
from vibe.core.tools.permissions import PermissionContext
from vibe.core.tools.terminal_runtime import TerminalRuntime
from vibe.core.tools.ui import ToolUIDataAdapter
from vibe.core.types import ToolCallEvent, ToolResultEvent, ToolStreamEvent
from vibe.core.utils import is_windows


@pytest.fixture
def bash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = BashToolConfig()
    return Bash(config_getter=lambda: config, state=BaseToolState())


def _hide_standard_git_installs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ProgramFiles", raising=False)
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)


def test_shell_results_keep_a_returncode_alias_for_post_tool_hooks():
    # The verbatim result dump is the `tool_output` payload handed to hooks.
    managed = ExperimentalBashResult(command="x", exit_code=3).model_dump(mode="json")
    assert managed["exit_code"] == 3
    assert managed["returncode"] == 3

    captured = CapturedShellResult(command="x", exit_code=2).model_dump(mode="json")
    assert captured["exit_code"] == 2
    assert captured["returncode"] == 2


def test_managed_shell_returncode_alias_is_zero_while_the_exit_code_is_unknown():
    dumped = ExperimentalBashResult(command="x").model_dump(mode="json")

    assert dumped["exit_code"] is None
    assert dumped["returncode"] == 0


@pytest.mark.asyncio
async def test_runs_echo_successfully(bash):
    result = await collect_result(bash.run(BashArgs(command="echo hello")))

    assert result.exit_code == 0
    assert result.stdout == "hello\n"
    assert result.stderr == ""


@pytest.mark.asyncio
async def test_fails_cat_command_with_missing_file(bash):
    with pytest.raises(ToolError) as err:
        await collect_result(bash.run(BashArgs(command="cat missing_file.txt")))

    message = str(err.value)
    assert "Command failed" in message
    assert "Return code: 1" in message
    assert "No such file or directory" in message


@pytest.mark.asyncio
async def test_uses_effective_workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = BashToolConfig()
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    result = await collect_result(bash_tool.run(BashArgs(command="pwd")))

    assert result.stdout.strip() == str(tmp_path)


@pytest.mark.asyncio
async def test_handles_timeout(bash):
    with pytest.raises(ToolError) as err:
        await collect_result(bash.run(BashArgs(command="sleep 2", timeout=1)))

    assert "Command timed out after 1s" in str(err.value)


@pytest.mark.asyncio
async def test_windows_cmd_spawn_ignores_non_cmd_comspec(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    _hide_standard_git_installs(monkeypatch)
    monkeypatch.setenv(
        "COMSPEC", "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
    )
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    monkeypatch.setattr(
        "vibe.utils.platform.shutil.which", lambda name, path=None: None
    )

    proc = object()
    calls = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return proc

    async def fake_create_subprocess_shell(*args, **kwargs):
        raise AssertionError("cmd fallback must not use COMSPEC-backed shell mode")

    monkeypatch.setattr(
        bash_module.asyncio, "create_subprocess_exec", fake_create_subprocess_exec
    )
    monkeypatch.setattr(
        bash_module.asyncio, "create_subprocess_shell", fake_create_subprocess_shell
    )

    result = await bash_module.spawn_shell_command("echo hello")

    assert result is proc
    assert calls[0][0][:4] == (
        "C:\\Windows\\System32\\cmd.exe",
        "/d",
        "/c",
        "echo hello",
    )


@pytest.mark.asyncio
async def test_truncates_output_to_max_bytes(bash):
    config = BashToolConfig(max_output_bytes=5)
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    result = await collect_result(
        bash_tool.run(BashArgs(command="printf 'abcdefghij'"))
    )

    assert result.stdout == "abcde"
    assert result.stderr == ""
    assert result.exit_code == 0


@pytest.mark.skipif(is_windows(), reason="managed bash requires a POSIX-like platform")
@pytest.mark.asyncio
async def test_experimental_bash_projects_merged_pty_stream_as_stdout():
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )

    result = await collect_result(
        tool.run(ExperimentalBashArgs(command="printf err >&2"))
    )

    assert result.output == "err"
    assert ExperimentalBash.project_result(result) == {
        "stdout": "err",
        "stderr": "",
        "output": "err",
        "truncated": False,
    }


@pytest.mark.skipif(is_windows(), reason="managed bash requires a POSIX-like platform")
@pytest.mark.asyncio
async def test_experimental_bash_serializes_command_output_only_once():
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )

    # Split so the echoed command line does not itself contain the payload.
    result = await collect_result(
        tool.run(ExperimentalBashArgs(command="printf 'MARKER%s' PAYLOAD"))
    )

    dumped = result.model_dump(mode="json")
    assert dumped["output"] == "MARKERPAYLOAD"
    assert [key for key, value in dumped.items() if value == "MARKERPAYLOAD"] == [
        "output"
    ]


@pytest.mark.skipif(is_windows(), reason="managed bash requires a POSIX-like platform")
@pytest.mark.asyncio
async def test_experimental_bash_streams_foreground_output_before_the_result():
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )

    streamed: list[str] = []
    result = None
    async for item in tool.run(
        ExperimentalBashArgs(command="printf first; sleep 0.5; printf second"),
        InvokeContext(tool_call_id="call-1"),
    ):
        if isinstance(item, ToolStreamEvent):
            assert item.tool_name == "bash"
            assert item.tool_call_id == "call-1"
            streamed.append(item.message)
        else:
            result = item

    assert result is not None
    assert len(streamed) > 1
    assert "first" in "".join(streamed)
    assert "second" in "".join(streamed)
    assert "first" in result.output
    assert "second" in result.output


@pytest.mark.skipif(is_windows(), reason="managed bash requires a POSIX-like platform")
@pytest.mark.asyncio
async def test_experimental_bash_keeps_carriage_return_redraws_in_the_result():
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )

    # The PTY rewrites the trailing \n as \r\n; both survive to the result.
    result = await collect_result(
        tool.run(ExperimentalBashArgs(command="printf '10%%\\r100%%\\n'"))
    )

    assert result.output == "10%\r100%\r\n"


@pytest.mark.skipif(is_windows(), reason="managed bash requires a POSIX-like platform")
@pytest.mark.asyncio
async def test_streamed_chunks_reassemble_into_the_result_output():
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )
    command = "printf 'a\\r\\nb'; sleep 0.5; printf '\\r\\n10%%\\r100%%\\n'"

    streamed: list[str] = []
    result = None
    async for item in tool.run(
        ExperimentalBashArgs(command=command), InvokeContext(tool_call_id="call-1")
    ):
        if isinstance(item, ToolStreamEvent):
            streamed.append(item.message)
        else:
            result = item

    assert result is not None
    assert len(streamed) > 1
    # A CRLF landing across two reads must not shift the len(delivered) offset.
    assert "".join(streamed) == result.output


@pytest.mark.skipif(is_windows(), reason="managed bash requires a POSIX-like platform")
@pytest.mark.asyncio
async def test_soft_timeout_streams_the_whole_output_before_handing_over():
    terminal_runtime = TerminalRuntime()
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )

    streamed: list[str] = []
    result = None
    async for item in tool.run(
        ExperimentalBashArgs(command="printf first; sleep 30", timeout_seconds=0.5),
        InvokeContext(tool_call_id="call-2"),
    ):
        if isinstance(item, ToolStreamEvent):
            streamed.append(item.message)
        else:
            result = item

    assert result is not None
    assert result.background is True
    assert "".join(streamed) == result.output
    terminal_runtime.get().kill(result.session_id)


def _fake_session_info(status):
    return SessionInfo(
        session_id="s",
        command="printf accents",
        cwd=".",
        shell="/bin/bash",
        status=status,
        exit_code=0 if status == "completed" else None,
        output_path="/tmp/s.log",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )


class _ChunkedManager:
    def __init__(self, chunks, tail=""):
        self._chunks = list(chunks)
        self._log = "".join(chunks) + tail
        self.reads = 0

    def read_output(self, *, session_id, cursor, wait_seconds, max_bytes):
        self.reads += 1
        output = (
            self._chunks.pop(0)
            if self._chunks
            else self._log.encode()[cursor:].decode()
        )
        status = "running" if self._chunks else "completed"
        return (
            _fake_session_info(status),
            OutputChunk(
                output=output,
                next_cursor=cursor + len(output.encode()),
                truncated=False,
            ),
        )


def _foreground_stream(
    manager, *, tool_call_id: str | None = "call-1", max_bytes: int = 1_000
):
    return _ForegroundStream(
        cast(TerminalSessionManager, manager),
        session_id="s",
        tool_name="bash",
        tool_call_id=tool_call_id,
        max_bytes=max_bytes,
    )


@pytest.mark.asyncio
async def test_experimental_bash_caps_streamed_output_by_encoded_bytes():
    manager = _ChunkedManager(["é" * 8, "é" * 8, "é" * 8])
    # Odd cap against 2-byte characters forces the second clip to cut mid-character.
    max_bytes = 21
    stream = _foreground_stream(manager, max_bytes=max_bytes)

    events = [event async for event in stream.pump(timeout=5)]

    streamed = "".join(event.message for event in events)
    assert [event.message for event in events] == ["é" * 8, "é" * 2]
    assert len(streamed.encode()) <= max_bytes
    assert "\ufffd" not in streamed
    assert manager.reads == 3


@pytest.mark.asyncio
async def test_experimental_bash_streams_every_byte_when_under_the_cap():
    manager = _ChunkedManager(["héllo ", "wörld"])
    stream = _foreground_stream(manager)

    events = [event async for event in stream.pump(timeout=5)]

    assert "".join(event.message for event in events) == "héllo wörld"
    assert stream.completed is True


@pytest.mark.asyncio
async def test_draining_resumes_where_the_pump_stopped():
    manager = _ChunkedManager(["first ", "second "], tail="third")
    stream = _foreground_stream(manager)

    pumped = [event async for event in stream.pump(timeout=5)]
    drained = await stream.drain()

    assert [event.message for event in pumped] == ["first ", "second "]
    assert drained is not None
    assert drained.message == "third"
    # A second drain has nothing left to send: the cursor moved with the first.
    assert await stream.drain() is None


@pytest.mark.asyncio
async def test_draining_notices_an_exit_the_pump_timed_out_on():
    manager = _ChunkedManager(["done"])
    stream = _foreground_stream(manager)

    pumped = [event async for event in stream.pump(timeout=0)]
    assert pumped == []
    assert stream.completed is False

    drained = await stream.drain()

    assert drained is not None
    assert drained.message == "done"
    assert stream.completed is True


@pytest.mark.skipif(is_windows(), reason="managed bash requires a POSIX-like platform")
@pytest.mark.asyncio
async def test_experimental_bash_keeps_failure_output_out_of_the_display():
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )

    with pytest.raises(ToolError) as err:
        async for _ in tool.run(
            ExperimentalBashArgs(command="echo boom; exit 3"),
            InvokeContext(tool_call_id="call-1"),
        ):
            pass

    assert "boom" in str(err.value)
    assert err.value.display == "Command failed: 'echo boom; exit 3'\nReturn code: 3"
    # What the client shows is always the head of what the model reads.
    assert str(err.value).startswith(err.value.display)


@pytest.mark.asyncio
async def test_foreground_stream_emits_nothing_without_a_tool_call():
    manager = _ChunkedManager(["héllo ", "wörld"])
    stream = _foreground_stream(manager, tool_call_id=None)

    events = [event async for event in stream.pump(timeout=5)]

    assert events == []
    assert stream.completed is True
    assert stream._cursor == len("héllo wörld".encode())


@pytest.mark.skipif(is_windows(), reason="managed bash requires a POSIX-like platform")
@pytest.mark.asyncio
async def test_experimental_bash_skips_streaming_without_an_invoke_context():
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )

    items = [item async for item in tool.run(ExperimentalBashArgs(command="echo hi"))]

    assert not any(isinstance(item, ToolStreamEvent) for item in items)
    assert len(items) == 1
    result = items[0]
    assert isinstance(result, ExperimentalBashResult)
    # A PTY turns the newline into CRLF and the decoder keeps it verbatim.
    assert result.output == "hi\r\n"


@pytest.mark.asyncio
async def test_cat_preserves_accents_from_latin1_encoded_file(bash, tmp_path):
    file = tmp_path / "menu.txt"
    file.write_bytes("café au lait\nthé glacé\n".encode("latin-1"))

    result = await collect_result(bash.run(BashArgs(command=f"cat {file.name}")))

    assert result.exit_code == 0
    assert "\ufffd" not in result.stdout
    assert result.stdout == "café au lait\nthé glacé\n"


class _SplitReadTerminal:
    def __init__(self, fragments: list[bytes]) -> None:
        self._fragments = fragments
        self.pid = 1
        self.pty_backend = "fake"

    @property
    def returncode(self) -> int | None:
        return 0

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def wait_readable(self, timeout_seconds: float) -> bool:
        return bool(self._fragments)

    def read(self, size: int) -> bytes:
        return self._fragments.pop(0) if self._fragments else b""

    def write(self, data: bytes) -> int:
        return len(data)

    def close(self) -> None:
        pass


class _UnusedBackend:
    def request_termination(self, terminal) -> None:
        pass

    def force_terminate_terminal(self, terminal, *, timeout_seconds: float) -> None:
        pass


class _CompletedTerminal:
    pid = 1
    pty_backend = "fake"

    @property
    def returncode(self) -> int:
        return 0

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def wait_readable(self, timeout_seconds: float) -> bool:
        return False

    def read(self, size: int) -> bytes:
        return b""

    def write(self, data: bytes) -> int:
        return len(data)

    def close(self) -> None:
        pass


class _RunningTerminal:
    pid = 1
    pty_backend = "fake"

    @property
    def returncode(self) -> int | None:
        return None

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        raise subprocess.TimeoutExpired("running", timeout or 0)

    def wait_readable(self, timeout_seconds: float) -> bool:
        return False

    def read(self, size: int) -> bytes:
        return b""

    def write(self, data: bytes) -> int:
        return len(data)

    def close(self) -> None:
        pass


class _UnreapableTerminal:
    pid = 1
    pty_backend = "fake"

    @property
    def returncode(self) -> int | None:
        return None

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int | None:
        return None

    def wait_readable(self, timeout_seconds: float) -> bool:
        return True

    def read(self, size: int) -> bytes:
        raise OSError("pty vanished")

    def write(self, data: bytes) -> int:
        return len(data)

    def close(self) -> None:
        pass


class _ControllableTerminal:
    pid = 1
    pty_backend = "fake"

    def __init__(self, name: str, *, exits_on_request: bool) -> None:
        self.name = name
        self.exits_on_request = exits_on_request
        self.alive = True

    @property
    def returncode(self) -> int | None:
        return None if self.alive else 0

    def poll(self) -> int | None:
        return None if self.alive else 0

    def wait(self, timeout: float | None = None) -> int:
        if self.alive:
            raise subprocess.TimeoutExpired(self.name, timeout or 0)
        return 0

    def wait_readable(self, timeout_seconds: float) -> bool:
        return False

    def read(self, size: int) -> bytes:
        return b""

    def write(self, data: bytes) -> int:
        return len(data)

    def close(self) -> None:
        pass


class _RecordingTerminationBackend:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    def request_termination(self, terminal: _ControllableTerminal) -> None:
        self.events.append(("request", terminal.name))
        if terminal.exits_on_request:
            terminal.alive = False

    def force_terminate_terminal(
        self, terminal: _ControllableTerminal, *, timeout_seconds: float
    ) -> None:
        self.events.append(("force", terminal.name))
        terminal.alive = False


def _fake_session(tmp_path, name: str, terminal) -> TerminalSession:
    output_path = tmp_path / f"{name}.log"
    output_path.touch()
    return TerminalSession(
        session_id=name,
        command="fake",
        cwd=tmp_path,
        shell="fake",
        terminal=terminal,
        output_path=output_path,
        manifest_path=tmp_path / f"{name}.json",
        created_at=0,
    )


def test_manager_does_not_force_process_that_exits_during_grace(tmp_path):
    backend = _RecordingTerminationBackend()
    terminal = _ControllableTerminal("first", exits_on_request=True)
    manager = TerminalSessionManager(
        backend=cast(ManagedShellBackend, backend), session_prefix="test"
    )

    manager._terminate_sessions([_fake_session(tmp_path, "first", terminal)])

    assert backend.events == [("request", "first")]


def test_manager_requests_all_terminations_before_forcing_survivors(
    tmp_path, monkeypatch
):
    backend = _RecordingTerminationBackend()
    first = _ControllableTerminal("first", exits_on_request=False)
    second = _ControllableTerminal("second", exits_on_request=False)
    manager = TerminalSessionManager(
        backend=cast(ManagedShellBackend, backend), session_prefix="test"
    )
    monkeypatch.setattr(
        "vibe.core.tools.builtins.experimental_bash.KILL_GRACE_SECONDS", 0
    )

    manager._terminate_sessions([
        _fake_session(tmp_path, "first", first),
        _fake_session(tmp_path, "second", second),
    ])

    assert backend.events == [
        ("request", "first"),
        ("request", "second"),
        ("force", "first"),
        ("force", "second"),
    ]


class _UnkillableBackend:
    def request_termination(self, terminal: _ControllableTerminal) -> None:
        pass

    def force_terminate_terminal(
        self, terminal: _ControllableTerminal, *, timeout_seconds: float
    ) -> None:
        raise ManagedShellBackendError("taskkill failed")


def _install_unkillable_manager(tmp_path, monkeypatch, name: str):
    monkeypatch.setattr(
        "vibe.core.tools.builtins.experimental_bash.KILL_GRACE_SECONDS", 0
    )
    monkeypatch.setattr(
        "vibe.core.tools.builtins.experimental_bash.FORCE_TERMINATION_TIMEOUT_SECONDS",
        0,
    )
    manager = TerminalSessionManager(
        backend=cast(ManagedShellBackend, _UnkillableBackend()), session_prefix="test"
    )
    session = _fake_session(
        tmp_path, name, _ControllableTerminal(name, exits_on_request=False)
    )
    manager._sessions[session.session_id] = session
    return manager, session


def test_kill_restores_running_status_when_termination_fails(tmp_path, monkeypatch):
    manager, session = _install_unkillable_manager(tmp_path, monkeypatch, "stuck")

    with pytest.raises(ManagedShellBackendError):
        manager.kill(session.session_id)

    assert session.status == "running"
    assert manager.info(session.session_id).status == "running"


def test_reset_restores_running_status_when_termination_fails(tmp_path, monkeypatch):
    manager, session = _install_unkillable_manager(tmp_path, monkeypatch, "stuck")

    with pytest.raises(ManagedShellBackendError):
        manager.reset(clear_logs=False)

    assert session.status == "running"


@pytest.mark.skipif(is_windows(), reason="managed bash is POSIX-only")
def test_posix_shell_resolution_prefers_zsh_fallback_but_honors_overrides(monkeypatch):
    from vibe.core.tools.builtins.managed_shell import _posix

    backend = _posix.PosixManagedShellBackend()
    calls: list[str] = []
    resolved_shells = {
        "requested": "/mock/requested",
        "configured": "/mock/configured",
        "/bin/zsh": "/bin/zsh",
        "bash": "/mock/bash",
    }

    def fake_resolve_executable(candidate: str) -> str | None:
        calls.append(candidate)
        return resolved_shells.get(candidate)

    monkeypatch.setattr(_posix, "_resolve_executable", fake_resolve_executable)

    assert backend.resolve_shell("requested", "configured") == "/mock/requested"
    assert calls == ["requested"]

    calls.clear()
    assert backend.resolve_shell(None, "configured") == "/mock/configured"
    assert calls == ["configured"]

    calls.clear()
    assert backend.resolve_shell(None, None) == "/bin/zsh"
    assert calls == ["zsh", "/bin/zsh"]


def test_reader_loop_preserves_multibyte_split_across_chunks(tmp_path):
    snowman = "☃".encode()
    terminal = _SplitReadTerminal([snowman[:2], snowman[2:]])
    manager = TerminalSessionManager(
        backend=cast(ManagedShellBackend, _UnusedBackend())
    )
    output_path = tmp_path / "out.log"
    output_path.touch()
    session = TerminalSession(
        session_id="split",
        command="cmd",
        cwd=tmp_path,
        shell="/bin/sh",
        terminal=terminal,
        output_path=output_path,
        manifest_path=tmp_path / "split.json",
        created_at=0.0,
        pty_backend=terminal.pty_backend,
    )

    manager._reader_loop(session)

    chunk = manager._read_file_chunk(output_path, cursor=0, max_bytes=64)
    assert chunk.output == "☃"
    assert "\ufffd" not in chunk.output


def test_reader_loop_reports_an_exit_code_when_the_terminal_cannot_be_reaped(tmp_path):
    manager = TerminalSessionManager(
        backend=cast(ManagedShellBackend, _UnusedBackend())
    )
    output_path = tmp_path / "out.log"
    output_path.touch()
    session = TerminalSession(
        session_id="unreapable",
        command="cmd",
        cwd=tmp_path,
        shell="/bin/sh",
        terminal=_UnreapableTerminal(),
        output_path=output_path,
        manifest_path=tmp_path / "unreapable.json",
        created_at=0.0,
    )

    manager._reader_loop(session)

    assert session.status == "completed"
    assert session.exit_code == UNKNOWN_EXIT_CODE


def test_read_file_chunk_does_not_split_multibyte_at_page_boundary(tmp_path):
    manager = TerminalSessionManager()
    output_path = tmp_path / "out.log"
    # "a" * 4 then a 3-byte snowman then "b": with max_bytes=5 the window ends
    # one byte into the snowman.
    output_path.write_bytes(b"aaaa" + "☃".encode() + b"b")

    first = manager._read_file_chunk(output_path, cursor=0, max_bytes=5)
    assert first.output == "aaaa"
    assert "\ufffd" not in first.output
    assert first.truncated is True

    second = manager._read_file_chunk(
        output_path, cursor=first.next_cursor, max_bytes=5
    )
    assert second.output == "☃b"
    assert "\ufffd" not in second.output
    assert second.truncated is False


def test_running_read_output_defers_incomplete_utf8_at_current_eof(tmp_path):
    manager = TerminalSessionManager()
    output_path = tmp_path / "out.log"
    snowman = "☃".encode()
    output_path.write_bytes(b"a" + snowman[:2])
    session = TerminalSession(
        session_id="running",
        command="cmd",
        cwd=tmp_path,
        shell="/bin/sh",
        terminal=_RunningTerminal(),
        output_path=output_path,
        manifest_path=tmp_path / "running.json",
        created_at=0.0,
    )
    manager._sessions[session.session_id] = session

    _info, first = manager.read_output(
        session_id="running", cursor=0, wait_seconds=0, max_bytes=64
    )

    assert first.output == "a"
    assert "\ufffd" not in first.output
    assert first.next_cursor == 1
    assert first.truncated is True

    output_path.write_bytes(b"a" + snowman + b"z")
    _info, second = manager.read_output(
        session_id="running", cursor=first.next_cursor, wait_seconds=0, max_bytes=64
    )

    assert second.output == "☃z"
    assert "\ufffd" not in second.output
    assert second.next_cursor == output_path.stat().st_size
    assert second.truncated is False


def test_read_file_chunk_keeps_every_carriage_return(tmp_path):
    manager = TerminalSessionManager()
    output_path = tmp_path / "out.log"
    output_path.write_bytes(b"10%\r55%\r100%\r\ndone\r\n")

    chunk = manager._read_file_chunk(output_path, cursor=0, max_bytes=64)

    assert chunk.output == "10%\r55%\r100%\r\ndone\r\n"


_CRLF_PAYLOAD = b"one\r\ntwo\r\n50%\rdone\r\n"


@pytest.mark.parametrize("max_bytes", range(1, len(_CRLF_PAYLOAD) + 1))
def test_read_file_chunk_reassembles_the_file_at_every_split_boundary(
    tmp_path, max_bytes
):
    manager = TerminalSessionManager()
    output_path = tmp_path / "out.log"
    output_path.write_bytes(_CRLF_PAYLOAD)

    cursor = 0
    parts: list[str] = []
    while cursor < len(_CRLF_PAYLOAD):
        chunk = manager._read_file_chunk(
            output_path, cursor=cursor, max_bytes=max_bytes, more_output_expected=True
        )
        assert chunk.next_cursor > cursor
        cursor = chunk.next_cursor
        parts.append(chunk.output)

    assert "".join(parts) == _CRLF_PAYLOAD.decode()


def _running_session(tmp_path, output_path) -> TerminalSession:
    return TerminalSession(
        session_id="running",
        command="cmd",
        cwd=tmp_path,
        shell="/bin/sh",
        terminal=_RunningTerminal(),
        output_path=output_path,
        manifest_path=tmp_path / "running.json",
        created_at=0.0,
    )


def test_running_read_output_hands_back_a_lone_carriage_return(tmp_path):
    # A progress bar writes `Progress: 50%\r` and pauses; the next poll is empty.
    manager = TerminalSessionManager()
    output_path = tmp_path / "out.log"
    output_path.write_bytes(b"Progress: 50%\r")
    session = _running_session(tmp_path, output_path)
    manager._sessions[session.session_id] = session

    _info, first = manager.read_output(
        session_id="running", cursor=0, wait_seconds=0, max_bytes=64
    )

    assert first.output == "Progress: 50%\r"
    assert first.next_cursor == output_path.stat().st_size
    assert first.truncated is False

    _info, second = manager.read_output(
        session_id="running", cursor=first.next_cursor, wait_seconds=0, max_bytes=64
    )

    assert second.output == ""
    assert second.next_cursor == first.next_cursor


def test_running_read_output_splits_a_crlf_without_losing_a_byte(tmp_path):
    manager = TerminalSessionManager()
    output_path = tmp_path / "out.log"
    output_path.write_bytes(b"a\r")
    session = _running_session(tmp_path, output_path)
    manager._sessions[session.session_id] = session

    _info, first = manager.read_output(
        session_id="running", cursor=0, wait_seconds=0, max_bytes=64
    )

    assert first.output == "a\r"

    output_path.write_bytes(b"a\r\nb")
    _info, second = manager.read_output(
        session_id="running", cursor=first.next_cursor, wait_seconds=0, max_bytes=64
    )

    assert second.output == "\nb"
    assert first.output + second.output == "a\r\nb"


def test_completed_read_output_keeps_a_final_carriage_return(tmp_path):
    manager = TerminalSessionManager()
    output_path = tmp_path / "out.log"
    output_path.write_bytes(b"50%\r")
    session = TerminalSession(
        session_id="done",
        command="cmd",
        cwd=tmp_path,
        shell="/bin/sh",
        terminal=_CompletedTerminal(),
        output_path=output_path,
        manifest_path=tmp_path / "done.json",
        created_at=0.0,
        status="completed",
        exit_code=0,
    )
    manager._sessions[session.session_id] = session

    _info, chunk = manager.read_output(
        session_id="done", cursor=0, wait_seconds=0, max_bytes=64
    )

    assert chunk.output == "50%\r"
    assert chunk.next_cursor == output_path.stat().st_size
    assert chunk.truncated is False


def test_inspect_session_does_not_split_multibyte_at_tail_boundary(tmp_path):
    manager = TerminalSessionManager()
    output_path = tmp_path / "out.log"
    output_path.write_bytes(b"aa" + "☃".encode() + b"z")
    session = TerminalSession(
        session_id="tail",
        command="cmd",
        cwd=tmp_path,
        shell="/bin/sh",
        terminal=_CompletedTerminal(),
        output_path=output_path,
        manifest_path=tmp_path / "tail.json",
        created_at=0.0,
        status="completed",
        exit_code=0,
    )
    manager._sessions[session.session_id] = session

    _info, chunk = manager.inspect_session("tail", max_bytes=3)

    assert chunk.output == "z"
    assert "\ufffd" not in chunk.output
    assert chunk.next_cursor == output_path.stat().st_size
    assert chunk.truncated is False


def test_bash_stdin_requires_exactly_one_input_source():
    BashStdinArgs(session_id="s", text="hi")
    BashStdinArgs(session_id="s", control=["ctrl_c"])

    with pytest.raises(ValidationError):
        BashStdinArgs(session_id="s")
    with pytest.raises(ValidationError):
        BashStdinArgs(session_id="s", text="hi", control=["ctrl_c"])


def test_bash_stdin_control_field_rejects_unknown_keys():
    BashStdinArgs(session_id="s", control=["ctrl_c", "enter"])

    with pytest.raises(ValidationError):
        BashStdinArgs.model_validate({"session_id": "s", "control": ["not_a_real_key"]})


def test_bash_byte_limit_models_accept_legacy_max_chars_alias():
    assert (
        BashOutputArgs.model_validate({"session_id": "s", "max_chars": 12}).max_bytes
        == 12
    )
    assert (
        BashSessionsArgs.model_validate({
            "action": "inspect",
            "max_chars": 13,
        }).max_bytes
        == 13
    )
    assert (
        BashLogFileArgs.model_validate({"action": "read", "max_chars": 14}).max_bytes
        == 14
    )


def test_bash_config_models_accept_legacy_max_inline_chars_alias():
    assert (
        ExperimentalBashToolConfig.model_validate({
            "max_inline_chars": 12
        }).max_inline_bytes
        == 12
    )
    assert (
        BashOutputConfig.model_validate({"max_inline_chars": 13}).max_inline_bytes == 13
    )
    assert (
        BashSessionsConfig.model_validate({"max_inline_chars": 14}).max_inline_bytes
        == 14
    )
    assert (
        BashLogFileConfig.model_validate({"max_inline_chars": 15}).max_inline_bytes
        == 15
    )


def test_bash_output_display_describes_polling_and_running_result():
    adapter = ToolUIDataAdapter(BashOutput)
    call = adapter.get_call_display(
        ToolCallEvent(
            tool_call_id="call",
            tool_name="bash_output",
            tool_class=BashOutput,
            args=BashOutputArgs(session_id="bash_1", wait_seconds=1),
        )
    )
    result = adapter.get_result_display(
        ToolResultEvent(
            tool_call_id="call",
            tool_name="bash_output",
            tool_class=BashOutput,
            result=BashOutputResult(
                session_id="bash_1",
                status="running",
                output="",
                next_cursor=0,
                truncated=True,
                output_path="/tmp/bash_1.log",
            ),
        )
    )

    assert call.summary == "Waiting for bash session bash_1"
    assert call.verb == "Waiting for"
    assert call.message == "bash session bash_1"
    assert result.success is True
    assert result.verb == "Polled"
    assert result.message == "session bash_1 is still running"
    assert result.suffix == "truncated"


def test_bash_stdin_display_describes_bytes_written():
    adapter = ToolUIDataAdapter(BashStdin)
    call = adapter.get_call_display(
        ToolCallEvent(
            tool_call_id="call",
            tool_name="bash_stdin",
            tool_class=BashStdin,
            args=BashStdinArgs(session_id="bash_1", text="ok\n"),
        )
    )
    result = adapter.get_result_display(
        ToolResultEvent(
            tool_call_id="call",
            tool_name="bash_stdin",
            tool_class=BashStdin,
            result=BashStdinResult(
                session_id="bash_1", bytes_written=3, status="running"
            ),
        )
    )

    assert call.summary == "Sending input to bash session bash_1"
    assert call.verb == "Sending"
    assert call.message == "input to bash session bash_1"
    assert result.success is True
    assert result.verb == "Sent"
    assert result.message == "3 bytes to running session bash_1"

    completed = adapter.get_result_display(
        ToolResultEvent(
            tool_call_id="call",
            tool_name="bash_stdin",
            tool_class=BashStdin,
            result=BashStdinResult(
                session_id="bash_1", bytes_written=3, status="completed"
            ),
        )
    )

    assert completed.success is True
    assert completed.verb == "Sent"
    assert completed.message == "3 bytes to completed session bash_1"


def test_bash_sessions_display_describes_actions():
    adapter = ToolUIDataAdapter(BashSessions)
    call = adapter.get_call_display(
        ToolCallEvent(
            tool_call_id="call",
            tool_name="bash_sessions",
            tool_class=BashSessions,
            args=BashSessionsArgs(action="kill", session_id="bash_1"),
        )
    )
    result = adapter.get_result_display(
        ToolResultEvent(
            tool_call_id="call",
            tool_name="bash_sessions",
            tool_class=BashSessions,
            result=BashSessionsResult(action="reset", sessions=[]),
        )
    )

    assert call.summary == "Killing one bash session bash_1"
    assert call.verb == "Killing"
    assert call.message == "one bash session bash_1"
    assert result.success is True
    assert result.verb == "Reset"
    assert result.message == "all bash sessions; stopped 0 sessions"


def test_bash_sessions_schema_distinguishes_kill_from_reset():
    schema = BashSessionsArgs.model_json_schema()

    action_description = schema["properties"]["action"]["description"]
    session_id_description = schema["properties"]["session_id"]["description"]

    assert "terminates exactly that one session" in action_description
    assert "stops every session in this tool family" in action_description
    assert "`kill` affects exactly this one session" in session_id_description


def test_bash_log_file_display_describes_actions_and_truncation():
    adapter = ToolUIDataAdapter(BashLogFile)
    call = adapter.get_call_display(
        ToolCallEvent(
            tool_call_id="call",
            tool_name="bash_log_file",
            tool_class=BashLogFile,
            args=BashLogFileArgs(action="read", session_id="bash_1"),
        )
    )
    result = adapter.get_result_display(
        ToolResultEvent(
            tool_call_id="call",
            tool_name="bash_log_file",
            tool_class=BashLogFile,
            result=BashLogFileResult(
                action="read",
                path="/tmp/bash_1.log",
                content="output",
                next_cursor=10,
                truncated=True,
            ),
        )
    )

    assert call.summary == "Reading bash log bash_1"
    assert call.verb == "Reading"
    assert call.message == "bash log bash_1"
    assert result.success is True
    assert result.verb == "Read"
    assert result.message == "bash log bash_1.log"
    assert result.suffix == "truncated"


@pytest.mark.skipif(is_windows(), reason="managed bash is POSIX-only")
@pytest.mark.asyncio
async def test_foreground_killed_session_is_reported_as_failure():
    terminal_runtime = TerminalRuntime()
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    started = await collect_result(
        tool.run(ExperimentalBashArgs(command="sleep 30", background=True))
    )
    sessions = BashSessions(
        config_getter=lambda: BashSessionsConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    await collect_result(
        sessions.run(BashSessionsArgs(action="kill", session_id=started.session_id))
    )

    with pytest.raises(ToolError):
        await tool._result_from_session(
            started.session_id, background=False, max_bytes=1000, enforce_success=True
        )


@pytest.mark.skipif(is_windows(), reason="managed bash is POSIX-only")
@pytest.mark.asyncio
async def test_cancelling_foreground_run_kills_the_session():
    terminal_runtime = TerminalRuntime()
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    streamed = asyncio.Event()

    async def consume() -> None:
        async for _ in tool.run(
            ExperimentalBashArgs(command="printf ready; sleep 30"),
            InvokeContext(tool_call_id="call-cancel"),
        ):
            streamed.set()

    task = asyncio.create_task(consume())
    await asyncio.wait_for(streamed.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    sessions = terminal_runtime.get().list_sessions()
    assert [info.status for info in sessions] == ["killed"]


@pytest.mark.skipif(is_windows(), reason="managed bash is POSIX-only")
@pytest.mark.asyncio
async def test_closing_the_generator_mid_stream_kills_the_session():
    terminal_runtime = TerminalRuntime()
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    gen = tool.run(
        ExperimentalBashArgs(command="printf ready; sleep 30"),
        InvokeContext(tool_call_id="call-aclose"),
    )

    assert isinstance(await anext(gen), ToolStreamEvent)
    await gen.aclose()

    sessions = terminal_runtime.get().list_sessions()
    assert [info.status for info in sessions] == ["killed"]


@pytest.mark.skipif(is_windows(), reason="managed bash is POSIX-only")
@pytest.mark.asyncio
async def test_closing_the_generator_keeps_a_background_session_running():
    terminal_runtime = TerminalRuntime()
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    manager = terminal_runtime.get()
    gen = tool.run(ExperimentalBashArgs(command="sleep 30", background=True))

    result = await anext(gen)
    await gen.aclose()

    assert not isinstance(result, ToolStreamEvent)
    assert manager.info(result.session_id).status == "running"
    manager.kill(result.session_id)


@pytest.mark.skipif(is_windows(), reason="managed bash is POSIX-only")
@pytest.mark.asyncio
async def test_closing_the_generator_keeps_a_soft_timeout_session_running():
    terminal_runtime = TerminalRuntime()
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    manager = terminal_runtime.get()
    gen = tool.run(ExperimentalBashArgs(command="sleep 30", timeout_seconds=0.5))

    result = await anext(gen)
    await gen.aclose()

    assert not isinstance(result, ToolStreamEvent)
    assert result.status == "running"
    assert manager.info(result.session_id).status == "running"
    manager.kill(result.session_id)


@pytest.mark.skipif(is_windows(), reason="managed bash is POSIX-only")
def test_reset_clear_logs_kills_running_sessions(tmp_path):
    manager = TerminalSessionManager()
    shell = manager.resolve_shell(None, None)
    session = manager.start(
        command="sleep 30", cwd=tmp_path, env=None, shell=shell, background=True
    )

    killed = manager.reset(clear_logs=True)

    assert any(info.session_id == session.session_id for info in killed)
    assert manager._sessions == {}
    assert session.terminal.poll() is not None


def test_manager_does_not_list_orphans_from_previous_vibe_session(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    sessions_dir = tmp_path / "bash-tool" / "sessions"
    sessions_dir.mkdir(parents=True)
    output_path = sessions_dir / "old.log"
    manifest_path = sessions_dir / "old.json"
    output_path.write_text("old output", encoding="utf-8")
    manifest_path.write_text(
        json.dumps({
            "session_id": "old",
            "command": "echo old",
            "cwd": str(tmp_path),
            "shell": "/bin/sh",
            "status": "orphaned",
            "exit_code": None,
            "output_path": str(output_path),
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "reader_error": None,
        }),
        encoding="utf-8",
    )

    manager = TerminalSessionManager()

    assert manager.list_sessions() == []
    assert output_path.exists()
    assert manifest_path.exists()


def test_manager_lists_only_own_family_orphaned_manifests(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    sessions_dir = tmp_path / "shell-tool" / "sessions"
    sessions_dir.mkdir(parents=True)

    for session_id in ("bash_old", "powershell_old"):
        output_path = sessions_dir / f"{session_id}.log"
        output_path.write_text(f"{session_id} output", encoding="utf-8")
        (sessions_dir / f"{session_id}.json").write_text(
            json.dumps({
                "session_id": session_id,
                "command": "echo old",
                "cwd": str(tmp_path),
                "shell": "/bin/sh",
                "status": "running",
                "exit_code": None,
                "output_path": str(output_path),
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "reader_error": None,
            }),
            encoding="utf-8",
        )

    bash_manager = TerminalSessionManager(
        backend=cast(ManagedShellBackend, _UnusedBackend()), session_prefix="bash"
    )
    windows_manager = TerminalSessionManager(
        backend=cast(ManagedShellBackend, _UnusedBackend()),
        shell_family="powershell",
        session_prefix="powershell",
    )

    bash_sessions = bash_manager.list_sessions()
    windows_sessions = windows_manager.list_sessions()

    assert [session.session_id for session in bash_sessions] == ["bash_old"]
    assert bash_sessions[0].status == "orphaned"
    assert [session.session_id for session in windows_sessions] == ["powershell_old"]
    assert windows_sessions[0].status == "orphaned"


def test_manager_log_relative_path_stays_within_own_session_family(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    sessions_dir = tmp_path / "shell-tool" / "sessions"
    sessions_dir.mkdir(parents=True)

    for session_id in ("bash_old", "powershell_old"):
        (sessions_dir / f"{session_id}.log").write_text(
            f"{session_id} output", encoding="utf-8"
        )

    bash_manager = TerminalSessionManager(
        backend=cast(ManagedShellBackend, _UnusedBackend()), session_prefix="bash"
    )
    windows_manager = TerminalSessionManager(
        backend=cast(ManagedShellBackend, _UnusedBackend()),
        shell_family="powershell",
        session_prefix="powershell",
    )

    assert (
        bash_manager.resolve_log_path(
            session_id=None, relative_path="sessions/bash_old.log"
        )
        == (sessions_dir / "bash_old.log").resolve()
    )
    with pytest.raises(ManagedShellError):
        bash_manager.resolve_log_path(
            session_id=None, relative_path="sessions/powershell_old.log"
        )

    assert (
        windows_manager.resolve_log_path(
            session_id=None, relative_path="sessions/powershell_old.log"
        )
        == (sessions_dir / "powershell_old.log").resolve()
    )
    with pytest.raises(ManagedShellError):
        windows_manager.resolve_log_path(
            session_id=None, relative_path="sessions/bash_old.log"
        )


def test_reset_clear_logs_deletes_only_own_family_files(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBE_HOME", str(tmp_path))
    sessions_dir = tmp_path / "shell-tool" / "sessions"
    sessions_dir.mkdir(parents=True)

    for session_id in ("bash_old", "powershell_old"):
        (sessions_dir / f"{session_id}.log").write_text(
            f"{session_id} output", encoding="utf-8"
        )
        (sessions_dir / f"{session_id}.json").write_text("{}", encoding="utf-8")

    manager = TerminalSessionManager(
        backend=cast(ManagedShellBackend, _UnusedBackend()), session_prefix="bash"
    )

    manager.reset(clear_logs=True)

    assert not (sessions_dir / "bash_old.log").exists()
    assert not (sessions_dir / "bash_old.json").exists()
    assert (sessions_dir / "powershell_old.log").exists()
    assert (sessions_dir / "powershell_old.json").exists()


def test_resolve_timeout_uses_shared_bash_default_timeout():
    config = ExperimentalBashToolConfig(default_timeout=300, max_timeout_seconds=600)
    bash_tool = ExperimentalBash(config_getter=lambda: config, state=BaseToolState())

    assert bash_tool._resolve_timeout(None) == 300
    assert bash_tool._resolve_timeout(50) == 50
    assert bash_tool._resolve_timeout(10_000) == 600


def test_build_env_neutralizes_pagers_but_keeps_interactive_term(monkeypatch):
    monkeypatch.delenv("TERM", raising=False)
    manager = TerminalSessionManager()

    env = manager._build_env(None)

    assert env["GIT_PAGER"] == "cat"
    assert env["PAGER"] == "cat"
    assert env["LESS"] == "-FX"
    assert env["TERM"] == "xterm-256color"


@pytest.mark.asyncio
async def test_background_session_can_be_polled_and_killed():
    terminal_runtime = TerminalRuntime()
    managed_bash = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    result = await collect_result(
        managed_bash.run(
            ExperimentalBashArgs(command="printf ready; sleep 30", background=True)
        )
    )
    output_tool = BashOutput(
        config_getter=lambda: BashOutputConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    sessions_tool = BashSessions(
        config_getter=lambda: BashSessionsConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )

    try:
        assert result.session_id
        assert result.background is True
        assert result.status == "running"

        output = await collect_result(
            output_tool.run(
                BashOutputArgs(session_id=result.session_id, cursor=0, wait_seconds=1)
            )
        )

        assert output.session_id == result.session_id
        assert "ready" in output.output
    finally:
        killed = await collect_result(
            sessions_tool.run(
                BashSessionsArgs(action="kill", session_id=result.session_id)
            )
        )
        assert killed.session is not None
        assert killed.session.status in {"killed", "completed"}


@pytest.mark.asyncio
async def test_stdin_can_drive_interactive_session():
    terminal_runtime = TerminalRuntime()
    managed_bash = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    result = await collect_result(
        managed_bash.run(
            ExperimentalBashArgs(
                command='read value; printf "answer=$value\\n"', background=True
            )
        )
    )
    stdin_tool = BashStdin(
        config_getter=lambda: BashStdinConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    output_tool = BashOutput(
        config_getter=lambda: BashOutputConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )
    sessions_tool = BashSessions(
        config_getter=lambda: BashSessionsConfig(),
        state=BaseToolState(),
        terminal_runtime=terminal_runtime,
    )

    try:
        await collect_result(
            stdin_tool.run(BashStdinArgs(session_id=result.session_id, text="ok\n"))
        )
        output = await collect_result(
            output_tool.run(
                BashOutputArgs(
                    session_id=result.session_id,
                    cursor=result.next_cursor,
                    wait_seconds=1,
                )
            )
        )

        assert output.status == "completed"
        assert "answer=ok" in output.output
    finally:
        await collect_result(
            sessions_tool.run(
                BashSessionsArgs(action="kill", session_id=result.session_id)
            )
        )


@pytest.mark.parametrize("predicate", ["-exec", "-execdir", "-ok", "-okdir"])
def test_find_execution_predicates_force_ask(predicate: str):
    config = BashToolConfig(permission=ToolPermission.ALWAYS)
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    permission = bash_tool.resolve_permission(
        BashArgs(command=f"find . {predicate} id \\;")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert [required.label for required in permission.required_permissions] == [
        f"find . {predicate} id \\;"
    ]


def test_find_exec_compound_includes_companion_required_permission():
    config = BashToolConfig(permission=ToolPermission.ALWAYS)
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    permission = bash_tool.resolve_permission(
        BashArgs(command='find . -exec id \\; && python3 -c "import os"')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    labels = {rp.label for rp in permission.required_permissions}
    assert any("find" in label for label in labels), (
        f"Expected a find-exec RequiredPermission, got {labels}"
    )
    assert any("python3" in label for label in labels), (
        f"Companion command should also require permission, got {labels}"
    )


def test_find_execution_predicate_does_not_override_denylist():
    config = BashToolConfig(denylist=["passwd"])
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    permission = bash_tool.resolve_permission(
        BashArgs(command="find . -exec id \\; && passwd root")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.NEVER
    assert "matches denylist pattern 'passwd'" in (permission.reason or "")


@pytest.mark.skipif(is_windows(), reason="outside-dir permissions are POSIX-only")
def test_legacy_bash_quoted_outside_path_requires_approval(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    bash_tool = Bash(
        config_getter=lambda: BashToolConfig(permission=ToolPermission.ASK),
        state=BaseToolState(),
    )

    permission = bash_tool.resolve_permission(BashArgs(command=f'cat "{outside}"'))

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside.parent) in required.label
        for required in permission.required_permissions
    )


@pytest.mark.skipif(is_windows(), reason="managed bash requires a POSIX-like platform")
def test_experimental_bash_quoted_outside_path_requires_approval(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    bash_tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(permission=ToolPermission.ASK),
        state=BaseToolState(),
    )

    permission = bash_tool.resolve_permission(
        ExperimentalBashArgs(command=f'cat "{outside}"')
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside.parent) in required.label
        for required in permission.required_permissions
    )


@pytest.mark.skipif(is_windows(), reason="managed bash requires a POSIX-like platform")
@pytest.mark.parametrize("command", ["grep root", "find", "od -c"])
def test_experimental_bash_readers_require_approval_for_outside_paths(
    command, tmp_path, monkeypatch
):
    workdir = tmp_path / "workdir"
    outside = tmp_path / "outside"
    workdir.mkdir()
    outside.mkdir()
    outside_file = outside / "secret.txt"
    outside_file.write_text("secret", encoding="utf-8")
    monkeypatch.chdir(workdir)
    bash_tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )

    permission = bash_tool.resolve_permission(
        ExperimentalBashArgs(command=f"{command} {outside_file}")
    )

    assert isinstance(permission, PermissionContext)
    assert permission.permission is ToolPermission.ASK
    assert any(
        str(outside) in required.label for required in permission.required_permissions
    )


def test_resolve_permission():
    config = BashToolConfig(allowlist=["echo", "pwd"], denylist=["rm"])
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    allowlisted = bash_tool.resolve_permission(BashArgs(command="echo hi"))
    denylisted = bash_tool.resolve_permission(BashArgs(command="rm -rf /tmp"))
    mixed = bash_tool.resolve_permission(BashArgs(command="pwd && whoami"))
    empty = bash_tool.resolve_permission(BashArgs(command=""))

    assert isinstance(allowlisted, PermissionContext)
    assert allowlisted.permission is ToolPermission.ALWAYS
    assert isinstance(denylisted, PermissionContext)
    assert denylisted.permission is ToolPermission.NEVER
    assert isinstance(mixed, PermissionContext)
    assert mixed.permission is ToolPermission.ASK
    assert any(rp.label == "whoami *" for rp in mixed.required_permissions)
    assert empty is None


class TestResolvePermissionWindowsSyntax:
    """Verify allowlist/denylist works with Windows-style commands."""

    def _make_bash(self, **kwargs) -> Bash:
        config = BashToolConfig(**kwargs)
        return Bash(config_getter=lambda: config, state=BaseToolState())

    def test_dir_with_windows_flags_allowlisted(self):
        bash_tool = self._make_bash(allowlist=["dir"])
        result = bash_tool.resolve_permission(BashArgs(command="dir /s /b"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_type_command_allowlisted(self):
        bash_tool = self._make_bash(allowlist=["type"])
        result = bash_tool.resolve_permission(BashArgs(command="type file.txt"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_findstr_allowlisted(self):
        bash_tool = self._make_bash(allowlist=["findstr"])
        result = bash_tool.resolve_permission(
            BashArgs(command="findstr /s pattern *.txt")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_ver_allowlisted(self):
        bash_tool = self._make_bash(allowlist=["ver"])
        result = bash_tool.resolve_permission(BashArgs(command="ver"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_where_allowlisted(self):
        bash_tool = self._make_bash(allowlist=["where"])
        result = bash_tool.resolve_permission(BashArgs(command="where python"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_cmd_k_denylisted(self):
        bash_tool = self._make_bash(denylist=["cmd /k"])
        result = bash_tool.resolve_permission(BashArgs(command="cmd /k something"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_powershell_noexit_denylisted(self):
        bash_tool = self._make_bash(denylist=["powershell -NoExit"])
        result = bash_tool.resolve_permission(BashArgs(command="powershell -NoExit"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_notepad_denylisted(self):
        bash_tool = self._make_bash(denylist=["notepad"])
        result = bash_tool.resolve_permission(BashArgs(command="notepad file.txt"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_cmd_standalone_denylisted(self):
        bash_tool = self._make_bash(denylist_standalone=["cmd"])
        result = bash_tool.resolve_permission(BashArgs(command="cmd"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_powershell_standalone_denylisted(self):
        bash_tool = self._make_bash(denylist_standalone=["powershell"])
        result = bash_tool.resolve_permission(BashArgs(command="powershell"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_powershell_cmdlet_asks(self):
        bash_tool = self._make_bash(allowlist=["dir", "echo"])
        result = bash_tool.resolve_permission(BashArgs(command="Get-ChildItem -Path ."))
        assert isinstance(result, PermissionContext)
        assert result.permission == ToolPermission.ASK

    def test_mixed_allowed_and_unknown_asks(self):
        bash_tool = self._make_bash(allowlist=["git status"])
        result = bash_tool.resolve_permission(
            BashArgs(command="git status && npm install")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission == ToolPermission.ASK

    def test_chained_windows_commands_all_allowed(self):
        bash_tool = self._make_bash(allowlist=["dir", "echo"])
        result = bash_tool.resolve_permission(BashArgs(command="dir /s && echo done"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_chained_commands_one_denied(self):
        bash_tool = self._make_bash(allowlist=["dir"], denylist=["rm"])
        result = bash_tool.resolve_permission(BashArgs(command="dir /s && rm -rf /"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_piped_windows_commands(self):
        bash_tool = self._make_bash(allowlist=["findstr", "type"])
        result = bash_tool.resolve_permission(
            BashArgs(command="type file.txt | findstr pattern")
        )
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS


class TestDenylistWordBoundary:
    """Verify denylist matches whole command names, not prefixes."""

    def _make_bash(self, **kwargs) -> Bash:
        config = BashToolConfig(**kwargs)
        return Bash(config_getter=lambda: config, state=BaseToolState())

    def test_vi_blocks_vi_exact(self):
        bash_tool = self._make_bash(denylist=["vi"])
        result = bash_tool.resolve_permission(BashArgs(command="vi"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_vi_blocks_vi_with_args(self):
        bash_tool = self._make_bash(denylist=["vi"])
        result = bash_tool.resolve_permission(BashArgs(command="vi file.txt"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_vi_does_not_block_vibe(self):
        bash_tool = self._make_bash(denylist=["vi"])
        result = bash_tool.resolve_permission(BashArgs(command="vibe -p hello"))
        assert result is None or result.permission is not ToolPermission.NEVER

    def test_multiword_pattern_still_works(self):
        bash_tool = self._make_bash(denylist=["bash -i"])
        result = bash_tool.resolve_permission(BashArgs(command="bash -i"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_multiword_pattern_with_trailing_args(self):
        bash_tool = self._make_bash(denylist=["bash -i"])
        result = bash_tool.resolve_permission(BashArgs(command="bash -i extra"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_multiword_pattern_does_not_match_partial(self):
        bash_tool = self._make_bash(denylist=["bash -i"])
        result = bash_tool.resolve_permission(BashArgs(command="bash -init"))
        assert result is None or result.permission is not ToolPermission.NEVER

    def test_deny_reason_is_set(self):
        bash_tool = self._make_bash(denylist=["vim"])
        result = bash_tool.resolve_permission(BashArgs(command="vim file.txt"))
        assert isinstance(result, PermissionContext)
        assert result.reason is not None
        assert "vim" in result.reason

    def test_standalone_deny_reason_is_set(self):
        bash_tool = self._make_bash(denylist_standalone=["python"])
        result = bash_tool.resolve_permission(BashArgs(command="python"))
        assert isinstance(result, PermissionContext)
        assert result.reason is not None
        assert result.permission is ToolPermission.NEVER
        assert "python" in result.reason
        assert "standalone" in result.reason

    def test_allowlist_does_not_match_prefix(self):
        bash_tool = self._make_bash(allowlist=["cat"])
        result = bash_tool.resolve_permission(BashArgs(command="catalog"))
        assert result is not None and result.permission is not ToolPermission.ALWAYS


def test_default_allowlist_includes_read_only_commands():
    """Test that common read-only commands are in the default allowlist."""
    from vibe.core.tools.builtins.bash import _get_default_allowlist

    allowlist = _get_default_allowlist()

    # Read-only commands that should be in the default allowlist
    read_only_commands = [
        "grep",
        "cut",
        "sort",
        "tr",
        "uniq",
        "basename",
        "comm",
        "date",
        "diff",
        "dirname",
        "du",
        "fmt",
        "fold",
        "join",
        "less",
        "md5sum",
        "more",
        "nl",
        "od",
        "paste",
        "readlink",
        "sha1sum",
        "sha256sum",
        "shasum",
        "stat",
        "sum",
        "tac",
        "which",
    ]

    for cmd in read_only_commands:
        assert cmd in allowlist, (
            f"Read-only command '{cmd}' should be in default allowlist"
        )


def test_new_read_only_commands_are_allowlisted():
    """Test that newly added read-only commands are automatically allowed."""
    config = BashToolConfig()  # Use default config
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    # Test that newly added read-only commands are allowed by default
    test_commands = [
        "grep pattern file.txt",
        "cut -d',' -f1 file.csv",
        "sort file.txt",
        "tr 'a' 'b' < file.txt",
        "uniq file.txt",
        "basename file.txt",
        "comm file1.txt file2.txt",
        "date",
        "diff file1.txt file2.txt",
        "dirname file.txt",
        "du -sh .",
        "fmt file.txt",
        "fold -w 80 file.txt",
        "join -t',' file1.csv file2.csv",
        "less file.txt",
        "md5sum file.txt",
        "more file.txt",
        "nl file.txt",
        "od -c file.bin",
        "paste file1.txt file2.txt",
        "readlink -f link.txt",
        "sha1sum file.txt",
        "sha256sum file.txt",
        "shasum file.txt",
        "stat file.txt",
        "sum file.txt",
        "tac file.txt",
        "which python",
    ]

    for cmd in test_commands:
        permission = bash_tool.resolve_permission(BashArgs(command=cmd))
        assert isinstance(permission, PermissionContext), (
            f"Permission should be PermissionContext for '{cmd}'"
        )
        assert permission.permission is ToolPermission.ALWAYS, (
            f"Command '{cmd}' should be always allowed"
        )


def _force_windows_bash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        "vibe.utils.platform.shutil.which",
        lambda name, path=None: (
            "C:\\Program Files\\Git\\bin\\bash.exe" if name == "bash" else None
        ),
    )


def _force_windows_cmd(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    _hide_standard_git_installs(monkeypatch)
    monkeypatch.setattr(
        "vibe.utils.platform.shutil.which", lambda name, path=None: None
    )


class TestShellAwareDefaultLists:
    def test_windows_bash_selects_posix_lists(self, monkeypatch):
        _force_windows_bash(monkeypatch)
        assert "ls" in default_read_only_commands()
        assert "dir" not in default_read_only_commands()
        assert "vim" in _get_default_denylist()
        assert "bash" in _get_default_denylist_standalone()

    def test_windows_cmd_selects_windows_lists(self, monkeypatch):
        _force_windows_cmd(monkeypatch)
        assert "dir" in default_read_only_commands()
        assert "ls" not in default_read_only_commands()
        assert "cmd /k" in _get_default_denylist()
        assert "notepad" in _get_default_denylist_standalone()


class TestResolvePermissionShellAware:
    """Permission analysis must run on Windows, gated by the resolved shell."""

    def _make_default_bash(self) -> Bash:
        config = BashToolConfig()
        return Bash(config_getter=lambda: config, state=BaseToolState())

    def test_windows_bash_allowlists_unix_read_only(self, monkeypatch):
        _force_windows_bash(monkeypatch)
        bash_tool = self._make_default_bash()
        result = bash_tool.resolve_permission(BashArgs(command="ls -la"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ALWAYS

    def test_windows_bash_denies_interactive_editor(self, monkeypatch):
        _force_windows_bash(monkeypatch)
        bash_tool = self._make_default_bash()
        result = bash_tool.resolve_permission(BashArgs(command="vim notes.txt"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.NEVER

    def test_windows_bash_asks_for_unknown_command(self, monkeypatch):
        _force_windows_bash(monkeypatch)
        bash_tool = self._make_default_bash()
        result = bash_tool.resolve_permission(BashArgs(command="frobnicate --now"))
        assert isinstance(result, PermissionContext)
        assert result.permission is ToolPermission.ASK

    def test_windows_cmd_defers_analysis_for_read_only(self, monkeypatch):
        _force_windows_cmd(monkeypatch)
        bash_tool = self._make_default_bash()
        # cmd.exe is not parsed with the bash grammar; defer to config (ASK).
        assert bash_tool.resolve_permission(BashArgs(command="dir /s /b")) is None

    def test_windows_cmd_defers_analysis_for_dangerous(self, monkeypatch):
        _force_windows_cmd(monkeypatch)
        bash_tool = self._make_default_bash()
        # Not NEVER-blocked via a bash parse; the human is asked instead.
        assert bash_tool.resolve_permission(BashArgs(command="cmd /k whoami")) is None


@pytest.mark.skipif(is_windows(), reason="managed bash requires a POSIX-like platform")
@pytest.mark.parametrize(
    "command",
    [
        "python3 << 'EOF'\nprint(42)\nEOF",
        "python3 - << 'EOF'\nprint(42)\nEOF",
        "python3 <<'PYEOF'\nimport sys\nprint('hello')\nPYEOF",
        "python3 < input.txt",
    ],
)
def test_experimental_bash_standalone_denylisted_with_redirect_not_denied(command):
    bash_tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )
    result = bash_tool.resolve_permission(ExperimentalBashArgs(command=command))
    assert isinstance(result, PermissionContext)
    assert result.permission is not ToolPermission.NEVER, (
        f"Command with redirect should not be denied: {command!r}"
    )
