from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal
import sys
import time

import pytest

from vibe.app_server._shell import ShellController, manual_shell_context
from vibe.app_server.protocol import ShellRunParams, ShellRunResponse


def test_manual_shell_context_caps_stdout_and_stderr_independently() -> None:
    result = ShellRunResponse(
        operation_id="shell-1",
        command="build",
        cwd="/workspace",
        stdout="o" * 10,
        stderr="e" * 10,
        exit_code=1,
    )

    context = manual_shell_context(result, max_output_bytes=5)

    assert context.count("[truncated]") == 2
    assert "oooooo" not in context
    assert "eeeeee" not in context


@pytest.mark.asyncio
async def test_shell_interrupt_is_sticky_before_process_spawn(tmp_path: Path) -> None:
    controller = ShellController(tmp_path)
    operation_id = "shell-early-interrupt"

    async def interrupt_on_start() -> None:
        assert await controller.interrupt(operation_id)

    result = await controller.run(
        ShellRunParams(
            session_id="session-1",
            operation_id=operation_id,
            command="sleep 10",
            cwd=str(tmp_path),
        ),
        observe_start=interrupt_on_start,
    )

    assert result.interrupted is True


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX shell commands")
@pytest.mark.asyncio
async def test_an_interrupt_returns_although_an_escaped_process_holds_the_output(
    tmp_path: Path,
) -> None:
    """*Prepare*: A running command whose child moved to its own session, out of
        reach of the interrupt, and keeps the command's output open.
    *Do*: Interrupt the command.
    *Assert*: The run returns as interrupted within seconds, with the output
        that arrived before the interrupt.
    """
    # Prepare
    controller = ShellController(tmp_path)
    operation_id = "shell-escaped-child"
    escaped: list[int] = []

    async def interrupt_once_escaped(text: str) -> None:
        if not escaped and text.strip().isdigit():
            escaped.append(int(text))
            asyncio.get_running_loop().create_task(controller.interrupt(operation_id))

    try:
        # Do
        result = await asyncio.wait_for(
            controller.run(
                ShellRunParams(
                    session_id="session-1",
                    operation_id=operation_id,
                    command=(
                        "python3 -c 'import os, time; os.setsid();"
                        " print(os.getpid(), flush=True); time.sleep(30)' & wait"
                    ),
                    cwd=str(tmp_path),
                ),
                observe_output=interrupt_once_escaped,
            ),
            timeout=5,
        )

        # Assert
        assert result.interrupted is True
        assert result.stdout == f"{escaped[0]}\n"
    finally:
        for pid in escaped:
            os.kill(pid, signal.SIGKILL)


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX shell commands")
@pytest.mark.asyncio
async def test_a_command_waits_for_the_background_job_it_started(
    tmp_path: Path,
) -> None:
    """*Prepare*: A command that starts a background job holding its output.
    *Do*: Run it to completion.
    *Assert*: The run returns only once the job ended, with the job's output.
    """
    # Prepare
    controller = ShellController(tmp_path)
    started = time.monotonic()

    # Do
    result = await controller.run(
        ShellRunParams(
            session_id="session-1",
            operation_id="shell-background-job",
            command="(sleep 1; echo job done) & echo started",
            cwd=str(tmp_path),
        )
    )

    # Assert
    assert time.monotonic() - started >= 1
    assert result.stdout == "started\njob done\n"
    assert result.interrupted is False
