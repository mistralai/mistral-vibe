"""Exec trampoline that creates a POSIX controlling terminal without preexec_fn."""

import fcntl
import json
import os
import signal
import struct
import sys
import termios
from typing import Literal, TypedDict, cast

_MAX_REQUEST_BYTES = 16 * 1024 * 1024


class PosixLaunchRequest(TypedDict):
    version: Literal[1]
    shell: str
    argv: list[str]
    cwd: str
    env: dict[str, str]


def run(request_fd: int, status_fd: int, slave_fd: int) -> None:
    stage = "request"
    try:
        request = _read_request(request_fd)
        os.close(request_fd)
        stage = "setsid"
        os.setsid()
        stage = "controlling_terminal"
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        stage = "chdir"
        os.chdir(request["cwd"])
        stage = "stdio"
        for descriptor in (0, 1, 2):
            os.dup2(slave_fd, descriptor)
        if slave_fd > 2:
            os.close(slave_fd)
        if hasattr(signal, "pthread_sigmask"):
            signal.pthread_sigmask(signal.SIG_SETMASK, [])
        for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
            if sig := getattr(signal, name, None):
                signal.signal(sig, signal.SIG_DFL)
        flags = fcntl.fcntl(status_fd, fcntl.F_GETFD)
        fcntl.fcntl(status_fd, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
        os.write(status_fd, b"R")
        stage = "exec"
        os.execve(request["shell"], request["argv"], request["env"])
    except BaseException as error:
        _write_error(status_fd, stage, getattr(error, "errno", None))
        raise SystemExit(127) from None


def _read_request(descriptor: int) -> PosixLaunchRequest:
    size = struct.unpack(">I", _read_exact(descriptor, 4))[0]
    if size == 0 or size > _MAX_REQUEST_BYTES:
        raise ValueError("invalid launch request size")
    raw = json.loads(_read_exact(descriptor, size))
    if not isinstance(raw, dict) or set(raw) != {"version", "shell", "argv", "cwd", "env"}:
        raise ValueError("invalid launch request fields")
    if raw.get("version") != 1:
        raise ValueError("unsupported launch request version")
    shell = raw.get("shell")
    argv = raw.get("argv")
    cwd = raw.get("cwd")
    env = raw.get("env")
    if not isinstance(shell, str) or not shell:
        raise ValueError("invalid launch shell")
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        raise ValueError("invalid launch argv")
    if argv[0] != shell:
        raise ValueError("launch argv does not begin with shell")
    if not isinstance(cwd, str) or not cwd:
        raise ValueError("invalid launch cwd")
    if not isinstance(env, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in env.items()
    ):
        raise ValueError("invalid launch environment")
    return cast(PosixLaunchRequest, raw)


def _read_exact(descriptor: int, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = os.read(descriptor, remaining)
        if not chunk:
            raise EOFError("launch request ended early")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _write_error(descriptor: int, stage: str, error_number: int | None) -> None:
    try:
        payload = json.dumps(
            {"version": 1, "stage": stage, "errno": error_number},
            separators=(",", ":"),
        ).encode()
        os.write(descriptor, b"E" + struct.pack(">I", len(payload)) + payload)
    except BaseException:
        pass


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 3:
        return 2
    try:
        descriptors = [int(value) for value in arguments]
    except ValueError:
        return 2
    run(*descriptors)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
