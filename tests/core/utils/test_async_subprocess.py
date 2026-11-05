from __future__ import annotations

import asyncio
import os
import signal
import sys

import pytest

from vibe.core.utils.async_subprocess import kill_async_subprocess


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups only")
@pytest.mark.asyncio
async def test_killing_a_group_whose_leader_already_died_kills_its_children() -> None:
    """*Prepare*: A shell in its own group that started a long `sleep`, then a
    kill of the shell alone, so `sleep` survives and holds the shell's pipes.
    *Do*: Kill the shell's process group.
    *Assert*: `sleep` is killed too, so the shell's pipe closes at once.
    """
    # Prepare
    shell = await asyncio.create_subprocess_shell(
        "sleep 30 & echo $!; wait",
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert shell.stdout is not None
    child_pid = int(await shell.stdout.readline())
    os.kill(shell.pid, signal.SIGKILL)
    while True:
        try:
            os.kill(shell.pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.01)

    # Do
    await asyncio.wait_for(kill_async_subprocess(shell), timeout=5)

    # Assert
    assert await asyncio.wait_for(shell.stdout.read(), timeout=5) == b""
    try:
        os.kill(child_pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _shell_in_its_own_group(command: str) -> asyncio.subprocess.Process:
    stdin, writer = os.pipe()
    os.close(writer)
    try:
        return await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=stdin,
            executable="/bin/sh",
            start_new_session=True,
        )
    finally:
        os.close(stdin)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups only")
@pytest.mark.asyncio
async def test_killing_a_shell_right_after_its_first_output_kills_its_next_command() -> (
    None
):
    """*Prepare*: Shells in their own group that print, then start `sleep`.
    *Do*: Kill each group as soon as the first output arrives, while the shell
        may still be starting `sleep`.
    *Assert*: Every kill returns quickly, and no `sleep` keeps the pipes open.
    """
    for _ in range(20):
        # Prepare
        shell = await _shell_in_its_own_group("printf 'slow'; sleep 30")
        assert shell.stdout is not None
        assert await shell.stdout.read(4) == b"slow"

        # Do
        await asyncio.wait_for(kill_async_subprocess(shell), timeout=3)

        # Assert
        assert await asyncio.wait_for(shell.stdout.read(), timeout=1) == b""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups only")
@pytest.mark.asyncio
async def test_a_kill_returns_although_a_process_that_left_the_group_holds_the_pipes() -> (
    None
):
    """*Prepare*: A shell whose child moved to its own session, out of reach of
        the group kill, and keeps the shell's pipes open.
    *Do*: Kill the shell's group.
    *Assert*: The kill returns within a second, with the shell killed.
    """
    # Prepare
    shell = await _shell_in_its_own_group(
        "python3 -c 'import os, time; os.setsid(); print(os.getpid(), flush=True);"
        " time.sleep(30)' & wait"
    )
    assert shell.stdout is not None
    escaped_pid = int(await asyncio.wait_for(shell.stdout.readline(), timeout=10))

    try:
        # Do
        await asyncio.wait_for(kill_async_subprocess(shell), timeout=1)

        # Assert
        assert shell.returncode == -signal.SIGKILL
    finally:
        os.kill(escaped_pid, signal.SIGKILL)
