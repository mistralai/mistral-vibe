"""`vibe run-server`, driven as a process over its stdio, on this checkout's
app server.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
import contextlib
from contextlib import ExitStack
import os
from pathlib import Path
import signal
import time

from pydantic import JsonValue
import pytest

from tests.e2e.agent_loop_characterization.support import (
    assistant_text_chunks,
    single_tool_call_chunks,
)
from tests.e2e.common import read_export, still_running
from tests.e2e.mock_server import ChatCompletionsRequestPayload, StreamingMockServer
from tests.e2e.run_server_client import PROTOCOL_VERSION, run_server
from tests.stubs.fake_agent_server import (
    FakeAgentServer,
    ToolHandler,
    echo_tool,
    private_socket_path,
    serving_in_background,
)
from tests.stubs.subagent_programs import spawn_and_wait

pytestmark = [
    pytest.mark.timeout(120),
    pytest.mark.usefixtures("setup_e2e_env", "rust_cli"),
]

_SECRET = "sk-run-server-secret-0123456789"

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

_CHILD_TASK = "CHILD_TASK: look harness up"


def _messages(payload: ChatCompletionsRequestPayload, role: str) -> list[str]:
    return [
        str(message.get("content", ""))
        for message in payload.get("messages", [])
        if message.get("role") == role
    ]


def _look_up_then_finish(
    _request_index: int, payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    """Each conversation calls `kb.lookup` once, then finishes."""
    if _messages(payload, "tool"):
        return assistant_text_chunks("Looked it up.")
    return single_tool_call_chunks(
        call_id="call_0", tool_name="lookup", arguments={"term": "harness"}
    )


def _delegate_the_lookup(
    _request_index: int, payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    """The root spawns subagent `reader` and waits; it calls `kb.lookup`."""
    users = _messages(payload, "user")
    child = bool(users) and _CHILD_TASK in users[0]
    if _messages(payload, "tool"):
        return assistant_text_chunks("Child done." if child else "Root done.")
    if child:
        return single_tool_call_chunks(
            call_id="child_call_0", tool_name="lookup", arguments={"term": "harness"}
        )
    return single_tool_call_chunks(
        call_id="root_call_0",
        tool_name="run_typescript",
        arguments={"code": spawn_and_wait("reader", _CHILD_TASK)},
    )


def _answer(
    _request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    return assistant_text_chunks("Done.")


async def _hang(_params: dict[str, JsonValue]) -> dict[str, JsonValue]:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


def _agent_server(
    stack: ExitStack,
    *,
    on_tool_call: ToolHandler = echo_tool,
    aborts: Mapping[str, str] | None = None,
    instructions: str | None = None,
) -> FakeAgentServer:
    """An agent server with a `kb.lookup` tool and a sandbox of its own: the
    run server runs every session in its agent server's sandbox.
    """
    socket = stack.enter_context(private_socket_path())
    workspace = socket.parent / "workspace"
    workspace.mkdir()
    server = FakeAgentServer(
        socket,
        workspace,
        tools=[_LOOKUP],
        on_tool_call=on_tool_call,
        aborts=aborts,
        instructions=instructions,
    )
    return stack.enter_context(serving_in_background(server))


@pytest.fixture
def hanging_agent_server() -> Iterator[FakeAgentServer]:
    """An agent server whose `kb.lookup` never answers."""
    with ExitStack() as stack:
        yield _agent_server(stack, on_tool_call=_hang)


def _wait_for_tool_call(server: FakeAgentServer) -> None:
    deadline = time.monotonic() + 30
    while "tools/call" not in server.methods:
        assert time.monotonic() < deadline, "the run never called its tool"
        time.sleep(0.05)


@pytest.mark.parametrize("streaming_mock_server", [_look_up_then_finish], indirect=True)
def test_concurrent_runs_end_on_their_own_and_one_abort_spares_the_others(
    streaming_mock_server: StreamingMockServer, tmp_path: Path
) -> None:
    """*Prepare*: Two agent servers with a `kb.lookup` tool; one answers its
    `tools/call` with the abort code.
    *Do*: Start a run on each, together, in one run server.
    *Assert*: The aborted run ends `aborted` with exit code 4, as
    `vibe -p --agent-socket` exits; the other finishes with exit code 0. Each
    has its own export, and its server saw only its own run.
    """
    # Prepare
    with ExitStack() as stack:
        aborting = _agent_server(stack, aborts={"tools/call": "No task to run"})
        answering = _agent_server(stack)
        client = stack.enter_context(run_server(tmp_path))
        client.initialize()

        # Do
        for run_id, server in [("aborts", aborting), ("finishes", answering)]:
            reply = client.start_run(
                run_id, server.socket_path, outputDir=str(tmp_path / run_id)
            )
            assert reply["result"] == {}, reply
        aborted = client.ended("aborts")
        finished = client.ended("finishes")
        code, stderr = client.finish()

    # Assert
    assert code == 0, stderr
    assert aborted["outcome"] == "aborted", aborted
    assert aborted["exitCode"] == 4
    assert aborted["error"]["message"] == "No task to run"
    assert finished["outcome"] == "finished", finished
    assert finished["exitCode"] == 0
    assert aborted["sessionId"] != finished["sessionId"]
    assert read_export(tmp_path / "aborts").outcome == "aborted"
    assert read_export(tmp_path / "finishes").outcome == "finished"
    assert aborting.methods.count("tools/call") == 1
    assert answering.methods.count("tools/call") == 1


@pytest.mark.parametrize("streaming_mock_server", [_look_up_then_finish], indirect=True)
@pytest.mark.parametrize("ending", ["sigterm", "end_of_input"])
def test_the_server_ends_its_live_runs_before_it_exits(
    streaming_mock_server: StreamingMockServer,
    hanging_agent_server: FakeAgentServer,
    tmp_path: Path,
    ending: str,
) -> None:
    """*Prepare*: A run server serving a run whose tool call never returns.
    *Do*: Send it SIGTERM, or close its input.
    *Assert*: It reports the run as terminated, with an export, then closes
    its output and exits with code 0. Its output is the protocol alone.
    """
    # Prepare
    with run_server(tmp_path, "--max-runs", "4") as client:
        initialized = client.initialize()
        started = client.start_run(
            "hangs", hanging_agent_server.socket_path, outputDir=str(tmp_path / "out")
        )
        _wait_for_tool_call(hanging_agent_server)

        # Do
        if ending == "sigterm":
            client.process.send_signal(signal.SIGTERM)
        else:
            client.close_input()
        ended = client.ended("hangs")
        code, stderr = client.finish()

    # Assert
    assert initialized["protocolVersion"] == PROTOCOL_VERSION
    assert initialized["maxRuns"] == 4
    assert started == {"jsonrpc": "2.0", "id": 2, "result": {}}
    assert ended["outcome"] == "terminated"
    assert ended["exitCode"] == 3
    assert code == 0, stderr
    assert len(client.stdout_lines) == 3
    assert read_export(tmp_path / "out").outcome == "terminated"


@pytest.mark.parametrize("streaming_mock_server", [_look_up_then_finish], indirect=True)
def test_a_runs_credentials_reach_the_model_and_nothing_else(
    streaming_mock_server: StreamingMockServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A run server whose environment has no API key.
    *Do*: Start a run that brings its own key as `credentials`.
    *Assert*: The run finishes and the model saw the key; the server's
    output, its stderr, the logs and every exported file do not hold it.
    """
    # Prepare
    monkeypatch.delenv("MISTRAL_API_KEY")
    output_dir = tmp_path / "out"
    with ExitStack() as stack:
        server = _agent_server(stack)
        client = stack.enter_context(run_server(tmp_path))
        client.initialize()

        # Do
        client.start_run(
            "keyed",
            server.socket_path,
            outputDir=str(output_dir),
            credentials={"MISTRAL_API_KEY": _SECRET},
        )
        ended = client.ended("keyed")
        code, stderr = client.finish()

    # Assert
    assert code == 0, stderr
    assert ended["outcome"] == "finished", repr(ended)
    assert streaming_mock_server.authorizations
    assert set(streaming_mock_server.authorizations) == {f"Bearer {_SECRET}"}
    places = {"stdout": "".join(client.stdout_lines), "stderr": stderr}
    vibe_home = Path(os.environ["VIBE_HOME"])
    for root in (vibe_home / "logs", output_dir):
        for path in root.rglob("*"):
            if path.is_file():
                places[str(path)] = path.read_text(errors="replace")
    assert any(str(output_dir) in place for place in places)
    for place, text in places.items():
        assert _SECRET not in text, f"{place} holds the credential"


@pytest.mark.parametrize("streaming_mock_server", [_look_up_then_finish], indirect=True)
def test_runs_past_k_start_another_app_server_and_an_idle_one_exits(
    streaming_mock_server: StreamingMockServer,
    hanging_agent_server: FakeAgentServer,
    tmp_path: Path,
) -> None:
    """*Prepare*: A run server with one run per app server.
    *Do*: Start two runs whose tool calls never return, then stop the first.
    *Assert*: One app server serves the first; the second starts another; the
    first's exits once its run has gone.
    """
    # Prepare
    with run_server(tmp_path, "--runs-per-app-server", "1") as client:
        client.initialize()

        # Do
        client.start_run("first", hanging_agent_server.socket_path)
        _wait_for_tool_call(hanging_agent_server)
        alone = client.app_servers()
        client.start_run("second", hanging_agent_server.socket_path)
        deadline = time.monotonic() + 30
        while hanging_agent_server.methods.count("tools/call") < 2:
            assert time.monotonic() < deadline, "the second run never called"
            time.sleep(0.05)
        together = client.app_servers()
        client.request("run/stop", {"runId": "first"})
        client.ended("first")
        deadline = time.monotonic() + 30
        while client.app_servers() > 1:
            assert time.monotonic() < deadline, "the idle app server never exited"
            time.sleep(0.05)
        after = client.app_servers()
        code, stderr = client.finish()

    # Assert
    assert (alone, together, after) == (1, 2, 1)
    assert code == 0, stderr


@pytest.mark.parametrize("streaming_mock_server", [_look_up_then_finish], indirect=True)
def test_a_killed_server_leaves_no_app_server(
    streaming_mock_server: StreamingMockServer,
    hanging_agent_server: FakeAgentServer,
    tmp_path: Path,
) -> None:
    """*Prepare*: A run server serving a run whose tool call never returns.
    *Do*: SIGKILL the run server, which runs no shutdown at all.
    *Assert*: Its app server ends too: at once on Linux, by the kernel's
    parent-death signal; elsewhere once it reads the end of its stdin.
    """
    # Prepare
    with run_server(tmp_path) as client:
        client.initialize()
        client.start_run("hangs", hanging_agent_server.socket_path)
        _wait_for_tool_call(hanging_agent_server)
        app_servers = client.app_server_pids()

        # Do
        client.process.kill()
        client.process.wait()

    # Assert
    try:
        assert len(app_servers) == 1
        assert still_running(app_servers) == []
    finally:
        for pid in app_servers:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("streaming_mock_server", [_look_up_then_finish], indirect=True)
def test_a_runs_time_limit_ends_it_as_a_deadline(
    streaming_mock_server: StreamingMockServer,
    hanging_agent_server: FakeAgentServer,
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent server whose `kb.lookup` never answers.
    *Do*: Start a run with a 3 s time limit.
    *Assert*: It ends `deadline` with exit code 3, soon after its limit, and
    exports.
    """
    # Prepare
    with run_server(tmp_path) as client:
        client.initialize()
        started = time.monotonic()

        # Do
        client.start_run(
            "limited",
            hanging_agent_server.socket_path,
            outputDir=str(tmp_path / "out"),
            timeLimit=3,
        )
        ended = client.ended("limited")
        elapsed = time.monotonic() - started
        client.finish()

    # Assert
    assert ended["outcome"] == "deadline", ended
    assert ended["exitCode"] == 3
    assert 3 <= elapsed < 20
    assert read_export(tmp_path / "out").outcome == "deadline"


@pytest.mark.parametrize("streaming_mock_server", [_delegate_the_lookup], indirect=True)
def test_a_runs_subagents_are_in_its_export(
    streaming_mock_server: StreamingMockServer, tmp_path: Path
) -> None:
    """*Prepare*: A model whose root spawns subagent `reader` and waits; the
    subagent calls `kb.lookup`.
    *Do*: Run it through the run server.
    *Assert*: The export lists the subagent, idle, with its journal, and the
    agent server's `tools/call` names it as the caller.
    """
    # Prepare
    output_dir = tmp_path / "out"
    with ExitStack() as stack:
        server = _agent_server(stack)
        client = stack.enter_context(run_server(tmp_path))
        client.initialize()

        # Do
        client.start_run(
            "delegates",
            server.socket_path,
            prompt="Delegate the lookup",
            outputDir=str(output_dir),
        )
        ended = client.ended("delegates")
        client.finish()

    # Assert
    assert ended["outcome"] == "finished", repr(ended)
    export = read_export(output_dir)
    assert export.subagents is not None
    [subagent] = export.subagents
    assert subagent.agent_name == "reader"
    assert subagent.status == "idle"
    assert subagent.parent_session_id == export.session_id
    assert subagent.journal_dir == f"subagents/{subagent.session_id}"
    assert any((output_dir / "subagents" / subagent.session_id).iterdir())
    [call] = [
        request["params"]
        for request in server.requests
        if request.get("method") == "tools/call"
    ]
    assert isinstance(call, dict)
    assert isinstance(call["caller"], dict)
    assert call["caller"]["sessionId"] == subagent.session_id


_GIVEN_INSTRUCTIONS = "Run the test suite with `make check` before you finish."


@pytest.mark.parametrize("streaming_mock_server", [_answer], indirect=True)
def test_the_agent_servers_instructions_reach_the_model(
    streaming_mock_server: StreamingMockServer, tmp_path: Path
) -> None:
    """*Prepare*: An agent server that gives project instructions in its
    handshake.
    *Do*: Run a session through the run server against it.
    *Assert*: The model's system prompt carries them under the project
    instructions, as it does for `vibe -p --agent-socket`.
    """
    # Prepare
    with ExitStack() as stack:
        server = _agent_server(stack, instructions=_GIVEN_INSTRUCTIONS)
        client = stack.enter_context(run_server(tmp_path))
        client.initialize()

        # Do
        started = client.start_run("instructed", server.socket_path, prompt="Say done")
        ended = client.ended("instructed")
        client.finish()

    # Assert
    assert started["result"] == {}
    assert ended["outcome"] == "finished", repr(ended)
    [system] = _messages(streaming_mock_server.requests[0], "system")
    assert system.index("## Project instructions\n") < system.index(
        f"Instructions for this project:\n\n{_GIVEN_INSTRUCTIONS}"
    )
