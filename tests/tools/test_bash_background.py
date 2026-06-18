from __future__ import annotations

import pytest
import pytest_asyncio

from tests.mock.utils import collect_result
from vibe.core.tools.background_shells import get_background_shell_registry
from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.bash import Bash, BashArgs, BashToolConfig
from vibe.core.tools.builtins.bash_output import (
    BashOutput,
    BashOutputArgs,
    BashOutputConfig,
)
from vibe.core.tools.builtins.kill_shell import (
    KillShell,
    KillShellArgs,
    KillShellConfig,
)


@pytest.fixture
def bash():
    return Bash(config_getter=lambda: BashToolConfig(), state=BaseToolState())


@pytest.fixture
def bash_output():
    return BashOutput(config_getter=lambda: BashOutputConfig(), state=BaseToolState())


@pytest.fixture
def kill_shell():
    return KillShell(config_getter=lambda: KillShellConfig(), state=BaseToolState())


@pytest_asyncio.fixture(autouse=True)
async def _clean_registry():
    yield
    await get_background_shell_registry().aclose_all()


@pytest.mark.asyncio
async def test_run_in_background_returns_shell_id(bash):
    result = await collect_result(
        bash.run(BashArgs(command="echo hello", run_in_background=True))
    )

    assert result.shell_id is not None
    assert result.shell_id.startswith("bg_")
    assert get_background_shell_registry().get(result.shell_id) is not None


@pytest.mark.asyncio
async def test_bash_output_reads_accumulated_output(bash, bash_output):
    started = await collect_result(
        bash.run(BashArgs(command="echo hello-world", run_in_background=True))
    )
    shell = get_background_shell_registry().get(started.shell_id)
    assert shell is not None
    await shell.wait()

    result = await collect_result(
        bash_output.run(BashOutputArgs(shell_id=started.shell_id))
    )

    assert "hello-world" in result.stdout
    assert result.running is False
    assert result.returncode == 0


@pytest.mark.asyncio
async def test_bash_output_only_returns_new_output(bash, bash_output):
    started = await collect_result(
        bash.run(BashArgs(command="echo once", run_in_background=True))
    )
    shell = get_background_shell_registry().get(started.shell_id)
    assert shell is not None
    await shell.wait()

    first = await collect_result(
        bash_output.run(BashOutputArgs(shell_id=started.shell_id))
    )
    second = await collect_result(
        bash_output.run(BashOutputArgs(shell_id=started.shell_id))
    )

    assert "once" in first.stdout
    assert second.stdout == ""


@pytest.mark.asyncio
async def test_bash_output_unknown_shell_raises(bash_output):
    with pytest.raises(ToolError):
        await collect_result(bash_output.run(BashOutputArgs(shell_id="bg_nope")))


@pytest.mark.asyncio
async def test_kill_shell_terminates_and_unregisters(bash, kill_shell):
    started = await collect_result(
        bash.run(BashArgs(command="sleep 30", run_in_background=True))
    )

    result = await collect_result(
        kill_shell.run(KillShellArgs(shell_id=started.shell_id))
    )

    assert result.killed is True
    assert get_background_shell_registry().get(started.shell_id) is None


@pytest.mark.asyncio
async def test_kill_shell_unknown_shell_raises(kill_shell):
    with pytest.raises(ToolError):
        await collect_result(kill_shell.run(KillShellArgs(shell_id="bg_nope")))


@pytest.mark.asyncio
async def test_foreground_still_works(bash):
    result = await collect_result(bash.run(BashArgs(command="echo fg")))

    assert result.shell_id is None
    assert "fg" in result.stdout
