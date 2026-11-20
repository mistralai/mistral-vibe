from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager, suppress
import signal
import socket
import sys


@contextmanager
def waking_loop_on_signals() -> Iterator[None]:
    if sys.platform == "win32":
        yield
        return
    loop = asyncio.get_running_loop()
    reader, writer = socket.socketpair()
    reader.setblocking(False)
    writer.setblocking(False)
    previous = signal.set_wakeup_fd(writer.fileno(), warn_on_full_buffer=False)
    loop.add_reader(reader.fileno(), _drain, reader)
    try:
        yield
    finally:
        loop.remove_reader(reader.fileno())
        signal.set_wakeup_fd(previous)
        reader.close()
        writer.close()


def _drain(reader: socket.socket) -> None:
    with suppress(BlockingIOError, InterruptedError):
        while reader.recv(4096):
            pass
