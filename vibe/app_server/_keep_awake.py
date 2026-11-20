"""Keep the machine from idle-sleeping while a session of this process runs a turn.

One OS request for the whole process, held while at least one session has a turn
running or waiting on an approval or a question, as Vibe Desktop does for its
local sessions. Only idle sleep is prevented: the display still sleeps, and closing
the lid or choosing Sleep still puts the machine to sleep.

A worker thread owns the request, so starting or stopping it never blocks the event
loop, and Windows sets and resets its thread-scoped request on one thread. Each
platform's request ends with the process, so a crash never leaves the machine awake:

* macOS: ``caffeinate -i -w <pid>``.
* Linux: an ``idle`` lock from ``systemd-inhibit``, held by a ``cat`` reading a pipe
  this process holds; the pipe closes with the process. Desktop environments that
  run their own idle timer may not honour logind's idle locks.
* Windows: ``SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)``; Windows
  drops it when the thread ends.

Elsewhere, or when the platform's tool is missing, sessions run as before.
"""

from __future__ import annotations

from collections.abc import Callable
import os
import subprocess
import sys
import threading

from vibe.observability.logging import logger

type Release = Callable[[], None]
type Acquire = Callable[[], Release | None]

_WHY = "A Vibe turn is running"
_STOP_TIMEOUT_S = 1.0
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


class KeepAwake:
    """Holds the process's request while any holder is active.

    ``update`` and ``close`` only record what is wanted; the worker thread starts
    and stops the request, and is the only thread that calls ``acquire`` and the
    release it returns.
    """

    def __init__(self, acquire: Acquire | None = None) -> None:
        self._acquire = acquire or platform_acquire
        self._holders: set[object] = set()
        self._closed = False
        # What the worker has applied: whether it holds the request, whether it
        # is changing that now, and whether it has stopped for good.
        self._holding = False
        self._applying = False
        self._finished = False
        self._changed = threading.Condition()
        self._worker: threading.Thread | None = None

    @property
    def requested(self) -> bool:
        """Whether a holder wants the machine awake, applied or not yet."""
        with self._changed:
            return bool(self._holders)

    def update(self, holder: object, *, active: bool) -> None:
        """Record whether ``holder`` needs the machine awake now. Never blocks."""
        with self._changed:
            if self._closed:
                return
            wanted = bool(self._holders)
            if active:
                self._holders.add(holder)
            else:
                self._holders.discard(holder)
            if bool(self._holders) == wanted:
                return
            if self._worker is None:
                worker = threading.Thread(
                    target=self._run, name="vibe-keep-awake", daemon=True
                )
                worker.start()
                self._worker = worker
            self._changed.notify_all()

    def settle(self, timeout: float | None = None) -> bool:
        """Wait until the worker applied the last update; False on timeout."""
        with self._changed:
            return self._changed.wait_for(self._settled, timeout)

    def close(self, timeout: float = _STOP_TIMEOUT_S * 2) -> None:
        """Release the request for good; later updates change nothing."""
        with self._changed:
            self._closed = True
            self._holders.clear()
            worker = self._worker
            self._changed.notify_all()
        if worker is not None:
            worker.join(timeout)

    def _settled(self) -> bool:
        if self._worker is None:
            return True
        if self._closed:
            return self._finished
        return not self._applying and self._holding == bool(self._holders)

    def _run(self) -> None:
        release: Release | None = None
        while True:
            with self._changed:
                self._changed.wait_for(
                    lambda: self._closed or bool(self._holders) != self._holding
                )
                want = bool(self._holders)
                holding = self._holding
                self._applying = True
            if want and not holding:
                release = self._safely_acquire()
            elif not want and holding:
                self._safely_release(release)
                release = None
            with self._changed:
                self._holding = want
                self._applying = False
                if self._closed and not want:
                    self._finished = True
                self._changed.notify_all()
                if self._finished:
                    return

    def _safely_acquire(self) -> Release | None:
        try:
            return self._acquire()
        except Exception:
            logger.warning("Failed to keep the machine awake", exc_info=True)
            return None

    @staticmethod
    def _safely_release(release: Release | None) -> None:
        if release is None:
            return
        try:
            release()
        except Exception:
            logger.warning("Failed to let the machine sleep again", exc_info=True)


def platform_acquire() -> Release | None:
    """Start this platform's request on the calling thread; None when it has none."""
    if sys.platform == "darwin":
        return _start_process(["caffeinate", "-i", "-w", str(os.getpid())])
    if sys.platform == "win32":
        return _set_windows_request()
    if sys.platform.startswith("linux"):
        # ``cat`` waits on a pipe only this process writes to: when the process
        # exits, even killed, the pipe closes, ``cat`` ends and the lock with it.
        return _start_process(
            [
                "systemd-inhibit",
                "--what=idle",
                "--who=Vibe",
                f"--why={_WHY}",
                "--mode=block",
                "cat",
            ],
            stdin=subprocess.PIPE,
        )
    logger.info("Keeping the machine awake is not supported on %s", sys.platform)
    return None


def _start_process(command: list[str], *, stdin: int | None = None) -> Release | None:
    try:
        process = subprocess.Popen(
            command,
            stdin=stdin if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as exc:
        logger.info("Cannot keep the machine awake with %s: %s", command[0], exc)
        return None

    def release() -> None:
        if process.stdin is not None:
            process.stdin.close()
        process.terminate()
        try:
            process.wait(timeout=_STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=_STOP_TIMEOUT_S)

    return release


def _set_windows_request() -> Release | None:
    # Imported here: ``windll`` exists on Windows only.
    import ctypes

    set_state = ctypes.windll.kernel32.SetThreadExecutionState  # pyright: ignore[reportAttributeAccessIssue]
    set_state.argtypes = [ctypes.c_uint32]
    set_state.restype = ctypes.c_uint32
    if not set_state(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED):
        logger.info("SetThreadExecutionState refused to keep the machine awake")
        return None

    def release() -> None:
        # The request belongs to the thread that set it: the worker resets it too.
        set_state(_ES_CONTINUOUS)

    return release


__all__ = ["Acquire", "KeepAwake", "Release", "platform_acquire"]
