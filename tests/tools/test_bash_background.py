from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, InvokeContext, ToolError
from vibe.core.tools.builtins.bash import Bash, BashArgs, BashToolConfig
from vibe.core.tools.builtins.bash_output import (
    BashOutput,
    BashOutputArgs,
    BashOutputToolConfig,
)


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    session = tmp_path / "session"
    session.mkdir()
    return session


@pytest.fixture
def ctx(session_dir: Path) -> InvokeContext:
    return InvokeContext(tool_call_id="test", session_dir=session_dir)


@pytest_asyncio.fixture
async def bash_tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = BashToolConfig()
    tool = Bash(config_getter=lambda: config, state=BaseToolState())
    try:
        yield tool
    finally:
        # Kill any background processes the test left behind so the
        # asyncio loop can shut down cleanly.
        await tool.on_reset()


@pytest.fixture
def bash_output_tool():
    config = BashOutputToolConfig()
    return BashOutput(config_getter=lambda: config, state=BaseToolState())


async def _wait_for_status(
    output_tool: BashOutput,
    ctx: InvokeContext,
    bash_id: str,
    target: str,
    timeout: float = 5.0,
) -> None:
    """Poll bash_output until it reports the desired status or the timeout
    elapses.  Keeps tests deterministic without sleeping blindly.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        result = await collect_result(
            output_tool.run(BashOutputArgs(bash_id=bash_id), ctx=ctx)
        )
        if result.status == target:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"bash {bash_id} did not reach status {target!r} within {timeout}s"
    )


@pytest.mark.asyncio
async def test_background_returns_immediately_with_id(bash_tool, ctx):
    result = await collect_result(
        bash_tool.run(
            BashArgs(command="sleep 0.2 && echo done", run_in_background=True), ctx=ctx
        )
    )

    assert result.background is True
    assert result.bash_id is not None
    assert result.stdout == ""
    assert result.stderr == ""
    assert result.returncode == 0

    # Metadata and output files should exist under the session directory
    bg_dir = ctx.session_dir / "bash_processes"
    assert (bg_dir / f"{result.bash_id}.json").exists()
    assert (bg_dir / f"{result.bash_id}.stdout").exists()
    assert (bg_dir / f"{result.bash_id}.stderr").exists()


@pytest.mark.asyncio
async def test_background_metadata_reflects_running_then_exited(
    bash_tool, bash_output_tool, ctx
):
    result = await collect_result(
        bash_tool.run(
            BashArgs(command="sleep 0.2 && echo hello", run_in_background=True), ctx=ctx
        )
    )
    bash_id = result.bash_id
    assert bash_id is not None

    # Initially running
    initial = await collect_result(
        bash_output_tool.run(BashOutputArgs(bash_id=bash_id), ctx=ctx)
    )
    assert initial.status in ("running", "exited")

    # Eventually exited with the expected output
    await _wait_for_status(bash_output_tool, ctx, bash_id, "exited")
    final = await collect_result(
        bash_output_tool.run(BashOutputArgs(bash_id=bash_id), ctx=ctx)
    )
    assert final.status == "exited"
    assert final.returncode == 0
    assert "hello" in final.stdout


@pytest.mark.asyncio
async def test_background_captures_stderr(bash_tool, bash_output_tool, ctx):
    result = await collect_result(
        bash_tool.run(
            BashArgs(command="sh -c 'echo oops >&2; exit 3'", run_in_background=True),
            ctx=ctx,
        )
    )
    bash_id = result.bash_id
    assert bash_id is not None

    await _wait_for_status(bash_output_tool, ctx, bash_id, "exited")
    final = await collect_result(
        bash_output_tool.run(BashOutputArgs(bash_id=bash_id), ctx=ctx)
    )
    assert final.status == "exited"
    assert final.returncode == 3
    assert "oops" in final.stderr
    assert final.stdout == ""


@pytest.mark.asyncio
async def test_background_requires_session(bash_tool):
    with pytest.raises(ToolError) as err:
        await collect_result(
            bash_tool.run(BashArgs(command="echo hi", run_in_background=True), ctx=None)
        )
    assert "session directory" in str(err.value)


@pytest.mark.asyncio
async def test_on_reset_terminates_background_process(bash_tool, bash_output_tool, ctx):
    result = await collect_result(
        bash_tool.run(BashArgs(command="sleep 30", run_in_background=True), ctx=ctx)
    )
    bash_id = result.bash_id
    assert bash_id is not None
    assert bash_id in bash_tool._background_processes

    await bash_tool.on_reset()

    assert bash_id not in bash_tool._background_processes

    # Metadata should reflect the terminated state
    metadata_path = ctx.session_dir / "bash_processes" / f"{bash_id}.json"
    metadata = json.loads(metadata_path.read_text())
    assert metadata["status"] == "terminated"

    # A subsequent bash_output read returns the terminated snapshot
    status_result = await collect_result(
        bash_output_tool.run(BashOutputArgs(bash_id=bash_id), ctx=ctx)
    )
    assert status_result.status == "terminated"


@pytest.mark.asyncio
async def test_bash_output_unknown_id_raises(bash_output_tool, ctx):
    with pytest.raises(ToolError) as err:
        await collect_result(
            bash_output_tool.run(BashOutputArgs(bash_id="does-not-exist"), ctx=ctx)
        )
    assert "does-not-exist" in str(err.value)


@pytest.mark.asyncio
async def test_bash_output_requires_session(bash_output_tool):
    with pytest.raises(ToolError) as err:
        await collect_result(
            bash_output_tool.run(BashOutputArgs(bash_id="abc"), ctx=None)
        )
    assert "session" in str(err.value).lower()


@pytest.mark.asyncio
async def test_foreground_still_works(bash_tool, ctx):
    """Regression check: the default path still returns synchronously
    and is unaffected by the new branching in run().
    """
    result = await collect_result(bash_tool.run(BashArgs(command="echo hi"), ctx=ctx))
    assert result.returncode == 0
    assert result.stdout == "hi\n"
    assert result.bash_id is None
    assert result.background is False


@pytest.mark.asyncio
async def test_multiple_background_processes_isolated(bash_tool, bash_output_tool, ctx):
    r1 = await collect_result(
        bash_tool.run(BashArgs(command="echo one", run_in_background=True), ctx=ctx)
    )
    r2 = await collect_result(
        bash_tool.run(BashArgs(command="echo two", run_in_background=True), ctx=ctx)
    )
    assert r1.bash_id != r2.bash_id

    await _wait_for_status(bash_output_tool, ctx, r1.bash_id, "exited")
    await _wait_for_status(bash_output_tool, ctx, r2.bash_id, "exited")

    out1 = await collect_result(
        bash_output_tool.run(BashOutputArgs(bash_id=r1.bash_id), ctx=ctx)
    )
    out2 = await collect_result(
        bash_output_tool.run(BashOutputArgs(bash_id=r2.bash_id), ctx=ctx)
    )

    assert "one" in out1.stdout
    assert "two" in out2.stdout
    assert "two" not in out1.stdout
    assert "one" not in out2.stdout
