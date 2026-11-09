"""`vibe -p --agent-socket` with subagents: the run waits for them, stops
them when it stops, and exports each one beside the root.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from pathlib import Path
import time

from pydantic import JsonValue
import pytest

from tests.e2e.agent_loop_characterization.support import (
    assistant_text_chunks,
    single_tool_call_chunks,
)
from tests.e2e.common import read_export, run_rust_vibe
from tests.e2e.mock_server import ChatCompletionsRequestPayload, StreamingMockServer
from tests.stubs.fake_agent_server import (
    FakeAgentServer,
    private_socket_path,
    serving_in_background,
)
from tests.stubs.subagent_programs import spawn_and_wait, spawn_only

pytestmark = [
    pytest.mark.timeout(90),
    pytest.mark.usefixtures("setup_e2e_env", "rust_cli"),
]

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


@pytest.fixture
def socket_path() -> Iterator[Path]:
    with private_socket_path() as path:
        yield path


def _tool_results(payload: ChatCompletionsRequestPayload) -> list[str]:
    return [
        str(message.get("content", ""))
        for message in payload.get("messages", [])
        if message.get("role") == "tool"
    ]


def _root_spawns_a_child_that_looks_up(
    program: str,
) -> Callable[[int, ChatCompletionsRequestPayload], list[dict[str, object]]]:
    """The root runs ``program``, which spawns subagent ``reader``; the
    subagent calls `kb.lookup`. Each finishes once it has read its tool's
    result.
    """

    def respond(
        _request_index: int, payload: ChatCompletionsRequestPayload
    ) -> list[dict[str, object]]:
        users = [
            str(message.get("content", ""))
            for message in payload.get("messages", [])
            if message.get("role") == "user"
        ]
        child = bool(users) and _CHILD_TASK in users[0]
        if _tool_results(payload):
            return assistant_text_chunks("Child done." if child else "Root done.")
        if child:
            return single_tool_call_chunks(
                call_id="child_call_0",
                tool_name="lookup",
                arguments={"term": "harness"},
            )
        return single_tool_call_chunks(
            call_id="root_call_0",
            tool_name="run_typescript",
            arguments={"code": program},
        )

    return respond


_spawn_and_wait_for_a_child = _root_spawns_a_child_that_looks_up(
    spawn_and_wait("reader", _CHILD_TASK)
)
_spawn_a_child_and_finish = _root_spawns_a_child_that_looks_up(
    spawn_only("reader", _CHILD_TASK)
)


def _args(socket_path: Path, output_dir: Path, *extra: str) -> list[str]:
    return [
        "-p",
        "Delegate the lookup",
        "--auto-approve",
        "--agent-socket",
        str(socket_path),
        "--output-dir",
        str(output_dir),
        *extra,
    ]


@pytest.mark.parametrize(
    "streaming_mock_server", [_spawn_and_wait_for_a_child], indirect=True
)
def test_a_subagents_tool_call_names_it_and_the_export_holds_its_journal(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent server with a `kb.lookup` tool, and a model whose
    root spawns a subagent from a program and waits for it; the subagent
    calls `kb.lookup`.
    *Do*: Run `vibe -p --agent-socket`.
    *Assert*: The server's `tools/call` names the subagent as its caller. The
    export lists the subagent, idle, with its own model, usage, cost (priced
    at its model's rates) and steps and the root's program call that spawned
    it, and holds its journal; the
    root's own steps leave the subagent's out.
    """
    # Prepare
    output_dir = tmp_path / "out"
    server = FakeAgentServer(socket_path, None, tools=[_LOOKUP])

    # Do
    with serving_in_background(server):
        result = run_rust_vibe(e2e_workdir, _args(socket_path, output_dir))

    # Assert
    assert result.returncode == 0, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "finished", export.error
    root_id = export.session_id
    assert export.subagents is not None
    [subagent] = export.subagents
    [call] = [
        request["params"]
        for request in server.requests
        if request.get("method") == "tools/call"
    ]
    assert isinstance(call, dict)
    assert call["caller"] == {
        "sessionId": subagent.session_id,
        "rootSessionId": root_id,
        "parentSessionId": root_id,
        "name": "reader",
        "agentType": None,
    }
    assert call["toolCallId"] == "child_call_0"
    assert subagent.parent_session_id == root_id
    assert subagent.root_session_id == root_id
    assert subagent.agent_name == "reader"
    assert subagent.status == "idle"
    assert subagent.steps == 2
    assert subagent.usage is not None
    assert subagent.usage.total_tokens > 0
    assert subagent.cost_usd is not None
    assert subagent.model is not None
    assert subagent.pricing_model is not None
    assert subagent.pricing_fallback is False
    assert subagent.spawn_call_id is not None
    assert ":root_call_0:" in subagent.spawn_call_id
    assert export.steps == 2
    assert subagent.journal_dir == f"subagents/{subagent.session_id}"
    assert any((output_dir / "subagents" / subagent.session_id).iterdir())


@pytest.mark.parametrize(
    "streaming_mock_server", [_spawn_a_child_and_finish], indirect=True
)
def test_the_run_waits_for_a_subagent_its_root_left_running(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent server whose `kb.lookup` answers after 2 s, and a
    model whose root spawns a subagent and finishes without waiting for it;
    the subagent calls `kb.lookup`.
    *Do*: Run `vibe -p --agent-socket`.
    *Assert*: The run finishes only once the subagent has: the export lists
    it idle, with both its steps, and the model saw the subagent read its
    tool's result.
    """
    # Prepare
    output_dir = tmp_path / "out"

    async def answers_late(_params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        await asyncio.sleep(2)
        return {"output": "harness: the run's scaffolding"}

    server = FakeAgentServer(
        socket_path, None, tools=[_LOOKUP], on_tool_call=answers_late
    )

    # Do
    with serving_in_background(server):
        result = run_rust_vibe(e2e_workdir, _args(socket_path, output_dir))

    # Assert
    assert result.returncode == 0, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "finished", export.error
    assert export.subagents is not None
    [subagent] = export.subagents
    assert subagent.status == "idle"
    assert subagent.steps == 2
    assert "tools/call" in server.methods
    assert any(
        "harness: the run's scaffolding" in content
        for payload in streaming_mock_server.requests
        for content in _tool_results(payload)
    )
    assert subagent.journal_dir is not None
    assert (output_dir / subagent.journal_dir).is_dir()


@pytest.mark.parametrize(
    "streaming_mock_server", [_spawn_a_child_and_finish], indirect=True
)
def test_a_deadline_interrupts_the_subagents_the_run_waits_for(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    socket_path: Path,
    tmp_path: Path,
) -> None:
    """*Prepare*: An agent server whose `kb.lookup` never answers, and a
    model whose root spawns a subagent and finishes; the subagent calls
    `kb.lookup`.
    *Do*: Run `vibe -p --agent-socket` with a time limit.
    *Assert*: Exit 3 with outcome `deadline` well before the client tool
    timeout. The export still has the root's usage, read after the subagent's
    last events, and lists the subagent as interrupted, with its journal.
    """
    # Prepare
    # The deadline counts from process start, so it must cover startup under load.
    time_limit_s = 15
    output_dir = tmp_path / "out"

    async def never_answers(_params: dict[str, JsonValue]) -> dict[str, JsonValue]:
        await asyncio.sleep(120)
        return {"output": "too late"}

    server = FakeAgentServer(
        socket_path, None, tools=[_LOOKUP], on_tool_call=never_answers
    )

    # Do
    started = time.monotonic()
    with serving_in_background(server):
        result = run_rust_vibe(
            e2e_workdir,
            _args(socket_path, output_dir, "--time-limit", str(time_limit_s)),
        )
    elapsed = time.monotonic() - started

    # Assert
    assert result.returncode == 3, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "deadline"
    assert export.usage is not None
    assert "tools/call" in server.methods
    assert export.subagents is not None
    [subagent] = export.subagents
    assert subagent.agent_name == "reader"
    assert subagent.status == "interrupted"
    assert subagent.journal_dir == f"subagents/{subagent.session_id}"
    assert elapsed < time_limit_s + 30
