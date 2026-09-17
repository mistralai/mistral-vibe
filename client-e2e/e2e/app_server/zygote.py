"""Launch the Python Vibe client by forking a preloaded process, skipping imports."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile

from e2e.app_server.config import PYTHON_BIN, VIBE_DIR, ZYGOTE_CLIENT, ZYGOTE_ENV_VAR
from e2e.pty.capture import CaptureSpec, Launcher, exec_launch, open_terminal

_SERVER = Path(__file__).with_name("_zygote_server.py")
_READY = "zygote ready"


class Zygote:
    """A preloaded Python CLI process that forks one client per capture."""

    def __init__(self, socket_path: str, process: subprocess.Popen[str]) -> None:
        self._socket_path = socket_path
        self._process = process

    def launch(self, spec: CaptureSpec) -> tuple[int, int]:
        """Fork one client onto a fresh PTY and return its pid and master fd."""
        master, slave = open_terminal(spec.rows, spec.columns)
        request = {
            "command": list(spec.command),
            "cwd": spec.cwd,
            "env": dict(spec.env),
        }
        try:
            pid = self._request(request, slave)
        finally:
            os.close(slave)
        if pid < 0:
            os.close(master)
            raise RuntimeError("zygote client died before taking over its terminal")
        return pid, master

    def _request(self, request: dict[str, object], slave: int) -> int:
        # The slave fd travels with the request: `os.ttyname` fails with ERANGE
        # once macOS hands out high pty numbers under parallel workers.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.connect(self._socket_path)
            socket.send_fds(client, [json.dumps(request).encode() + b"\n"], [slave])
            with client.makefile("rb") as reply:
                answer = reply.readline()
        if not answer:
            raise RuntimeError("zygote closed the connection without forking")
        return int(answer)

    def close(self) -> None:
        self._process.kill()
        self._process.wait()
        if self._process.stdout is not None:
            self._process.stdout.close()


@contextmanager
def zygote() -> Iterator[Zygote | None]:
    """Yield a running zygote, or None when it is disabled or unavailable."""
    if os.environ.get(ZYGOTE_ENV_VAR) == "0" or not PYTHON_BIN.exists():
        yield None
        return
    directory = tempfile.mkdtemp(prefix="e2e_zygote_")
    process = subprocess.Popen(
        [os.fspath(PYTHON_BIN), os.fspath(_SERVER), os.path.join(directory, "socket")],
        cwd=os.fspath(VIBE_DIR),
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    started = Zygote(os.path.join(directory, "socket"), process)
    try:
        assert process.stdout is not None
        if process.stdout.readline().strip() != _READY:
            raise RuntimeError("zygote failed to preload the Python Vibe CLI")
        yield started
    finally:
        started.close()
        shutil.rmtree(directory, ignore_errors=True)


def launcher(running: Zygote | None, client: str) -> Launcher:
    """Return the launcher for one client: the zygote for Python, exec otherwise."""
    if running is None or client != ZYGOTE_CLIENT:
        return exec_launch
    return running.launch
