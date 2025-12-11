from __future__ import annotations

import pytest

from vibe.core.tools.base import BaseToolState, ToolError, ToolPermission
from vibe.core.tools.builtins.bash import Bash, BashArgs, BashToolConfig
from vibe.core.utils import is_windows


@pytest.fixture
def bash(tmp_path):
    config = BashToolConfig(workdir=tmp_path)
    return Bash(config=config, state=BaseToolState())


@pytest.mark.asyncio
async def test_runs_echo_successfully(bash):
    result = await bash.run(BashArgs(command="python -c \"print('hello')\""))

    assert result.returncode == 0
    assert result.stdout == ("hello\r\n" if is_windows() else "hello\n")
    assert result.stderr == ""


@pytest.mark.asyncio
async def test_fails_cat_command_with_missing_file(bash):
    with pytest.raises(ToolError) as err:
        await bash.run(BashArgs(command="python -c \"print(open('missing_file.txt').read())\""))

    message = str(err.value)
    # The error message varies by platform/python version but generally contains FileNotFoundError or similar
    assert "Command execution failed" in message or "Command failed" in message


@pytest.mark.asyncio
async def test_uses_effective_workdir(tmp_path):
    config = BashToolConfig(workdir=tmp_path)
    bash_tool = Bash(config=config, state=BaseToolState())

    result = await bash_tool.run(BashArgs(command="python -c \"import os; print(os.getcwd())\""))

    # Normalize paths for comparison (Windows vs Unix slashes)
    assert str(tmp_path.resolve()).lower() in result.stdout.lower()


@pytest.mark.asyncio
async def test_handles_timeout(bash):
    with pytest.raises(ToolError) as err:
        await bash.run(BashArgs(command="python -c \"import time; time.sleep(2)\"", timeout=1))

    assert "Command timed out after 1s" in str(err.value)


@pytest.mark.asyncio
async def test_truncates_output_to_max_bytes(bash):
    config = BashToolConfig(workdir=None, max_output_bytes=5)
    bash_tool = Bash(config=config, state=BaseToolState())

    result = await bash_tool.run(BashArgs(command="python -c \"print('abcdefghij', end='')\""))

    assert result.stdout == "abcde... (truncated)"
    assert result.stderr == ""
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_decodes_non_utf8_bytes(bash):
    result = await bash.run(BashArgs(command="python -c \"import sys; sys.stdout.buffer.write(b'\\xff\\xfe')\""))

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
