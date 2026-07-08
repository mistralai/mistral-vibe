from __future__ import annotations

import json
import subprocess
from typing import cast

from pydantic import ValidationError
import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, ToolError, ToolPermission
from vibe.core.tools.builtins.bash import Bash, BashArgs, BashToolConfig
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
    ExperimentalBashToolConfig,
    TerminalSession,
    TerminalSessionManager,
)
from vibe.core.tools.builtins.managed_bash.backend import ManagedBashBackend
from vibe.core.tools.permissions import PermissionContext
from vibe.core.tools.ui import ToolUIDataAdapter
from vibe.core.types import ToolCallEvent, ToolResultEvent
from vibe.core.utils import is_windows


@pytest.fixture
def bash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = BashToolConfig()
    return Bash(config_getter=lambda: config, state=BaseToolState())


@pytest.mark.asyncio
async def test_runs_echo_successfully(bash):
    result = await collect_result(bash.run(BashArgs(command="echo hello")))

    assert result.returncode == 0
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
async def test_truncates_output_to_max_bytes(bash):
    config = BashToolConfig(max_output_bytes=5)
    bash_tool = Bash(config_getter=lambda: config, state=BaseToolState())

    result = await collect_result(
        bash_tool.run(BashArgs(command="printf 'abcdefghij'"))
    )

    assert result.stdout == "abcde"
    assert result.stderr == ""
    assert result.returncode == 0


@pytest.mark.skipif(is_windows(), reason="managed bash requires a POSIX-like platform")
@pytest.mark.asyncio
async def test_experimental_bash_keeps_compatibility_stderr_empty():
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )

    result = await collect_result(
        tool.run(ExperimentalBashArgs(command="printf err >&2"))
    )

    assert result.stdout == "err"
    assert result.output == "err"
    assert result.stderr == ""


@pytest.mark.asyncio
async def test_cat_preserves_accents_from_latin1_encoded_file(bash, tmp_path):
    file = tmp_path / "menu.txt"
    file.write_bytes("café au lait\nthé glacé\n".encode("latin-1"))

    result = await collect_result(bash.run(BashArgs(command=f"cat {file.name}")))

    assert result.returncode == 0
    assert "\ufffd" not in result.stdout
    assert result.stdout == "café au lait\nthé glacé\n"


class _SplitReadBackend:
    def __init__(self, fragments: list[bytes]) -> None:
        self._fragments = fragments

    def wait_readable(self, master_fd: int, timeout_seconds: float) -> bool:
        return bool(self._fragments)

    def read(self, master_fd: int, size: int) -> bytes:
        return self._fragments.pop(0) if self._fragments else b""

    def close_fd(self, fd: int) -> None:
        pass

    def terminate_process_group(
        self, process: subprocess.Popen[bytes], *, force: bool, grace_seconds: float
    ) -> None:
        pass


class _CompletedProcess:
    returncode = 0

    def poll(self) -> int:
        return 0

    def wait(self, timeout: float | None = None) -> int:
        return 0


class _RunningProcess:
    returncode: int | None = None

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        raise subprocess.TimeoutExpired("running", timeout or 0)


@pytest.mark.skipif(is_windows(), reason="managed bash is POSIX-only")
def test_posix_shell_resolution_prefers_zsh_fallback_but_honors_overrides(monkeypatch):
    from vibe.core.tools.builtins.managed_bash import _posix

    backend = _posix.PosixManagedBashBackend()
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
    backend = _SplitReadBackend([snowman[:2], snowman[2:]])
    manager = TerminalSessionManager(backend=cast(ManagedBashBackend, backend))
    output_path = tmp_path / "out.log"
    output_path.touch()
    session = TerminalSession(
        session_id="split",
        command="cmd",
        cwd=tmp_path,
        shell="/bin/sh",
        process=cast(subprocess.Popen[bytes], _CompletedProcess()),
        master_fd=1,
        output_path=output_path,
        manifest_path=tmp_path / "split.json",
        created_at=0.0,
    )

    manager._reader_loop(session)

    chunk = manager._read_file_chunk(output_path, cursor=0, max_bytes=64)
    assert chunk.output == "☃"
    assert "\ufffd" not in chunk.output


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
        process=cast(subprocess.Popen[bytes], _RunningProcess()),
        master_fd=1,
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


def test_inspect_session_does_not_split_multibyte_at_tail_boundary(tmp_path):
    manager = TerminalSessionManager()
    output_path = tmp_path / "out.log"
    output_path.write_bytes(b"aa" + "☃".encode() + b"z")
    session = TerminalSession(
        session_id="tail",
        command="cmd",
        cwd=tmp_path,
        shell="/bin/sh",
        process=cast(subprocess.Popen[bytes], _CompletedProcess()),
        master_fd=1,
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
    assert result.success is True
    assert result.message == "Session bash_1 is still running"
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
    assert result.success is True
    assert result.message == "Sent 3 bytes to running session bash_1"

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
    assert completed.message == "Sent 3 bytes to completed session bash_1"


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

    assert call.summary == "Killing bash session bash_1"
    assert result.success is True
    assert result.message == "Reset bash sessions; stopped 0 sessions"


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
    assert result.success is True
    assert result.message == "Read bash log bash_1.log"
    assert result.suffix == "truncated"


@pytest.mark.skipif(is_windows(), reason="managed bash is POSIX-only")
@pytest.mark.asyncio
async def test_foreground_killed_session_is_reported_as_failure():
    tool = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )
    started = await collect_result(
        tool.run(ExperimentalBashArgs(command="sleep 30", background=True))
    )
    sessions = BashSessions(
        config_getter=lambda: BashSessionsConfig(), state=BaseToolState()
    )
    await collect_result(
        sessions.run(BashSessionsArgs(action="kill", session_id=started.session_id))
    )

    with pytest.raises(ToolError):
        tool._result_from_session(
            started.session_id, background=False, max_bytes=1000, enforce_success=True
        )


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
    assert session.process.poll() is not None


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
    managed_bash = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )
    result = await collect_result(
        managed_bash.run(
            ExperimentalBashArgs(command="printf ready; sleep 30", background=True)
        )
    )
    output_tool = BashOutput(
        config_getter=lambda: BashOutputConfig(), state=BaseToolState()
    )
    sessions_tool = BashSessions(
        config_getter=lambda: BashSessionsConfig(), state=BaseToolState()
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
    managed_bash = ExperimentalBash(
        config_getter=lambda: ExperimentalBashToolConfig(), state=BaseToolState()
    )
    result = await collect_result(
        managed_bash.run(
            ExperimentalBashArgs(
                command='read value; printf "answer=$value\\n"', background=True
            )
        )
    )
    stdin_tool = BashStdin(
        config_getter=lambda: BashStdinConfig(), state=BaseToolState()
    )
    output_tool = BashOutput(
        config_getter=lambda: BashOutputConfig(), state=BaseToolState()
    )
    sessions_tool = BashSessions(
        config_getter=lambda: BashSessionsConfig(), state=BaseToolState()
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
        "basename /path/to/file",
        "comm file1.txt file2.txt",
        "date",
        "diff file1.txt file2.txt",
        "dirname /path/to/file",
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
        "readlink -f /path/to/link",
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
