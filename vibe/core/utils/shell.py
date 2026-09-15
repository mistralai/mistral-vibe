from __future__ import annotations

import asyncio
import os
from pathlib import Path

from vibe.utils.platform import WindowsShellKind, is_windows, resolve_windows_shell


def uses_posix_shell() -> bool:
    if not is_windows():
        return True
    return resolve_windows_shell().kind is WindowsShellKind.BASH


async def spawn_shell_command(
    command: str, *, cwd: Path | None = None
) -> asyncio.subprocess.Process:
    env = _shell_environment()
    cwd = cwd or Path.cwd()
    if is_windows():
        shell = resolve_windows_shell()
        if shell.kind is WindowsShellKind.BASH and shell.executable is not None:
            return await asyncio.create_subprocess_exec(
                shell.executable,
                "-c",
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                env=env,
                cwd=cwd,
            )
        # cmd.exe must receive the command as one raw command-line tail, not as
        # an argv entry: exec mode routes it through subprocess.list2cmdline,
        # which escapes inner quotes as \" — a form cmd.exe does not undo, so
        # `git commit -m "a b"` reaches git as three broken pathspecs. Shell
        # mode passes it verbatim, and pinning `executable` keeps CPython from
        # consulting COMSPEC to pick the interpreter.
        return await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            env=env,
            cwd=cwd,
            executable=shell.executable or "cmd.exe",
        )

    return await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        stdin=asyncio.subprocess.DEVNULL,
        env=env,
        cwd=cwd,
        executable=os.environ.get("SHELL"),
        start_new_session=True,
    )


def _shell_environment() -> dict[str, str]:
    env = {**os.environ, "CI": "true", "NONINTERACTIVE": "1", "NO_TTY": "1"}
    if is_windows():
        return {**env, "GIT_PAGER": "more", "PAGER": "more"}
    # LC_ALL overrides every LC_* category, so a user-set LC_ALL=C (common in
    # CI/containers) would defeat LC_CTYPE below and yield non-UTF-8 output.
    env.pop("LC_ALL", None)
    return {
        **env,
        "TERM": "dumb",
        "DEBIAN_FRONTEND": "noninteractive",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
        "LESS": "-FX",
        "LC_CTYPE": "C.UTF-8",
    }


__all__ = ["spawn_shell_command", "uses_posix_shell"]
