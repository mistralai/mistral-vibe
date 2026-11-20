from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from vibe.app_server import _keep_awake
from vibe.app_server._keep_awake import KeepAwake, Release


class _RecordingRequest:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.threads: set[str] = set()

    def acquire(self) -> Release:
        self.calls.append("acquire")
        self.threads.add(threading.current_thread().name)

        def release() -> None:
            self.calls.append("release")
            self.threads.add(threading.current_thread().name)

        return release


def _wait_for_pid(pid_file: Path) -> int:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        written = pid_file.read_text(encoding="utf-8") if pid_file.exists() else ""
        if written.endswith("\n"):
            return int(written)
        time.sleep(0.01)
    raise AssertionError("the process never wrote its pid")


def _settled(keep_awake: KeepAwake) -> None:
    assert keep_awake.settle(timeout=5)


def test_one_request_is_held_while_any_session_needs_it() -> None:
    """*Prepare*: A keep-awake service over a recording request.
    *Do*: Two sessions start a turn, then each ends it.
    *Assert*: One request is taken on the first start and released on the last
    end, both on the worker thread.
    """
    # Prepare
    request = _RecordingRequest()
    keep_awake = KeepAwake(acquire=request.acquire)
    first, second = object(), object()

    # Do
    keep_awake.update(first, active=True)
    keep_awake.update(second, active=True)
    keep_awake.update(first, active=True)
    _settled(keep_awake)
    keep_awake.update(first, active=False)
    _settled(keep_awake)
    requested_while_second_runs = keep_awake.requested
    calls_while_second_runs = list(request.calls)
    keep_awake.update(second, active=False)
    _settled(keep_awake)
    keep_awake.close()

    # Assert
    assert requested_while_second_runs
    assert calls_while_second_runs == ["acquire"]
    assert not keep_awake.requested
    assert request.calls == ["acquire", "release"]
    assert request.threads == {"vibe-keep-awake"}


def test_close_releases_the_request_for_good() -> None:
    """*Prepare*: A keep-awake service holding a request for a session.
    *Do*: Close the service, then report the session active again.
    *Assert*: The request is released once and never taken again.
    """
    # Prepare
    request = _RecordingRequest()
    keep_awake = KeepAwake(acquire=request.acquire)
    session = object()
    keep_awake.update(session, active=True)
    _settled(keep_awake)

    # Do
    keep_awake.close()
    keep_awake.update(session, active=True)

    # Assert
    assert not keep_awake.requested
    assert request.calls == ["acquire", "release"]


@pytest.mark.parametrize("failing", ["acquire", "release"])
def test_a_failing_platform_request_never_breaks_a_turn(failing: str) -> None:
    """*Prepare*: A keep-awake service whose acquire or release raises.
    *Do*: Start a turn, end it, then start another.
    *Assert*: Nothing raises, and the next turn asks again.
    """
    # Prepare
    attempts: list[str] = []

    def release() -> None:
        attempts.append("release")
        if failing == "release":
            raise OSError("release failed")

    def acquire() -> Release:
        attempts.append("acquire")
        if failing == "acquire":
            raise OSError("acquire failed")
        return release

    keep_awake = KeepAwake(acquire=acquire)
    session = object()

    # Do
    for active in (True, False, True):
        keep_awake.update(session, active=active)
        _settled(keep_awake)
    keep_awake.close()

    # Assert
    expected_first = ["acquire"] if failing == "acquire" else ["acquire", "release"]
    assert attempts[: len(expected_first) + 1] == [*expected_first, "acquire"]


@pytest.mark.parametrize(
    ("platform", "command"),
    [
        ("darwin", ["caffeinate", "-i", "-w"]),
        ("linux", ["systemd-inhibit", "--what=idle"]),
    ],
)
def test_the_platform_request_prevents_idle_sleep_only(
    monkeypatch: pytest.MonkeyPatch, platform: str, command: list[str]
) -> None:
    """*Prepare*: A platform, and a process start that records its command.
    *Do*: Ask for the platform's request.
    *Assert*: It names the idle-only request, never one that blocks a suspend.
    """
    # Prepare
    started: list[list[str]] = []
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(
        _keep_awake, "_start_process", lambda argv, **_: started.append(argv)
    )

    # Do
    _keep_awake.platform_acquire()

    # Assert
    [argv] = started
    assert argv[: len(command)] == command
    assert not any("sleep" in arg for arg in argv if arg.startswith("--what"))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pipe semantics")
def test_releasing_a_process_request_reaps_it(tmp_path: Path) -> None:
    """*Prepare*: A request held by a real ``cat`` reading this process's pipe,
    which writes its pid down first.
    *Do*: Release it.
    *Assert*: The process is gone and reaped: no zombie is left behind.
    """
    # Prepare
    pid_file = tmp_path / "pid"
    release = _keep_awake._start_process(
        ["sh", "-c", f'echo $$ > "{pid_file}"; exec cat'], stdin=subprocess.PIPE
    )
    assert release is not None
    pid = _wait_for_pid(pid_file)

    # Do
    release()

    # Assert
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
