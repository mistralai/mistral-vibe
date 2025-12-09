from __future__ import annotations

import pytest

from vibe.core.tools.base import BaseToolState, ToolError, ToolPermission
from vibe.core.tools.builtins.bash import Bash, BashArgs, BashToolConfig


@pytest.fixture
def bash(tmp_path):
    config = BashToolConfig(workdir=tmp_path)
    return Bash(config=config, state=BaseToolState())


@pytest.mark.asyncio
async def test_runs_echo_successfully(bash):
    result = await bash.run(BashArgs(command="echo hello"))

    assert result.returncode == 0
    assert result.stdout == "hello\n"
    assert result.stderr == ""


@pytest.mark.asyncio
async def test_fails_cat_command_with_missing_file(bash):
    with pytest.raises(ToolError) as err:
        await bash.run(BashArgs(command="cat missing_file.txt"))

    message = str(err.value)
    assert "Command failed" in message
    assert "Return code: 1" in message
    assert "No such file or directory" in message


@pytest.mark.asyncio
async def test_uses_effective_workdir(tmp_path):
    config = BashToolConfig(workdir=tmp_path)
    bash_tool = Bash(config=config, state=BaseToolState())

    result = await bash_tool.run(BashArgs(command="pwd"))

    assert result.stdout.strip() == str(tmp_path)


@pytest.mark.asyncio
async def test_handles_timeout(bash):
    with pytest.raises(ToolError) as err:
        await bash.run(BashArgs(command="sleep 2", timeout=1))

    assert "Command timed out after 1s" in str(err.value)


@pytest.mark.asyncio
async def test_truncates_output_to_max_bytes(bash):
    config = BashToolConfig(workdir=None, max_output_bytes=5)
    bash_tool = Bash(config=config, state=BaseToolState())

    result = await bash_tool.run(BashArgs(command="printf 'abcdefghij'"))

    assert result.stdout == "abcde"
    assert result.stderr == ""
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_decodes_non_utf8_bytes(bash):
    result = await bash.run(BashArgs(command="printf '\\xff\\xfe'"))

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
