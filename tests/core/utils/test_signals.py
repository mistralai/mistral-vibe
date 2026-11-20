from __future__ import annotations

import signal
import socket
import sys

import pytest

from vibe.core.utils.signals import waking_loop_on_signals

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")


@posix_only
@pytest.mark.asyncio
async def test_waking_the_loop_restores_the_previous_wakeup_fd() -> None:
    reader, writer = socket.socketpair()
    writer.setblocking(False)
    original = signal.set_wakeup_fd(writer.fileno())
    try:
        with waking_loop_on_signals():
            assert signal.set_wakeup_fd(-1) not in (-1, writer.fileno())
        assert signal.set_wakeup_fd(original) == writer.fileno()
    finally:
        reader.close()
        writer.close()
