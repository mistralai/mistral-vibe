"""Preload the Python Vibe CLI once, then fork one client per capture request."""

from __future__ import annotations

import fcntl
import json
import os
import socket
import sys
import termios
import time
from typing import Any

from vibe.cli import (
    cli as _cli,  # noqa: F401
    process_start,
)
from vibe.cli.entrypoint import main
from vibe.cli.textual_ui import app as _app  # noqa: F401

READY_LINE = b"zygote ready\n"
_BACKLOG = 64
_STD_FDS = (0, 1, 2)


def serve(socket_path: str) -> None:
    """Answer fork requests until the socket is closed or the process is killed."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    server.listen(_BACKLOG)
    sys.stdout.buffer.write(READY_LINE)
    sys.stdout.buffer.flush()
    while True:
        connection, _ = server.accept()
        with connection:
            _reap()
            _handle(server, connection)


def _handle(server: socket.socket, connection: socket.socket) -> None:
    """Fork one client and answer with its pid, or -1 if it died before setup."""
    request, tty = _receive_request(connection)
    ready_read, ready_write = os.pipe()
    pid = os.fork()
    if pid == 0:
        server.close()
        os.close(ready_read)
        _become_client(connection, request, ready_write, tty)
    os.close(tty)
    os.close(ready_write)
    started = bool(os.read(ready_read, 1))
    os.close(ready_read)
    connection.sendall(f"{pid if started else -1}\n".encode())


def _become_client(
    connection: socket.socket, request: dict[str, Any], ready: int, tty: int
) -> None:
    """Take over the requested PTY and run the CLI; never return to the server."""
    try:
        connection.close()
        os.setsid()
        _attach_terminal(tty)
        os.chdir(request["cwd"])
        os.environ.clear()
        os.environ.update(request["env"])
        sys.argv = list(request["command"])
        process_start.PROCESS_START_MONOTONIC = time.monotonic()
        os.write(ready, b"\1")
        os.close(ready)
        main()
    except BaseException:
        os._exit(127)
    os._exit(0)


def _attach_terminal(fd: int) -> None:
    """Make the received PTY slave this session's controlling terminal and stdio."""
    fcntl.ioctl(fd, termios.TIOCSCTTY, 0)
    for standard in _STD_FDS:
        os.dup2(fd, standard)
    if fd > max(_STD_FDS):
        os.close(fd)


def _receive_request(connection: socket.socket) -> tuple[dict[str, Any], int]:
    """Read one JSON request plus the PTY slave fd sent alongside it."""
    buffer, fds, _, _ = socket.recv_fds(connection, 65536, 1)
    buffer = bytearray(buffer)
    while not buffer.endswith(b"\n"):
        chunk = connection.recv(65536)
        if not chunk:
            break
        buffer += chunk
    return json.loads(bytes(buffer)), fds[0]


def _reap() -> None:
    """Collect finished clients; a handler would be inherited across the fork."""
    while True:
        try:
            if os.waitpid(-1, os.WNOHANG)[0] == 0:
                return
        except ChildProcessError:
            return


if __name__ == "__main__":
    serve(sys.argv[1])
