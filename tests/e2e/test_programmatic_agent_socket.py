"""`vibe -p --agent-socket`, driven as a process against an agent server.

The installed `vibe` runs on the host against the mock model endpoint, while
its tools run through an agent server: a sandbox that executes them in its own
directory, and tools the server runs itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
import itertools
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any

from pydantic import JsonValue
import pytest

from tests.e2e.agent_loop_characterization.support import (
    assistant_text_chunks,
    single_tool_call_chunks,
)
from tests.e2e.common import VIBE_EXECUTABLE, export_config, read_export
from tests.e2e.mock_server import ChatCompletionsRequestPayload, StreamingMockServer
from tests.stubs.fake_agent_server import (
    AbortRun,
    FakeAgentServer,
    private_socket_path,
    serving_in_background,
)
from vibe.app_server.run_export import RunExport

pytestmark = [pytest.mark.timeout(90), pytest.mark.usefixtures("setup_e2e_env")]

_MARKER = "ran-here.txt"


def _write_a_marker_then_finish(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index > 0:
        return assistant_text_chunks("Marker written.")
    return single_tool_call_chunks(
        call_id="call_0",
        tool_name="bash",
        arguments={"command": f"pwd > {_MARKER} && cat {_MARKER}"},
    )


_LOOKUP: dict[str, JsonValue] = {
    "namespace": "kb",
    "name": "lookup",
    "description": "Look a term up in the knowledge base.",
    "inputSchema": {
        "type": "object",
        "properties": {"term": {"type": "string"}},
        "required": ["term"],
    },
    "modelAccess": "direct",
}


def _look_up_then_finish(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index > 0:
        return assistant_text_chunks("Looked it up.")
    return single_tool_call_chunks(
        call_id="call_0", tool_name="lookup", arguments={"term": "harness"}
    )


def _tool_names(payload: ChatCompletionsRequestPayload) -> list[str]:
    request: dict[str, Any] = dict(payload)
    return [tool["function"]["name"] for tool in request.get("tools", [])]


def _tool_results(payload: ChatCompletionsRequestPayload) -> list[str]:
    return [
        str(message.get("content", ""))
        for message in payload.get("messages", [])
        if message.get("role") == "tool"
    ]


def _sleep_then_finish(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index > 0:
        return assistant_text_chunks("The command timed out.")
    return single_tool_call_chunks(
        call_id="call_0", tool_name="bash", arguments={"command": "sleep 1"}
    )


_BACKGROUND_SLEEP = "sleep 1000 & echo $! > sleep.pid; wait"


def _sleep_in_the_background_then_finish(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index > 0:
        return assistant_text_chunks("The command timed out.")
    return single_tool_call_chunks(
        call_id="call_0", tool_name="bash", arguments={"command": _BACKGROUND_SLEEP}
    )


def _running(pid: int) -> bool:
    """Whether ``pid`` is a live process: a zombie waiting to be reaped is not."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


@pytest.fixture
def socket_path() -> Iterator[Path]:
    with private_socket_path() as path:
        yield path


@pytest.fixture(autouse=True)
def sandbox_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The sandbox's temporary directory, where a run copies its skills.

    The fake server runs the sandbox in this process, so the copies land in
    a directory of the test's own rather than the machine's.
    """
    path = tmp_path / "sandbox-tmp"
    path.mkdir()
    monkeypatch.setenv("TMPDIR", str(path))
    return path


def _run_vibe(host_dir: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [VIBE_EXECUTABLE, *args],
        cwd=host_dir,
        capture_output=True,
        text=True,
        timeout=60,
        env=os.environ.copy(),
        check=False,
    )


@pytest.mark.parametrize(
    "streaming_mock_server", [_write_a_marker_then_finish], indirect=True
)
def test_a_tool_call_runs_in_the_sandbox_not_the_host_directory(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent server over its own directory, and a model that
    writes a marker file with bash, then finishes.
    *Do*: Run `vibe -p --agent-socket` from a separate host directory.
    *Assert*: Exit 0 with a finished export; the marker is in the sandbox's
    directory, not the host's, and the tool reported the sandbox's directory.
    """
    # Prepare
    sandbox_dir = (tmp_path / "sandbox").resolve()
    sandbox_dir.mkdir()
    output_dir = tmp_path / "out"
    server = FakeAgentServer(socket_path, sandbox_dir)

    # Do
    with serving_in_background(server):
        result = _run_vibe(
            e2e_workdir,
            [
                "-p",
                "Write the marker",
                "--auto-approve",
                "--agent-socket",
                str(socket_path),
                "--output-dir",
                str(output_dir),
            ],
        )

    # Assert
    assert result.returncode == 0, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "finished"
    assert export.exit_code == 0
    assert (sandbox_dir / _MARKER).read_text(encoding="utf-8").strip() == str(
        sandbox_dir
    )
    assert not (e2e_workdir / _MARKER).exists()
    assert server.methods[0] == "agent/initialize"
    assert "sandbox/execute" in server.methods
    assert any(
        str(sandbox_dir) in content
        for content in _tool_results(streaming_mock_server.requests[1])
    )


@pytest.mark.parametrize("streaming_mock_server", [_sleep_then_finish], indirect=True)
def test_a_command_the_server_times_out_is_a_timeout_the_model_reads(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent server that answers the model's bash command with
    the timeout error at its own 2s limit, and a model that runs one command,
    then finishes.
    *Do*: Run `vibe -p --agent-socket`.
    *Assert*: Exit 0 with a finished export, and the model read the timed-out
    command as it would on the host, with the server's limit rather than the
    command's own 300s.
    """
    # Prepare
    sandbox_dir = (tmp_path / "sandbox").resolve()
    sandbox_dir.mkdir()
    output_dir = tmp_path / "out"
    server = FakeAgentServer(
        socket_path,
        sandbox_dir,
        times_out=lambda command: " bash --request-" in command,
        time_limit=2,
    )

    # Do
    with serving_in_background(server):
        result = _run_vibe(
            e2e_workdir,
            [
                "-p",
                "Sleep",
                "--auto-approve",
                "--agent-socket",
                str(socket_path),
                "--output-dir",
                str(output_dir),
            ],
        )

    # Assert
    assert result.returncode == 0, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "finished", export.error
    assert len(streaming_mock_server.requests) == 2
    assert any(
        "Command timed out after 2s: 'sleep 1'" in content
        for content in _tool_results(streaming_mock_server.requests[1])
    )


@pytest.mark.parametrize(
    "streaming_mock_server", [_sleep_in_the_background_then_finish], indirect=True
)
def test_a_command_the_server_stops_leaves_no_process_behind(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent server whose sandbox stops a command at its own 5s
    limit, killing its process group as swerex does, and a model that starts
    a long sleep in the background and waits for it.
    *Do*: Run `vibe -p --agent-socket`.
    *Assert*: The model read the server's limit, and the sleep is gone: the
    server killed Vibe's tool helper, and the command went with it.
    """
    # Prepare
    sandbox_dir = (tmp_path / "sandbox").resolve()
    sandbox_dir.mkdir()
    output_dir = tmp_path / "out"
    server = FakeAgentServer(socket_path, sandbox_dir, kills_after=5)

    # Do
    with serving_in_background(server):
        result = _run_vibe(
            e2e_workdir,
            [
                "-p",
                "Sleep",
                "--auto-approve",
                "--agent-socket",
                str(socket_path),
                "--output-dir",
                str(output_dir),
            ],
        )

    # Assert
    assert result.returncode == 0, result.stderr
    assert read_export(output_dir).outcome == "finished"
    assert any(
        f"Command timed out after 5s: {_BACKGROUND_SLEEP!r}" in content
        for content in _tool_results(streaming_mock_server.requests[1])
    )
    pid = int((sandbox_dir / "sleep.pid").read_text(encoding="utf-8"))
    deadline = time.monotonic() + 10
    while _running(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if _running(pid):
        os.kill(pid, signal.SIGKILL)
        pytest.fail("the stopped command's sleep is still running")


_RUNS = "runs.txt"
_MAY_HAVE_RUN = "may or may not have run"


def _run_five_commands_then_finish(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index >= 5:
        return assistant_text_chunks("Done.")
    return single_tool_call_chunks(
        call_id=f"call_{request_index}",
        tool_name="bash",
        arguments={"command": f"echo ran >> {_RUNS}"},
    )


def _crashes_helper_bash_runs(*answered: int) -> Callable[[str], bool]:
    """Crash every helper run of a bash command but the ``answered`` ones.

    Runs are numbered from 0 in the order the server receives them.
    """
    runs = itertools.count()

    def crashes(command: str) -> bool:
        return " bash --request-" in command and next(runs) not in answered

    return crashes


@pytest.mark.parametrize(
    "streaming_mock_server", [_run_five_commands_then_finish], indirect=True
)
def test_a_helper_crash_is_a_tool_error_until_three_in_a_row(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent server whose tool helper crashes on the model's
    first, second, fourth and fifth bash commands, and a model that runs five
    commands, then finishes.
    *Do*: Run `vibe -p --agent-socket`.
    *Assert*: The run finishes: each crash reached the model as a tool error
    saying the command may or may not have run, and the third command, which
    ran, reset the count, so no three crashes came in a row.
    """
    # Prepare
    sandbox_dir = (tmp_path / "sandbox").resolve()
    sandbox_dir.mkdir()
    output_dir = tmp_path / "out"
    server = FakeAgentServer(
        socket_path, sandbox_dir, crashes=_crashes_helper_bash_runs(2)
    )

    # Do
    with serving_in_background(server):
        result = _run_vibe(
            e2e_workdir,
            [
                "-p",
                "Run the commands",
                "--auto-approve",
                "--agent-socket",
                str(socket_path),
                "--output-dir",
                str(output_dir),
            ],
        )

    # Assert
    assert result.returncode == 0, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "finished", export.error
    assert len(streaming_mock_server.requests) == 6
    results = _tool_results(streaming_mock_server.requests[5])
    assert [_MAY_HAVE_RUN in content for content in results] == [
        True,
        True,
        False,
        True,
        True,
    ]
    assert (sandbox_dir / _RUNS).read_text(encoding="utf-8") == "ran\n"


@pytest.mark.parametrize(
    "streaming_mock_server", [_run_five_commands_then_finish], indirect=True
)
def test_three_helper_crashes_in_a_row_are_an_infrastructure_failure(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent server whose tool helper crashes on every bash
    command, and a model that runs commands.
    *Do*: Run `vibe -p --agent-socket`.
    *Assert*: The model read the first two crashes as tool errors; the third
    ended the run as an infrastructure failure, exit 2, before the model was
    asked again.
    """
    # Prepare
    sandbox_dir = (tmp_path / "sandbox").resolve()
    sandbox_dir.mkdir()
    output_dir = tmp_path / "out"
    server = FakeAgentServer(
        socket_path, sandbox_dir, crashes=_crashes_helper_bash_runs()
    )

    # Do
    with serving_in_background(server):
        result = _run_vibe(
            e2e_workdir,
            [
                "-p",
                "Run the commands",
                "--auto-approve",
                "--agent-socket",
                str(socket_path),
                "--output-dir",
                str(output_dir),
            ],
        )

    # Assert
    assert result.returncode == 2, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "infrastructure_failure"
    assert export.error is not None
    assert "crashed 3 times in a row" in export.error.message
    assert len(streaming_mock_server.requests) == 3
    results = _tool_results(streaming_mock_server.requests[2])
    assert len(results) == 2
    assert all(_MAY_HAVE_RUN in content for content in results)


def _move_the_workspace_then_run_a_command(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    commands = ["cd .. && mv sandbox moved", "pwd"]
    if request_index >= len(commands):
        return assistant_text_chunks("Done.")
    return single_tool_call_chunks(
        call_id=f"call_{request_index}",
        tool_name="bash",
        arguments={"command": commands[request_index]},
    )


@pytest.mark.parametrize(
    "streaming_mock_server", [_move_the_workspace_then_run_a_command], indirect=True
)
def test_a_moved_working_directory_is_a_tool_error_not_a_failed_sandbox(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent server over its own directory, and a model that
    moves that directory away with bash, then runs another command.
    *Do*: Run `vibe -p --agent-socket`.
    *Assert*: The run finishes: the second command reached the model as a
    tool error about the missing directory, as on the host, since the tool
    helper does not start in the session's working directory.
    """
    # Prepare
    sandbox_dir = (tmp_path / "sandbox").resolve()
    sandbox_dir.mkdir()
    output_dir = tmp_path / "out"
    server = FakeAgentServer(socket_path, sandbox_dir)

    # Do
    with serving_in_background(server):
        result = _run_vibe(
            e2e_workdir,
            [
                "-p",
                "Move the workspace",
                "--auto-approve",
                "--agent-socket",
                str(socket_path),
                "--output-dir",
                str(output_dir),
            ],
        )

    # Assert
    assert result.returncode == 0, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "finished", export.error
    assert (tmp_path / "moved").is_dir()
    assert len(streaming_mock_server.requests) == 3
    results = _tool_results(streaming_mock_server.requests[2])
    assert len(results) == 2
    assert f"No such file or directory: '{sandbox_dir}'" in results[1]


def _read_then_write_through_a_link(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    calls: list[tuple[str, dict[str, object]]] = [
        ("read_file", {"path": "LINK.md"}),
        ("write_file", {"path": "LINK.md", "content": "new rules\n"}),
    ]
    if request_index >= len(calls):
        return assistant_text_chunks("Done.")
    tool_name, arguments = calls[request_index]
    return single_tool_call_chunks(
        call_id=f"call_{request_index}", tool_name=tool_name, arguments=arguments
    )


@pytest.mark.parametrize(
    "streaming_mock_server", [_read_then_write_through_a_link], indirect=True
)
def test_file_tools_follow_a_symlink_when_approvals_are_not_bypassed(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent server whose workspace holds a file and a symlink to
    it, and a model that reads, then overwrites, the file through the link.
    *Do*: Run `vibe -p --agent-socket` without `--auto-approve`, so the
    permission resolver approves each call's path.
    *Assert*: The read returns the file and the write lands in it, leaving the
    link in place, as on the host.
    """
    # Prepare
    sandbox_dir = (tmp_path / "sandbox").resolve()
    sandbox_dir.mkdir()
    target = sandbox_dir / "CLAUDE.md"
    target.write_text("rules\n", encoding="utf-8")
    link = sandbox_dir / "LINK.md"
    link.symlink_to(target.name)
    output_dir = tmp_path / "out"
    server = FakeAgentServer(socket_path, sandbox_dir)

    # Do
    with serving_in_background(server):
        result = _run_vibe(
            e2e_workdir,
            [
                "-p",
                "Update the rules",
                "--agent-socket",
                str(socket_path),
                "--output-dir",
                str(output_dir),
            ],
        )

    # Assert
    assert result.returncode == 0, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "finished", export.error
    assert len(streaming_mock_server.requests) == 3
    results = _tool_results(streaming_mock_server.requests[2])
    assert len(results) == 2
    assert "rules" in results[0]
    assert "tool_failed" not in results[0]
    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == "new rules\n"


def _answer(
    _request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    return assistant_text_chunks("Done.")


_GIVEN_INSTRUCTIONS = "Run the test suite with `make check` before you finish."
_SANDBOX_DOC = "Keep every module under 300 lines."


@pytest.mark.parametrize("streaming_mock_server", [_answer], indirect=True)
def test_the_servers_instructions_reach_the_model_ahead_of_the_projects(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent server that gives project instructions in its
    handshake, over a sandbox with an AGENTS.md at its root.
    *Do*: Run `vibe -p --agent-socket` against it.
    *Assert*: The model's system prompt carries both under the project
    instructions, the server's first.
    """
    # Prepare
    sandbox_dir = (tmp_path / "sandbox").resolve()
    sandbox_dir.mkdir()
    (sandbox_dir / "AGENTS.md").write_text(_SANDBOX_DOC, encoding="utf-8")
    server = FakeAgentServer(
        socket_path, sandbox_dir, instructions=f"{_GIVEN_INSTRUCTIONS}\n"
    )

    # Do
    with serving_in_background(server):
        result = _run_vibe(
            e2e_workdir,
            [
                "-p",
                "Say done",
                "--auto-approve",
                "--agent-socket",
                str(socket_path),
                "--output-dir",
                str(tmp_path / "out"),
            ],
        )

    # Assert
    assert result.returncode == 0, result.stderr
    [system] = [
        str(message.get("content", ""))
        for message in streaming_mock_server.requests[0].get("messages", [])
        if message.get("role") == "system"
    ]
    assert (
        system.index("## Project instructions\n")
        < system.index(f"Instructions for this project:\n\n{_GIVEN_INSTRUCTIONS}")
        < system.index(f"Contents of {sandbox_dir}/AGENTS.md:\n\n{_SANDBOX_DOC}")
    )


@pytest.mark.parametrize("streaming_mock_server", [_answer], indirect=True)
def test_a_skill_left_out_of_the_sandbox_is_a_warning_in_the_export(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: A user skill with a file over the size limit, so it cannot be
    copied into the sandbox, and a model that answers at once.
    *Do*: Run `vibe -p --agent-socket`, then the same prompt on the host.
    *Assert*: The socket run finishes, and its export warns that the skill was
    left out, naming the skill's file. The host run, which keeps the skill,
    exports no warnings at all.
    """
    # Prepare
    skill_dir = Path(os.environ["VIBE_HOME"]) / "skills" / "huge"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: huge\ndescription: Too big to copy.\n---\nUse it.\n",
        encoding="utf-8",
    )
    (skill_dir / "data.bin").write_bytes(b"x" * (300 * 1024))
    sandbox_dir = (tmp_path / "sandbox").resolve()
    sandbox_dir.mkdir()
    socket_out, host_out = tmp_path / "socket-out", tmp_path / "host-out"
    server = FakeAgentServer(socket_path, sandbox_dir)

    # Do
    with serving_in_background(server):
        socket_run = _run_vibe(
            e2e_workdir,
            [
                "-p",
                "Answer",
                "--auto-approve",
                "--agent-socket",
                str(socket_path),
                "--output-dir",
                str(socket_out),
            ],
        )
    host_run = _run_vibe(
        e2e_workdir, ["-p", "Answer", "--auto-approve", "--output-dir", str(host_out)]
    )

    # Assert
    assert socket_run.returncode == 0, socket_run.stderr
    export = read_export(socket_out)
    assert export.outcome == "finished"
    assert export.warnings is not None
    [warning] = export.warnings
    assert warning.source == str(skill_file)
    assert warning.message.startswith("Skill left out of the sandbox: ")
    assert "data.bin exceeds the size limit" in warning.message
    assert host_run.returncode == 0, host_run.stderr
    host_export = json.loads((host_out / "export.json").read_text(encoding="utf-8"))
    assert "warnings" not in host_export


def _read_then_answer(
    _request_index: int, payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    messages = payload.get("messages", [])
    if messages and messages[-1].get("role") == "tool":
        return assistant_text_chunks("The notes say hello.")
    return single_tool_call_chunks(
        call_id="call_read", tool_name="bash", arguments={"command": "cat notes.txt"}
    )


def _notes_workspace(path: Path) -> Path:
    path = path.resolve()
    path.mkdir()
    (path / "notes.txt").write_text("hello from the notes\n", encoding="utf-8")
    return path


def _comparable(export: RunExport) -> dict[str, Any]:
    """The export without what names the run: its session and its times."""
    return export.model_dump(
        mode="json", exclude={"session_id", "started_at", "ended_at"}
    )


@pytest.mark.parametrize("streaming_mock_server", [_read_then_answer], indirect=True)
def test_a_socket_run_exports_what_a_host_run_exports(
    streaming_mock_server: StreamingMockServer, socket_path: Path, tmp_path: Path
) -> None:
    """*Prepare*: Two copies of a workspace, and a model that reads a file with
    bash, then answers.
    *Do*: Run the same `vibe -p` command on one copy, then with
    `--agent-socket` against an agent server over the other.
    *Assert*: Both exit 0, and their exports match apart from the session and
    the times, background processes on in both. Both count the two model calls
    as steps, both models read the file, and the socket run read it through
    the sandbox.
    """
    # Prepare
    host_dir = _notes_workspace(tmp_path / "host")
    server = FakeAgentServer(socket_path, _notes_workspace(tmp_path / "sandbox"))
    host_out, socket_out = tmp_path / "host-out", tmp_path / "socket-out"
    args = ["-p", "Read the notes", "--auto-approve", "--max-turns", "5"]

    # Do
    host_run = _run_vibe(host_dir, [*args, "--output-dir", str(host_out)])
    with serving_in_background(server):
        socket_run = _run_vibe(
            host_dir,
            [
                *args,
                "--agent-socket",
                str(socket_path),
                "--output-dir",
                str(socket_out),
            ],
        )

    # Assert
    assert host_run.returncode == 0, host_run.stderr
    assert socket_run.returncode == 0, socket_run.stderr
    host_export, socket_export = read_export(host_out), read_export(socket_out)
    assert _comparable(socket_export) == _comparable(host_export)
    assert socket_export.outcome == "finished"
    assert socket_export.steps == 2
    assert export_config(socket_export)["enable_background_processes"] is True
    requests = streaming_mock_server.requests
    assert len(requests) == 4
    assert "hello from the notes" in json.dumps(requests[1].get("messages"))
    assert "hello from the notes" in json.dumps(requests[3].get("messages"))
    assert server.sandbox is not None
    assert any("--request-base64" in command for command in server.sandbox.commands)


@pytest.mark.parametrize(
    "streaming_mock_server", [_write_a_marker_then_finish], indirect=True
)
def test_no_sandbox_server_is_an_infrastructure_failure(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: A socket path nothing listens on.
    *Do*: Run `vibe -p --agent-socket` with it.
    *Assert*: Exit 2 with an infrastructure failure export, and the model is
    never called.
    """
    # Prepare
    output_dir = tmp_path / "out"

    # Do
    result = _run_vibe(
        e2e_workdir,
        [
            "-p",
            "Write the marker",
            "--agent-socket",
            str(socket_path),
            "--output-dir",
            str(output_dir),
        ],
    )

    # Assert
    assert result.returncode == 2, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "infrastructure_failure"
    assert export.error is not None
    assert str(socket_path) in export.error.message
    assert streaming_mock_server.requests == []


@pytest.mark.parametrize("streaming_mock_server", [_look_up_then_finish], indirect=True)
def test_a_tool_server_without_a_sandbox_answers_the_models_calls(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent server with a `kb.lookup` tool and no workspace, and
    a model that calls the tool, then finishes.
    *Do*: Run `vibe -p --agent-socket`.
    *Assert*: Exit 0 with a finished export; the model is offered the tool
    alongside the built-ins, the call goes to the server with its input and
    tool call id, and the model reads the server's answer. No sandbox request
    is sent.
    """
    # Prepare
    output_dir = tmp_path / "out"
    server = FakeAgentServer(socket_path, None, tools=[_LOOKUP])

    # Do
    with serving_in_background(server):
        result = _run_vibe(
            e2e_workdir,
            [
                "-p",
                "Look up harness",
                "--agent-socket",
                str(socket_path),
                "--output-dir",
                str(output_dir),
            ],
        )

    # Assert
    assert result.returncode == 0, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "finished"
    offered = _tool_names(streaming_mock_server.requests[0])
    assert "lookup" in offered
    assert "bash" in offered
    assert server.methods == ["agent/initialize", "tools/call"]
    params = server.requests[1]["params"]
    assert isinstance(params, dict)
    assert params["name"] == "kb.lookup"
    assert params["input"] == {"term": "harness"}
    assert params["toolCallId"] == "call_0"
    [answer] = _tool_results(streaming_mock_server.requests[1])
    assert "kb.lookup" in answer
    assert "harness" in answer


@pytest.mark.parametrize("streaming_mock_server", [_look_up_then_finish], indirect=True)
def test_a_tool_the_server_fails_is_a_tool_error_the_model_reads(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent server whose `kb.lookup` tool answers with an
    error, and a model that calls it, then finishes.
    *Do*: Run `vibe -p --agent-socket`.
    *Assert*: The run still finishes, and the model reads the error.
    """

    # Prepare
    async def offline(_params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return {"error": {"message": "The knowledge base is offline"}}

    output_dir = tmp_path / "out"
    server = FakeAgentServer(socket_path, None, tools=[_LOOKUP], on_tool_call=offline)

    # Do
    with serving_in_background(server):
        result = _run_vibe(
            e2e_workdir,
            [
                "-p",
                "Look up harness",
                "--agent-socket",
                str(socket_path),
                "--output-dir",
                str(output_dir),
            ],
        )

    # Assert
    assert result.returncode == 0, result.stderr
    assert read_export(output_dir).outcome == "finished"
    [answer] = _tool_results(streaming_mock_server.requests[1])
    assert "The knowledge base is offline" in answer


@pytest.mark.parametrize("streaming_mock_server", [_look_up_then_finish], indirect=True)
def test_a_server_that_aborts_on_a_tool_call_ends_the_run_as_aborted(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent server whose `kb.lookup` aborts the run with a
    message, and a model that calls it.
    *Do*: Run `vibe -p --agent-socket`.
    *Assert*: Exit 4 with an `aborted` export carrying the server's message,
    the session and its journal, and the message on stderr.
    """

    # Prepare
    async def abort(_params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        raise AbortRun("The environment ran out of budget")

    output_dir = tmp_path / "out"
    server = FakeAgentServer(socket_path, None, tools=[_LOOKUP], on_tool_call=abort)

    # Do
    with serving_in_background(server):
        result = _run_vibe(
            e2e_workdir,
            [
                "-p",
                "Look up harness",
                "--agent-socket",
                str(socket_path),
                "--output-dir",
                str(output_dir),
            ],
        )

    # Assert
    assert result.returncode == 4, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "aborted"
    assert export.exit_code == 4
    assert export.error is not None
    assert export.error.message == "The environment ran out of budget"
    assert export.session_id is not None
    assert export.journal_dir is not None
    assert server.methods == ["agent/initialize", "tools/call"]
    assert "The environment ran out of budget" in result.stderr


@pytest.mark.parametrize(
    "streaming_mock_server", [_write_a_marker_then_finish], indirect=True
)
def test_a_server_that_aborts_the_handshake_ends_the_run_before_the_model(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent server that answers the handshake with the abort
    code.
    *Do*: Run `vibe -p --agent-socket`.
    *Assert*: Exit 4 with an `aborted` export carrying the server's message,
    and the model is never called.
    """
    # Prepare
    output_dir = tmp_path / "out"
    server = FakeAgentServer(
        socket_path, None, aborts={"agent/initialize": "No task to run"}
    )

    # Do
    with serving_in_background(server):
        result = _run_vibe(
            e2e_workdir,
            [
                "-p",
                "Write the marker",
                "--agent-socket",
                str(socket_path),
                "--output-dir",
                str(output_dir),
            ],
        )

    # Assert
    assert result.returncode == 4, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "aborted"
    assert export.error is not None
    assert export.error.message == "No task to run"
    assert streaming_mock_server.requests == []


@pytest.mark.parametrize("streaming_mock_server", [_look_up_then_finish], indirect=True)
def test_a_namespace_named_like_an_mcp_server_is_a_usage_error(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: A config with an MCP server named `kb`, and an agent server
    with a tool in the `kb` namespace.
    *Do*: Run `vibe -p --agent-socket`.
    *Assert*: Exit 1 with a `usage_error` export naming the clash, and the
    model is never called.
    """
    # Prepare
    config = tmp_path / "vibe-home" / "config.toml"
    with config.open("a", encoding="utf-8") as file:
        file.write(
            "\n[[mcp_servers]]\n"
            'name = "kb"\n'
            'transport = "stdio"\n'
            'command = "/nonexistent/kb-mcp"\n'
        )
    output_dir = tmp_path / "out"
    server = FakeAgentServer(socket_path, None, tools=[_LOOKUP])

    # Do
    with serving_in_background(server):
        result = _run_vibe(
            e2e_workdir,
            [
                "-p",
                "Look up harness",
                "--agent-socket",
                str(socket_path),
                "--output-dir",
                str(output_dir),
            ],
        )

    # Assert
    assert result.returncode == 1, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "usage_error"
    assert export.error is not None
    assert "MCP server: kb" in export.error.message
    assert streaming_mock_server.requests == []


_NUDGE = "Please call submit now."


def _look_up_finish_then_look_up_again(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    match request_index:
        case 0:
            return single_tool_call_chunks(
                call_id="call_0", tool_name="lookup", arguments={"term": "harness"}
            )
        case 1:
            return assistant_text_chunks("Looked it up.")
        case _:
            return single_tool_call_chunks(
                call_id=f"call_{request_index}",
                tool_name="lookup",
                arguments={"term": "submit"},
            )


def _first_run(
    host_dir: Path, socket_path: Path, sandbox_dir: Path, out: Path
) -> tuple[str, FakeAgentServer]:
    """Run `vibe -p` once over the socket; return its session id and server."""
    server = FakeAgentServer(socket_path, sandbox_dir, tools=[_LOOKUP])
    with serving_in_background(server):
        result = _run_vibe(
            host_dir,
            [
                "-p",
                "Look up harness",
                "--auto-approve",
                "--agent-socket",
                str(socket_path),
                "--output-dir",
                str(out),
            ],
        )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Looked it up."
    export = read_export(out)
    assert export.outcome == "finished"
    assert export.session_id is not None
    return export.session_id, server


def _commands(server: FakeAgentServer) -> list[str]:
    assert server.sandbox is not None
    return server.sandbox.commands


def _skill_uploads(server: FakeAgentServer) -> list[str]:
    """The helper commands that copy skills into the sandbox."""
    return [command for command in _commands(server) if " skills-install " in command]


def _resume_args(
    socket_path: Path, session_id: str, nudge: Path, out: Path, *limits: str
) -> list[str]:
    return [
        "--agent-socket",
        str(socket_path),
        "--resume",
        session_id,
        "--prompt-file",
        str(nudge),
        "--auto-approve",
        "--output-dir",
        str(out),
        *limits,
    ]


@pytest.mark.parametrize(
    "streaming_mock_server", [_look_up_finish_then_look_up_again], indirect=True
)
def test_a_new_run_resumes_the_session_over_a_new_connection(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: A first `vibe -p --agent-socket` run that calls `kb.lookup`
    and finishes, then a new agent server on the same socket path and
    sandbox, and a nudge prompt file.
    *Do*: Relaunch with `--resume <session id> --prompt-file <nudge>
    --max-turns 1 --max-tokens 10 --time-limit 45`; each model call uses 7
    tokens, so the session's three calls go past 10 but the new run's one
    does not.
    *Assert*: The new run redoes the handshake and is offered the server's
    tool again; it finds the skills the first run copied into the sandbox and
    copies none; the model sees the whole first conversation followed by the
    nudge; the new run stops on its own turn limit, not the token budget,
    without a reply, so it prints none rather than the first run's; its
    export names the same session, records the new limits and only its own
    step and usage, and holds the session's journal; its last sandbox call
    stops the sandbox's process server.
    """
    # Prepare
    sandbox_dir = (tmp_path / "sandbox").resolve()
    sandbox_dir.mkdir()
    first_out, second_out = tmp_path / "first", tmp_path / "second"
    session_id, first = _first_run(e2e_workdir, socket_path, sandbox_dir, first_out)
    nudge = tmp_path / "nudge.md"
    nudge.write_text(_NUDGE, encoding="utf-8")
    second = FakeAgentServer(socket_path, sandbox_dir, tools=[_LOOKUP])

    # Do
    with serving_in_background(second):
        result = _run_vibe(
            e2e_workdir,
            _resume_args(
                socket_path,
                session_id,
                nudge,
                second_out,
                "--max-turns",
                "1",
                "--max-tokens",
                "10",
                "--time-limit",
                "45",
            ),
        )

    # Assert
    assert result.returncode == 3, result.stderr
    assert result.stdout == ""
    assert second.methods[0] == "agent/initialize"
    # Closing, the run stops what an earlier run may have left running.
    assert second.methods[-2:] == ["tools/call", "sandbox/execute"]
    assert " process " in _commands(second)[-1]
    assert _skill_uploads(first) != []
    assert _skill_uploads(second) == []
    assert any(" skills-scan " in command for command in _commands(second))
    resumed = streaming_mock_server.requests[2]
    assert "lookup" in _tool_names(resumed)
    messages = [
        (message.get("role"), message.get("content"))
        for message in resumed.get("messages", [])
    ]
    assert [content for role, content in messages if role == "user"] == [
        "Look up harness",
        _NUDGE,
    ]
    assert ("assistant", "Looked it up.") in messages
    assert len(_tool_results(resumed)) == 1
    assert messages[-1] == ("user", _NUDGE)
    export = read_export(second_out)
    assert export.outcome == "turn_limit"
    assert export.session_id == session_id
    assert export.steps == 1
    assert export.usage is not None
    assert (export.usage.input_tokens, export.usage.output_tokens) == (3, 4)
    assert export.limits.max_turns == 1
    assert export.limits.max_tokens == 10
    assert export.limits.time_limit_s == 45.0
    assert export.journal_dir is not None
    assert (second_out / export.journal_dir).is_dir()


@pytest.mark.parametrize(
    "streaming_mock_server", [_look_up_finish_then_look_up_again], indirect=True
)
def test_a_resumed_run_stops_at_its_own_deadline(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: A finished first run, then an agent server whose `kb.lookup`
    never answers.
    *Do*: Resume the session with a time limit.
    *Assert*: Exit 3 with outcome `deadline` while `kb.lookup` is pending, well
    before the 600 s client tool timeout, under the same session id.
    """
    # Prepare
    # The deadline counts from process start, so it must cover startup under load.
    time_limit_s = 15

    async def never_answers(_params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        await asyncio.sleep(120)
        return {"output": "too late"}

    sandbox_dir = (tmp_path / "sandbox").resolve()
    sandbox_dir.mkdir()
    first_out, second_out = tmp_path / "first", tmp_path / "second"
    session_id, _ = _first_run(e2e_workdir, socket_path, sandbox_dir, first_out)
    nudge = tmp_path / "nudge.md"
    nudge.write_text(_NUDGE, encoding="utf-8")
    second = FakeAgentServer(
        socket_path, sandbox_dir, tools=[_LOOKUP], on_tool_call=never_answers
    )

    # Do
    started = time.monotonic()
    with serving_in_background(second):
        result = _run_vibe(
            e2e_workdir,
            _resume_args(
                socket_path,
                session_id,
                nudge,
                second_out,
                "--time-limit",
                str(time_limit_s),
            ),
        )
    elapsed = time.monotonic() - started

    # Assert
    assert result.returncode == 3, result.stderr
    export = read_export(second_out)
    assert export.outcome == "deadline"
    assert export.session_id == session_id
    assert export.limits.time_limit_s == time_limit_s
    assert "tools/call" in second.methods
    assert elapsed < time_limit_s + 30
