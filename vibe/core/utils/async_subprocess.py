from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import os
import signal

from vibe.utils.platform import is_windows

logger = logging.getLogger(__name__)

# How long a killed process group gets to exit and close its pipes, once after
# the first signal and once after the second.
_GROUP_EXIT_GRACE_SECONDS = 0.25


async def kill_async_subprocess(
    proc: asyncio.subprocess.Process, *, kill_process_group: bool = True
) -> None:
    """Force-terminate an asyncio child process and wait until it exits.

    With ``kill_process_group=True`` (default), on Unix this sends ``SIGKILL``
    to the child's process group via :func:`os.killpg`. Only use that mode
    when the child is isolated in its own group (for example
    ``start_new_session=True`` with ``create_subprocess_shell``); otherwise the
    group id may match the parent's and unrelated processes could be killed.

    On Windows, ``kill_process_group=True`` runs ``taskkill /F /T`` to kill the
    process tree.

    With ``kill_process_group=False``, :meth:`asyncio.subprocess.Process.kill`
    is used on all platforms (typical for a single ``create_subprocess_exec``
    leaf such as ``grep``).

    On Unix with ``kill_process_group=True``, waiting is bounded: a process that
    left the group can keep the child's pipes open for as long as it runs, so
    after two signals this returns without waiting for those pipes to close.
    """
    # A group can outlive its leader: a killed shell leaves its children
    # running and holding its pipes, so the group is killed even then.
    if proc.returncode is not None and not (kill_process_group and not is_windows()):
        return

    try:
        if not kill_process_group:
            proc.kill()
        elif is_windows():
            try:
                subprocess_proc = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/F",
                    "/T",
                    "/PID",
                    str(proc.pid),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await subprocess_proc.wait()
            except (FileNotFoundError, OSError):
                proc.terminate()
        else:
            await _kill_process_group(proc)
            return

        await proc.wait()
    except (ProcessLookupError, PermissionError, OSError):
        pass


async def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    _signal_process_group(proc.pid)
    with suppress(TimeoutError):
        await asyncio.wait_for(proc.wait(), _GROUP_EXIT_GRACE_SECONDS)
        return
    # Something still holds the pipes. A process the shell was starting as the
    # signal went out joins the group too late to receive it. The shell is dead
    # by now and starts nothing new, so a second signal reaches that process.
    _signal_process_group(proc.pid)
    with suppress(TimeoutError):
        await asyncio.wait_for(proc.wait(), _GROUP_EXIT_GRACE_SECONDS)


def _signal_process_group(pgid: int) -> None:
    try:
        # The child leads its own group, so the group ID is its PID.
        # os.getpgid() fails once the leader has exited.
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    except Exception:
        logger.debug("Unexpected error killing process group %s", pgid, exc_info=True)
