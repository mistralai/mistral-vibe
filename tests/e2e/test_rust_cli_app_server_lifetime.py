"""No app-server outlives the `vibe -p` that started it.

`vibe -p` closes its app-server's stdin and waits for it before it exits,
however the run ends. A CLI killed outright runs no shutdown at all: its
app-server still ends, on the end of its stdin, or on Linux at once by the
kernel's parent-death signal, a SIGTERM it handles as the end of its stdin. `vibe run-server`'s side is in
`test_run_server_process.py`.
"""

from __future__ import annotations

from collections.abc import Iterator
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

import pytest

from tests.e2e.agent_loop_characterization.support import (
    assistant_text_chunks,
    single_tool_call_chunks,
)
from tests.e2e.common import RUST_CLI_ENV, still_running
from tests.e2e.mock_server import ChatCompletionsRequestPayload

pytestmark = [
    pytest.mark.timeout(120),
    pytest.mark.usefixtures("setup_e2e_env", "rust_cli"),
    pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell and signals"),
]

_TOOL_PID_FILE = "tool.pid"
# Covers process startup and the stop grace on a loaded CI machine.
_EXIT_BUDGET_S = 20.0
# How long a killed CLI's app-server may take to notice and end.
_ORPHAN_BUDGET_S = 15.0


def _text_reply(
    _request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    return assistant_text_chunks("All done.")


def _has_tool_result(payload: ChatCompletionsRequestPayload) -> bool:
    return any(message.get("role") == "tool" for message in payload.get("messages", []))


def _long_tool_call_then_text(
    _request_index: int, payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if _has_tool_result(payload):
        return assistant_text_chunks("All done.")
    return single_tool_call_chunks(
        call_id="call_long",
        tool_name="bash",
        arguments={"command": f"echo $$ > {_TOOL_PID_FILE}; exec sleep 300"},
    )


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _wait_for_pids(path: Path, deadline_s: float = 30.0) -> list[int]:
    end = time.monotonic() + deadline_s
    while not (text := _read_text(path)).strip():
        if time.monotonic() >= end:
            raise AssertionError(f"{path.name} was never written")
        time.sleep(0.05)
    return [int(pid) for pid in text.split()]


def _kill(pids: list[int]) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@pytest.fixture
def app_server_log(tmp_path: Path) -> Iterator[Path]:
    """Where the app-servers record their pids and exit statuses; any of
    them still alive afterwards is killed.
    """
    log = tmp_path / "app-servers"
    log.mkdir()
    yield log
    _kill([int(pid) for pid in _read_text(log / "pids").split()])


def _environment(log: Path, *, wrapped: bool) -> dict[str, str]:
    """This checkout's app server, run by a shell that records its pid.

    `wrapped`: the shell outlives the app server and records how it ended,
    so a killed process group records nothing.
    """
    env = os.environ.copy()
    env.pop(RUST_CLI_ENV)
    app_server = shlex.quote(str(Path(sys.executable).with_name("vibe-app-server")))
    pids = shlex.quote(str(log / "pids"))
    if wrapped:
        exits = shlex.quote(str(log / "exits"))
        script = f'echo $$ >> {pids}; {app_server} "$@"; echo $? >> {exits}'
    else:
        script = f'echo $$ >> {pids}; exec {app_server} "$@"'
    env["VIBE_APP_SERVER_CMD"] = shlex.join(["sh", "-c", script, "sh"])
    for name in ("VIBE_APP_SERVER_BIN", "VIBE_APP_SERVER_CWD", "VIBE_CLI"):
        env.pop(name, None)
    return env


def _start_vibe_p(
    workdir: Path, env: dict[str, str], *args: str, stdout: int = subprocess.PIPE
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            os.environ[RUST_CLI_ENV],
            "--workdir",
            str(workdir),
            "-p",
            "Run the long job",
            "--auto-approve",
            *args,
        ],
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        # A terminal's foreground job: its own process group.
        start_new_session=True,
    )


def _wait(process: subprocess.Popen[str]) -> str:
    try:
        _, stderr = process.communicate(timeout=_EXIT_BUDGET_S + 10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    return stderr


@pytest.mark.parametrize(
    ("streaming_mock_server", "end"),
    [
        (_text_reply, "finished"),
        (_text_reply, "closed-stdout-text"),
        (_text_reply, "closed-stdout-json"),
        (_text_reply, "closed-stdout-streaming"),
        (_long_tool_call_then_text, "time-limit"),
        (_long_tool_call_then_text, "sigterm"),
        (_long_tool_call_then_text, "sigint"),
        (_long_tool_call_then_text, "sighup"),
    ],
    indirect=["streaming_mock_server"],
)
def test_vibe_p_exits_after_its_app_server_ends(
    e2e_workdir: Path, app_server_log: Path, end: str
) -> None:
    """*Prepare*: An app server run by a shell that records how it ended.
    *Do*: End a `vibe -p` run: on its own, with nobody reading its stdout in
    each output format, at its time limit, or by a stop signal to its process
    group mid-tool.
    *Assert*: By the time the CLI has exited, its app server has ended on its
    own, through its stdin closing, rather than being killed and left behind.
    """
    # Prepare
    env = _environment(app_server_log, wrapped=True)
    args: list[str] = []
    stdout = subprocess.PIPE
    if end == "time-limit":
        args = ["--time-limit", "8"]
    closed_stdout = end.startswith("closed-stdout-")
    if closed_stdout:
        read, stdout = os.pipe()
        os.close(read)
        args = ["--output", end.removeprefix("closed-stdout-")]

    # Do
    process = _start_vibe_p(e2e_workdir, env, *args, stdout=stdout)
    if closed_stdout:
        os.close(stdout)
    if end.startswith("sig"):
        _wait_for_pids(e2e_workdir / _TOOL_PID_FILE)
        os.killpg(process.pid, getattr(signal, end.upper()))
    stderr = _wait(process)

    # Assert
    if closed_stdout:
        assert process.returncode == 2, stderr
        assert "panicked" not in stderr
    assert process.returncode in {0, 2, 3}, stderr
    assert _read_text(app_server_log / "exits").split(), (
        "the app server was killed, not waited for"
    )


@pytest.mark.parametrize(
    "streaming_mock_server", [_long_tool_call_then_text], indirect=True
)
def test_a_killed_vibe_p_leaves_no_app_server(
    e2e_workdir: Path, app_server_log: Path
) -> None:
    """*Prepare*: A `vibe -p` run whose tool sleeps for minutes.
    *Do*: SIGKILL the CLI alone, as an OOM killer or a supervisor would.
    *Assert*: Its app server and the tool end too.
    """
    # Prepare
    process = _start_vibe_p(e2e_workdir, _environment(app_server_log, wrapped=False))
    tool = _wait_for_pids(e2e_workdir / _TOOL_PID_FILE)

    # Do
    process.kill()
    _wait(process)

    # Assert
    app_servers = _wait_for_pids(app_server_log / "pids", 0)
    assert still_running([*app_servers, *tool], _ORPHAN_BUDGET_S) == []


@pytest.mark.parametrize(
    "streaming_mock_server", [_long_tool_call_then_text], indirect=True
)
def test_a_sigtermed_app_server_stops_its_tool(
    e2e_workdir: Path, app_server_log: Path
) -> None:
    """*Prepare*: A `vibe -p` run whose tool sleeps for minutes, in a session
    of its own.
    *Do*: SIGTERM the app server alone, as Linux's parent-death signal does
    once the CLI is killed, while its stdin is still open.
    *Assert*: The app server stops the tool before it exits.
    """
    # Prepare
    process = _start_vibe_p(e2e_workdir, _environment(app_server_log, wrapped=False))
    tool = _wait_for_pids(e2e_workdir / _TOOL_PID_FILE)
    app_servers = _wait_for_pids(app_server_log / "pids", 0)

    # Do
    for pid in app_servers:
        os.kill(pid, signal.SIGTERM)

    # Assert
    assert still_running([*app_servers, *tool], _ORPHAN_BUDGET_S) == []
    _wait(process)
