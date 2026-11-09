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
    deadline = threading.Timer(deadline_s, exit_process, (TERMINATED_EXIT_CODE,))
    deadline.daemon = True

    def terminate(_signum: int, _frame: object) -> None:
        if deadline.ident is not None:
            return
        # A Python handler, not the loop's: it arms the deadline even while a
        # callback holds the loop.
        deadline.start()
        loop.call_soon_threadsafe(ending.end_input)

    previous = signal.signal(signal.SIGTERM, terminate)
    try:
        yield ending
    except Exception:
        if deadline.ident is None:
            raise
        logger.exception("App-server shutdown on SIGTERM failed")
    finally:
        signal.signal(signal.SIGTERM, previous)
        if deadline.ident is not None:
            deadline.cancel()
            exit_process(TERMINATED_EXIT_CODE)
