from __future__ import annotations

import asyncio
import os

import pytest

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, InvokeContext, ToolError, ToolPermission
from vibe.core.tools.builtins.bash import (
    Bash,
    BashArgs,
    BashToolConfig,
)
from vibe.core.types import ToolStreamEvent


@pytest.fixture
def bash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = BashToolConfig()
    return Bash(config=config, state=BaseToolState())


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
    bash_tool = Bash(config=config, state=BaseToolState())

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
    bash_tool = Bash(config=config, state=BaseToolState())

    result = await collect_result(
        bash_tool.run(BashArgs(command="printf 'abcdefghij'"))
    )

    assert result.stdout == "abcde"
    assert result.stderr == ""
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_decodes_non_utf8_bytes(bash):
    result = await collect_result(bash.run(BashArgs(command="printf '\\xff\\xfe'")))

    # accept both possible encodings, as some shells emit escaped bytes as literal strings
    assert result.stdout in {"��", "\xff\xfe", r"\xff\xfe"}
    assert result.stderr == ""


def test_check_allowlist_denylist():
    config = BashToolConfig(allowlist=["echo", "pwd"], denylist=["rm"])
    bash_tool = Bash(config=config, state=BaseToolState())

    allowlisted = bash_tool.check_allowlist_denylist(BashArgs(command="echo hi"))
    denylisted = bash_tool.check_allowlist_denylist(BashArgs(command="rm -rf /tmp"))
    mixed = bash_tool.check_allowlist_denylist(BashArgs(command="pwd && whoami"))
    empty = bash_tool.check_allowlist_denylist(BashArgs(command=""))

    assert allowlisted is ToolPermission.ALWAYS
    assert denylisted is ToolPermission.NEVER
    assert mixed is None
    assert empty is None


@pytest.mark.asyncio
async def test_streams_progress_for_long_running_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.tools.builtins.bash._PROGRESS_UPDATE_INTERVAL_SECONDS",
        0,
    )
    wait_for_calls = 0
    original_wait_for = asyncio.wait_for

    class _FakeProcess:
        returncode = 0
        pid = os.getpid()

        async def communicate(self) -> tuple[bytes, bytes]:
            await asyncio.sleep(0)
            return b"", b""

    async def _fake_create_subprocess_shell(*_args, **_kwargs) -> _FakeProcess:
        return _FakeProcess()

    async def _fake_wait_for(awaitable, timeout=None):
        nonlocal wait_for_calls
        wait_for_calls += 1
        if wait_for_calls == 1:
            raise TimeoutError
        return await original_wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(
        "vibe.core.tools.builtins.bash.asyncio.create_subprocess_shell",
        _fake_create_subprocess_shell,
    )
    monkeypatch.setattr(
        "vibe.core.tools.builtins.bash.asyncio.wait_for",
        _fake_wait_for,
    )

    config = BashToolConfig()
    bash_tool = Bash(config=config, state=BaseToolState())
    ctx = InvokeContext(tool_call_id="call-123")

    stream_events: list[ToolStreamEvent] = []
    result = None
    async for item in bash_tool.run(BashArgs(command="echo ok"), ctx=ctx):
        if isinstance(item, ToolStreamEvent):
            stream_events.append(item)
        else:
            result = item

    assert result is not None
    assert stream_events
    assert stream_events[0].tool_name == "bash"
    assert stream_events[0].tool_call_id == "call-123"
    assert "elapsed" in stream_events[0].message
    assert "Ctrl+C" in stream_events[0].message
