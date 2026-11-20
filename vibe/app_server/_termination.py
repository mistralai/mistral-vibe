"""SIGTERM ends the app-server as the end of its stdin does.

The tools it runs sit in sessions of their own, so only the app-server stops
them: a client gone without closing its stdin (on Linux, the kernel's
parent-death signal sends SIGTERM) or one that signals it must not leave them
running. POSIX only: on Windows the clients end the whole process tree.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
import logging
import os
import signal
import sys
import threading
from typing import Any

from vibe.app_server.transport import JsonRpcTransport
from vibe.core.utils.signals import waking_loop_on_signals
from vibe.observability.logging import logger

# Covers a tool's stop (its grace, then its kill: 2 s each) and the sessions'
# flush; past it the process exits without them.
TERMINATION_DEADLINE_S = 10.0
TERMINATED_EXIT_CODE = 128 + signal.SIGTERM


def _exit_now(code: int) -> None:
    """End the process at once: a stdin read still blocks a worker thread,
    which a normal exit would wait for.
    """
    # Not from the deadline's thread: the stuck one may hold a handler's lock.
    if threading.current_thread() is threading.main_thread():
        logging.shutdown()
    os._exit(code)


class InputEndingTransport:
    """``transport`` whose messages end once :meth:`end_input` is called, as
    they would at the end of the stream.
    """

    def __init__(self, transport: JsonRpcTransport) -> None:
        self._transport = transport
        self._input_ended = asyncio.Event()

    def end_input(self) -> None:
        self._input_ended.set()

    async def send(self, message: dict[str, Any]) -> None:
        await self._transport.send(message)

    async def close(self) -> None:
        await self._transport.close()

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        incoming = aiter(self._transport.messages())
        ended = asyncio.ensure_future(self._input_ended.wait())
        received: asyncio.Future[dict[str, Any]] | None = None
        try:
            while True:
                received = asyncio.ensure_future(anext(incoming))
                await asyncio.wait(
                    (received, ended), return_when=asyncio.FIRST_COMPLETED
                )
                if not received.done():
                    received.cancel()
                    await asyncio.wait((received,))
                    return
                try:
                    message = received.result()
                except StopAsyncIteration:
                    return
                yield message
        finally:
            ended.cancel()
            # A cancelled serving leaves its read running, which aclose() rejects.
            if received is not None and not received.done():
                received.cancel()
                await asyncio.wait((received,))
            aclose = getattr(incoming, "aclose", None)
            if aclose is not None:
                await aclose()


class _SigtermDeadline:
    """Arms the deadline from a thread started up front: the signal handler
    only writes to a pipe, so a SIGTERM landing while another is handled (uv
    forwards its own on top of a process-group kill) cannot re-enter it.
    """

    def __init__(
        self,
        deadline_s: float,
        exit_process: Callable[[int], None],
        on_signal: Callable[[], None],
    ) -> None:
        self._deadline_s = deadline_s
        self._exit_process = exit_process
        self._on_signal = on_signal
        self._signalled = False
        self._decided = threading.Event()
        self._wound_down = threading.Event()
        self._read, self._write = os.pipe()
        os.set_blocking(self._write, False)
        self._closed = False
        self._watcher = threading.Thread(
            target=self._watch, name="vibe-sigterm-deadline", daemon=True
        )
        self._watcher.start()

    def notify(self, _signum: int, _frame: object) -> None:
        try:
            os.write(self._write, b"\0")
        except OSError:
            pass  # Full: a byte already waits.

    def _watch(self) -> None:
        self._signalled = bool(os.read(self._read, 1))
        self._decided.set()
        if not self._signalled:
            return
        self._on_signal()
        if not self._wound_down.wait(self._deadline_s):
            self._exit_process(TERMINATED_EXIT_CODE)

    def stop(self) -> bool:
        """Whether SIGTERM came. Only once :meth:`notify` is no longer the
        handler: the pipe's descriptors are reused once closed.
        """
        if not self._closed:
            self._closed = True
            os.close(self._write)
            self._decided.wait()
            self._wound_down.set()
            self._watcher.join()
            os.close(self._read)
        return self._signalled


@contextmanager
def ending_on_sigterm(
    transport: JsonRpcTransport,
    *,
    deadline_s: float = TERMINATION_DEADLINE_S,
    exit_process: Callable[[int], None] | None = None,
) -> Iterator[JsonRpcTransport]:
    """Serve ``transport`` so SIGTERM ends its input; once the serving the
    block runs has wound down, or ``deadline_s`` after the signal, whichever
    comes first, the process exits with :data:`TERMINATED_EXIT_CODE`.
    """
    if sys.platform == "win32":
        yield transport
        return
    exit_process = exit_process or _exit_now
    loop = asyncio.get_running_loop()
    ending = InputEndingTransport(transport)

    def end_input() -> None:
        try:
            loop.call_soon_threadsafe(ending.end_input)
        except RuntimeError:
            pass  # The loop is closed: serving is over.

    deadline = _SigtermDeadline(deadline_s, exit_process, end_input)
    previous = signal.signal(signal.SIGTERM, deadline.notify)
    try:
        with waking_loop_on_signals():
            yield ending
    except Exception:
        signal.signal(signal.SIGTERM, previous)
        if not deadline.stop():
            raise
        logger.exception("App-server shutdown on SIGTERM failed")
    finally:
        signal.signal(signal.SIGTERM, previous)
        if deadline.stop():
            exit_process(TERMINATED_EXIT_CODE)
