from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal
import sys

import pytest

from vibe.core.utils.async_subprocess import _kill_process_group, kill_async_subprocess
from vibe.utils.platform import is_windows

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(is_windows(), reason="POSIX signals"),
]


async def test_exited_group_does_not_hide_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminated = asyncio.Event()

    def signal_group(group: int, signum: int) -> None:
        if signum == signal.SIGTERM:
            terminated.set()
        elif signum == signal.SIGKILL:
            raise ProcessLookupError("Group exited during the grace period")

    monkeypatch.setattr(os, "killpg", signal_group)
    task = asyncio.create_task(_kill_process_group(123, grace_seconds=10))
    await terminated.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_grace_allows_child_cleanup_after_group_leader_exits(
    tmp_path: Path,
) -> None:
    cleaned = tmp_path / "cleaned"
    script = """
import os, signal, sys, time
from pathlib import Path
if os.fork() == 0:
    def stop(signum, frame):
        time.sleep(0.1)
        Path(sys.argv[1]).write_text('remote job cancelled')
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    print('ready', flush=True)
while True:
    signal.pause()
"""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        script,
        str(cleaned),
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert proc.stdout is not None
        assert await asyncio.wait_for(proc.stdout.readline(), 5) == b"ready\n"
        await asyncio.wait_for(kill_async_subprocess(proc, grace_seconds=0.5), 5)
        assert cleaned.read_text() == "remote job cancelled"
        assert proc.returncode == -signal.SIGTERM
    finally:
        await kill_async_subprocess(proc)


@pytest.mark.parametrize("interrupt_grace", [False, True])
async def test_uncooperative_process_is_killed_within_grace(
    interrupt_grace: bool,
) -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); signal.pause()",
        stdout=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert proc.stdout is not None
        assert await asyncio.wait_for(proc.stdout.readline(), 5) == b"ready\n"
        task = asyncio.create_task(
            kill_async_subprocess(proc, grace_seconds=10 if interrupt_grace else 0.1)
        )
        if interrupt_grace:
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await asyncio.wait_for(task, 2)
        await asyncio.wait_for(proc.wait(), 2)
        assert proc.returncode == -signal.SIGKILL
    finally:
        await kill_async_subprocess(proc)
