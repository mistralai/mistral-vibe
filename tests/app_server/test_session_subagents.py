"""The experimental ``session/subagents/*`` methods, over the stdio server.

A subagent runs as a child session of the root. A client waits for the
subagents a turn left running, interrupts them, and reads what each did:
its status, store, usage, cost and steps. Cached input tokens reach the
session's stats and each subagent's usage, and are priced the same way.
Client tools a subagent calls name it as their caller.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import contextlib
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
import pytest_asyncio

pytest.importorskip("mistralai_vibe_local_harness.vibe")

from mistralai_vibe_local_harness.protocol import RustTokenUsage
from mistralai_vibe_local_harness.vibe import (
    RequestSentTelemetry,
    UnifiedHarnessSessionBackendHost,
)
from mistralai_vibe_local_harness.vibe._completion import report_request_sent
from mistralai_vibe_local_harness.vibe._subagents._controller import SubagentController
from tests.stubs.local_sandbox import (
    EXECUTE_COMPLETION,
    Script,
    ScriptedModel,
    build_trusted_project,
    run_turn,
)
from tests.stubs.subagent_programs import spawn_and_wait, spawn_only, spawn_two
from vibe.app_server._client_provided_tools import (
    ClientToolCall,
    ClientToolDefinition,
    ClientToolResult,
    ClientTools,
)
from vibe.app_server._subagent_runs import (
    SubagentPricing,
    SubagentSteps,
    read_subagent_runs,
)
from vibe.app_server.local import LocalHarnessHost, LocalHarnessOptions
from vibe.app_server.models import AgentStatsSnapshot
from vibe.app_server.protocol import (
    EXPERIMENTAL_SERVER_METHODS,
    SERVER_METHODS,
    SessionOptions,
    SubagentRunStatus,
)
from vibe.app_server.stdio import serve_stdio
from vibe.app_server.transport import (
    JsonRpcTransport,
    StdioJsonRpcTransport,
    memory_transport_pair,
)
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config.models import ModelConfig
from vibe.utils.pricing import session_token_cost

pytestmark = [
    pytest.mark.timeout(60),
    pytest.mark.skipif(
        sys.platform == "win32", reason="shares the POSIX sandbox test fixtures"
    ),
]

_SUBAGENT_METHODS = {
    "session/subagents/interrupt",
    "session/subagents/read",
    "session/subagents/wait",
}


class _RpcError(Exception):
    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(error.get("message"))
        self.error = error


class _Client:
    """A JSON-RPC client of the stdio server: requests run side by side, and
    notifications are kept for a test to wait on.
    """

    def __init__(self, transport: JsonRpcTransport) -> None:
        self._transport = transport
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._notifications: list[dict[str, Any]] = []
        self._notified = asyncio.Condition()
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        async for message in self._transport.messages():
            if "method" not in message:
                future = self._pending.pop(message["id"], None)
                if future is not None and not future.done():
                    future.set_result(message)
            elif "id" not in message:
                async with self._notified:
                    self._notifications.append(message)
                    self._notified.notify_all()

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._transport.send({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        })
        message = await future
        if "error" in message:
            raise _RpcError(message["error"])
        return message["result"]

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._transport.send({
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        })

    async def turn_completed(self, turn_id: str) -> dict[str, Any]:
        def completed() -> dict[str, Any] | None:
            return next(
                (
                    message["params"]
                    for message in self._notifications
                    if message["method"] == "turn/completed"
                    and message["params"].get("turn", {}).get("id") == turn_id
                ),
                None,
            )

        async with self._notified:
            await self._notified.wait_for(lambda: completed() is not None)
        found = completed()
        assert found is not None
        return found

    async def close(self) -> None:
        await self._transport.close()
        self._reader.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._reader


class _Session:
    def __init__(self, client: _Client, session_id: str) -> None:
        self.client = client
        self.id = session_id

    async def run_turn(self, task: str) -> dict[str, Any]:
        started = await self.client.request(
            "turn/start",
            {"sessionId": self.id, "message": [{"type": "text", "text": task}]},
        )
        return await self.client.turn_completed(started["turn"]["id"])

    async def call(self, method: str, **params: Any) -> dict[str, Any]:
        return await self.client.request(method, {"sessionId": self.id, **params})


@pytest_asyncio.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Client]:
    peer, transport = memory_transport_pair()
    monkeypatch.setattr(
        StdioJsonRpcTransport, "from_standard_streams", lambda: transport
    )
    serving = asyncio.create_task(serve_stdio())
    rpc = _Client(peer)
    try:
        await rpc.request(
            "initialize", {"clientInfo": {"name": "subagents-test", "version": "1"}}
        )
        await rpc.notify("initialized", {})
        yield rpc
    finally:
        await rpc.close()
        await serving


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return build_trusted_project(tmp_path / "project", monkeypatch)


async def _start(client: _Client, project: Path, *, headless: bool = True) -> _Session:
    started = await client.request(
        "session/start",
        {
            "agentConfig": {
                "cwd": str(project),
                "agent": BuiltinAgentName.AUTO_APPROVE.value,
                "trustWorkspace": True,
                "headless": headless,
            }
        },
    )
    return _Session(client, started["state"]["session"]["id"])


_SPAWNING_TASK = "SPAWNING_TASK"
_CHILD_TASK = "CHILD_TASK"


class _HeldChildModel:
    """The scripted model, holding each subagent's answer until released, and
    reporting ``usage`` for every call.

    Like the provider call it replaces, it reports each request it is sent,
    which is what counts a subagent's steps.
    """

    def __init__(
        self, scripts: dict[str, Script], *, usage: RustTokenUsage | None = None
    ) -> None:
        self._model = ScriptedModel(scripts)
        self._usage = usage
        self.child_started = asyncio.Event()
        self.release = asyncio.Event()
        self.root_calls = 0

    async def __call__(
        self,
        action: Any,
        messages: list[Any],
        tools: object,
        config: object,
        *,
        retry_sink: object | None = None,
        delta_sink: object | None = None,
    ) -> object:
        report_request_sent(
            messages,
            cast(Any, config),
            cast(Any, config).active_model,
            purpose=action.purpose,
            iteration=action.iteration,
            turn_id=action.turn_id,
        )
        if action.turn_id is not None and action.turn_id.startswith("child-"):
            self.child_started.set()
            await self.release.wait()
        else:
            self.root_calls += 1
        answer = await self._model(action, messages, tools, config)
        if self._usage is None:
            return answer
        return answer.model_copy(
            update={"result": answer.result.model_copy(update={"usage": self._usage})}
        )


def _held(program: str) -> _HeldChildModel:
    return _HeldChildModel({_SPAWNING_TASK: [("run_typescript", {"code": program})]})


@pytest.mark.asyncio
async def test_wait_returns_once_the_subagent_a_turn_left_running_settles(
    client: _Client, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A parent that spawns a subagent and answers without waiting
    for it; the subagent answers only once released.
    *Do*: Run the turn, wait briefly, release the subagent and wait again,
    then read the subagents.
    *Assert*: The brief wait times out naming the running subagent; the
    second settles; the parent took no further turn; the read lists the
    subagent idle, with its spawning call, store, usage, cost and steps.
    """
    # Prepare
    model = _held(spawn_only("reader", _CHILD_TASK))
    monkeypatch.setattr(EXECUTE_COMPLETION, model)
    session = await _start(client, project)

    # Do
    await session.run_turn(_SPAWNING_TASK)
    await asyncio.wait_for(model.child_started.wait(), timeout=10)
    timed_out = await session.call("session/subagents/wait", timeoutMs=50)
    model.release.set()
    settled = await session.call("session/subagents/wait", timeoutMs=10_000)
    read = await session.call("session/subagents/read")

    # Assert
    [running] = timed_out["running"]
    assert timed_out["settled"] is False
    assert running["name"] == "reader"
    assert settled == {"settled": True, "running": []}
    assert model.root_calls == 2
    [subagent] = read["subagents"]
    assert subagent["sessionId"] == running["sessionId"]
    assert subagent["parentSessionId"] == session.id
    assert subagent["rootSessionId"] == session.id
    assert subagent["name"] == "reader"
    assert subagent["status"] == "idle"
    # The spawn ran in the parent's program, whose tool call ID is the
    # scripted model's ``<task>-<step>``.
    assert subagent["spawnCallId"].startswith("typescript:")
    assert f":{_SPAWNING_TASK}-0:" in subagent["spawnCallId"]
    assert Path(subagent["sessionDir"]).name == subagent["sessionId"]
    assert Path(subagent["sessionDir"]).is_dir()
    assert subagent["usage"]["totalTokens"] > 0
    assert subagent["costUsd"] is not None
    assert subagent["steps"] == 1


@pytest.mark.asyncio
async def test_interrupt_interrupts_a_running_subagent_with_the_reason(
    client: _Client, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A parent that spawns a subagent and answers without waiting
    for it; the subagent never answers.
    *Do*: Run the turn, interrupt the subagents with a reason, wait for them
    and read them; then interrupt again.
    *Assert*: The interrupt reports the subagent interrupted; the wait
    settles; the read shows it interrupted; the second interrupt finds
    nothing running.
    """
    # Prepare
    model = _held(spawn_only("reader", _CHILD_TASK))
    monkeypatch.setattr(EXECUTE_COMPLETION, model)
    session = await _start(client, project)

    # Do
    try:
        await session.run_turn(_SPAWNING_TASK)
        await asyncio.wait_for(model.child_started.wait(), timeout=10)
        interrupted = await session.call(
            "session/subagents/interrupt", reason="time limit reached"
        )
        settled = await session.call("session/subagents/wait", timeoutMs=10_000)
        read = await session.call("session/subagents/read")
        again = await session.call(
            "session/subagents/interrupt", reason="time limit reached"
        )
    finally:
        model.release.set()

    # Assert
    [subagent] = read["subagents"]
    assert interrupted == {
        "results": [
            {
                "sessionId": subagent["sessionId"],
                "name": "reader",
                "outcome": "interrupted",
                "error": None,
            }
        ]
    }
    assert settled["settled"] is True
    assert subagent["status"] == "interrupted"
    assert again == {"results": []}


@pytest.mark.asyncio
async def test_a_failed_interrupt_is_reported_for_its_subagent_only(
    client: _Client, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A parent that spawns two subagents and answers without
    waiting; neither answers, and interrupting the first fails.
    *Do*: Run the turn, then interrupt the subagents.
    *Assert*: The first is reported failed with its error, the second
    interrupted.
    """
    # Prepare
    model = _held(spawn_two("analyst", "reader", _CHILD_TASK))
    monkeypatch.setattr(EXECUTE_COMPLETION, model)
    interrupt_child = SubagentController._interrupt_child

    async def failing_for_the_analyst(
        self: SubagentController, agent_name: str, *args: Any, **kwargs: Any
    ) -> Any:
        if agent_name == "analyst":
            raise RuntimeError("the analyst's interrupt failed")
        return await interrupt_child(self, agent_name, *args, **kwargs)

    monkeypatch.setattr(SubagentController, "_interrupt_child", failing_for_the_analyst)
    session = await _start(client, project)

    # Do
    try:
        await session.run_turn(_SPAWNING_TASK)
        await asyncio.wait_for(model.child_started.wait(), timeout=10)
        interrupted = await session.call(
            "session/subagents/interrupt", reason="time limit reached"
        )
    finally:
        model.release.set()

    # Assert
    analyst, reader = interrupted["results"]
    assert analyst["name"] == "analyst"
    assert analyst["outcome"] == "failed"
    assert analyst["error"] == "RuntimeError: the analyst's interrupt failed"
    assert reader["name"] == "reader"
    assert reader["outcome"] == "interrupted"
    assert reader["error"] is None


@pytest.mark.asyncio
async def test_cached_tokens_reach_the_stats_and_the_subagent_and_are_priced(
    client: _Client, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A parent that spawns a subagent and waits for it, with every
    model call reporting cached input tokens.
    *Do*: Run the turn, then read the runtime, the session and the subagents.
    *Assert*: The session's stats and token usage carry the cached tokens,
    and the subagent's usage carries its own, priced at its model's rates:
    the root's, as it runs the root's model.
    """
    # Prepare
    usage = RustTokenUsage(
        input_tokens=1_000,
        output_tokens=10,
        total_tokens=1_010,
        cached_input_tokens=400,
    )
    model = _HeldChildModel(
        {
            _SPAWNING_TASK: [
                ("run_typescript", {"code": spawn_and_wait("reader", _CHILD_TASK)})
            ]
        },
        usage=usage,
    )
    model.release.set()
    monkeypatch.setattr(EXECUTE_COMPLETION, model)
    session = await _start(client, project)

    # Do
    await session.run_turn(_SPAWNING_TASK)
    runtime = await session.call("runtime/read")
    state = await session.call("session/read")
    read = await session.call("session/subagents/read")

    # Assert
    stats = AgentStatsSnapshot.model_validate(runtime["runtime"]["stats"])
    token_usage = state["state"]["session"]["tokenUsage"]
    assert stats.session_cached_tokens > 0
    assert stats.session_cached_tokens == token_usage["cachedInputTokens"]
    assert stats.session_prompt_tokens == token_usage["inputTokens"]
    assert stats.last_turn_cached_tokens == stats.session_cached_tokens
    [subagent] = read["subagents"]
    assert subagent["usage"] == {
        "inputTokens": 1_000,
        "outputTokens": 10,
        "cachedInputTokens": 400,
        "totalTokens": 1_010,
    }
    assert subagent["model"] is not None
    assert subagent["pricingModel"] is not None
    assert subagent["pricingFallback"] is False
    assert subagent["costUsd"] == pytest.approx(
        session_token_cost(
            prompt_tokens=1_000,
            completion_tokens=10,
            cached_tokens=400,
            input_price_per_million=stats.input_price_per_million,
            output_price_per_million=stats.output_price_per_million,
            cached_input_price_per_million=stats.cached_input_price_per_million,
        )
    )


@pytest.mark.asyncio
async def test_a_subagent_whose_store_is_slow_is_read_without_its_usage(
    client: _Client, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A parent that spawns a subagent and waits for it; reading
    the subagent's store then never finishes.
    *Do*: Run the turn, then read the subagents with a short timeout.
    *Assert*: The read answers in time; the subagent is listed with what the
    root's store gave, its spawning call, but without its usage or cost.
    """
    # Prepare
    monkeypatch.setattr(
        EXECUTE_COMPLETION,
        ScriptedModel({
            _SPAWNING_TASK: [
                ("run_typescript", {"code": spawn_and_wait("reader", _CHILD_TASK)})
            ]
        }),
    )
    session = await _start(client, project)
    await session.run_turn(_SPAWNING_TASK)
    host_read = UnifiedHarnessSessionBackendHost.read

    async def a_child_read_never_finishes(
        self: UnifiedHarnessSessionBackendHost, params: Any, *args: Any, **kwargs: Any
    ) -> Any:
        if params.session_id != session.id and params.history_limit == 0:
            await asyncio.Event().wait()
        return await host_read(self, params, *args, **kwargs)

    monkeypatch.setattr(
        UnifiedHarnessSessionBackendHost, "read", a_child_read_never_finishes
    )

    # Do
    read = await asyncio.wait_for(
        session.call("session/subagents/read", timeoutMs=200), timeout=10
    )

    # Assert
    [subagent] = read["subagents"]
    assert subagent["name"] == "reader"
    assert subagent["status"] == "idle"
    assert subagent["spawnCallId"] is not None
    assert subagent["usage"] is None
    assert subagent["costUsd"] is None


@pytest.mark.asyncio
async def test_the_methods_are_scoped_to_their_session(
    client: _Client, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A session without subagents.
    *Do*: Call each method for it, then for a session that is not open.
    *Assert*: Each answers with nothing to report for the session, and fails
    as not found for the other.
    """
    # Prepare
    monkeypatch.setattr(EXECUTE_COMPLETION, ScriptedModel({}))
    session = await _start(client, project)

    # Do
    answers = [
        await session.call("session/subagents/wait", timeoutMs=0),
        await session.call("session/subagents/interrupt", reason="stop"),
        await session.call("session/subagents/read"),
    ]
    errors = []
    for method, params in (
        ("session/subagents/wait", {"timeoutMs": 0}),
        ("session/subagents/interrupt", {"reason": "stop"}),
        ("session/subagents/read", {}),
    ):
        with pytest.raises(_RpcError) as raised:
            await client.request(method, {"sessionId": "not-this-session", **params})
        errors.append(raised.value.error)

    # Assert
    assert answers == [
        {"settled": True, "running": []},
        {"results": []},
        {"subagents": []},
    ]
    assert all("not-this-session" in error["message"] for error in errors)


def test_the_subagent_methods_are_advertised_as_experimental() -> None:
    """*Prepare*: The app-server's method catalogue.
    *Do*: Look up the subagent methods.
    *Assert*: Each is advertised, and marked experimental.
    """
    # Prepare
    methods = _SUBAGENT_METHODS

    # Do
    advertised = methods <= set(SERVER_METHODS)
    experimental = methods <= EXPERIMENTAL_SERVER_METHODS

    # Assert
    assert advertised
    assert experimental
    assert set(SERVER_METHODS) >= EXPERIMENTAL_SERVER_METHODS


_LOOKUP = ClientToolDefinition(
    namespace="kb",
    name="lookup",
    input_schema={"type": "object", "properties": {"term": {"type": "string"}}},
)

_CALLING_CHILD_PROGRAM = """
async function main() {
  return await tools.kb.lookup({term: "harness"});
}
"""


class _Runner:
    def __init__(self) -> None:
        self.calls: list[ClientToolCall] = []

    async def call_tool(self, call: ClientToolCall) -> ClientToolResult:
        self.calls.append(call)
        return ClientToolResult(output={"definition": "runs the agent"})


@pytest.mark.asyncio
async def test_a_client_tool_a_subagent_calls_names_the_subagent(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A parent that spawns a subagent and waits for it; the
    subagent calls the client tool `kb.lookup`.
    *Do*: Run the turn.
    *Assert*: The call's caller is the subagent, named, under its root.
    """
    # Prepare
    monkeypatch.setattr(
        EXECUTE_COMPLETION,
        ScriptedModel({
            _SPAWNING_TASK: [
                ("run_typescript", {"code": spawn_and_wait("reader", _CHILD_TASK)})
            ],
            _CHILD_TASK: [("run_typescript", {"code": _CALLING_CHILD_PROGRAM})],
        }),
    )
    runner = _Runner()
    host = LocalHarnessHost()
    options = LocalHarnessOptions(
        session_options=SessionOptions(
            cwd=str(project),
            agent=BuiltinAgentName.AUTO_APPROVE,
            trust_workspace=True,
            headless=True,
        ),
        client_tools=ClientTools(definitions=[_LOOKUP], runner=runner),
    )

    # Do
    try:
        await run_turn(host, options, _SPAWNING_TASK)
    finally:
        await host.close()

    # Assert
    [call] = runner.calls
    caller = call.caller
    assert caller is not None
    assert caller.name == "reader"
    assert caller.parent_session_id == caller.root_session_id
    assert caller.session_id != caller.root_session_id


def test_steps_count_each_subagents_agent_calls_only() -> None:
    """*Prepare*: Request-sent telemetry for a root turn, two calls of a child's
    agent, a child's compaction and a utility call.
    *Do*: Count them through the sink wrapper.
    *Assert*: Only the child's agent calls count, and every payload still
    reaches the wrapped sink.
    """

    # Prepare
    def payload(
        purpose: Literal["agent", "compaction", "title"], turn_id: str | None
    ) -> RequestSentTelemetry:
        return _request_sent("m", purpose=purpose, turn_id=turn_id)

    seen: list[RequestSentTelemetry] = []
    steps = SubagentSteps()
    record = steps.counting(seen.append)

    # Do
    for sent in (
        payload("agent", "root-turn-1"),
        payload("agent", "child-1:turn:1"),
        payload("agent", "child-1:turn:2"),
        payload("compaction", "child-1:turn:2"),
        payload("title", None),
    ):
        record(sent)

    # Assert
    assert steps.of("child-1") == 2
    assert steps.of("root-turn-1") is None
    assert steps.models_of("child-1") == ["m"]
    assert len(seen) == 5


def _request_sent(
    model: str,
    *,
    purpose: Literal["agent", "compaction", "title"] = "agent",
    turn_id: str | None,
) -> RequestSentTelemetry:
    return RequestSentTelemetry(
        model=model,
        purpose=purpose,
        iteration=0,
        nb_context_chars=0,
        nb_context_messages=0,
        nb_prompt_chars=0,
        turn_id=turn_id,
    )


class _HostWithAChildInAnUnknownState:
    """A harness host whose root has one child, in a lifecycle state this
    Vibe does not know, and no store to read usage from.
    """

    def child_session_records(self, root_session_id: str) -> list[object]:
        del root_session_id
        return [
            SimpleNamespace(
                child_session_id="child-1",
                agent_name="reader",
                agent_type=None,
                spawn_action_id="action-1",
                state=SimpleNamespace(type="a_state_from_a_newer_harness"),
            )
        ]

    async def read(self, params: Any) -> object:
        if params.session_id != "root":
            raise LookupError(params.session_id)
        history = SimpleNamespace(entries=[])
        return SimpleNamespace(
            snapshot=SimpleNamespace(state=SimpleNamespace(history=history))
        )


_ROOT_MODEL = ModelConfig(
    name="large-model",
    provider="mistral",
    alias="root",
    input_price=2.0,
    output_price=6.0,
    cached_input_price=0.5,
)
_SMALL_MODEL = ModelConfig(
    name="small-model",
    provider="mistral",
    alias="small",
    input_price=0.1,
    output_price=0.3,
    cached_input_price=0.01,
)
_ROOT_PRICES = AgentStatsSnapshot(
    input_price_per_million=_ROOT_MODEL.input_price,
    output_price_per_million=_ROOT_MODEL.output_price,
    cached_input_price_per_million=_ROOT_MODEL.cached_input_price,
)
_PRICING = SubagentPricing(
    models={"root": _ROOT_MODEL, "small": _SMALL_MODEL},
    root_alias="root",
    root_prices=_ROOT_PRICES,
)


async def _read(
    host: object,
    tmp_path: Path,
    *,
    timeout: float = 5,
    steps: SubagentSteps | None = None,
) -> list[Any]:
    return await read_subagent_runs(
        cast(Any, host),
        "root",
        steps or SubagentSteps(),
        storage_root=str(tmp_path),
        pricing=_PRICING,
        deadline=asyncio.get_running_loop().time() + timeout,
    )


def _cost_at(model: ModelConfig) -> float:
    # 600 uncached and 400 cached input tokens, and 10 output tokens.
    cached = model.cached_input_price
    assert cached is not None
    return (600 * model.input_price + 400 * cached + 10 * model.output_price) / 1e6


@pytest.mark.asyncio
async def test_a_subagent_in_an_unknown_state_is_still_listed(tmp_path: Path) -> None:
    """*Prepare*: A root whose child is in a lifecycle state Vibe does not know.
    *Do*: Read the root's subagents.
    *Assert*: The child is listed, with the unknown status and no store.
    """
    # Prepare
    host = _HostWithAChildInAnUnknownState()

    # Do
    subagents = await _read(host, tmp_path)

    # Assert
    [subagent] = subagents
    assert subagent.session_id == "child-1"
    assert subagent.name == "reader"
    assert subagent.status is SubagentRunStatus.UNKNOWN
    assert subagent.usage is None
    assert subagent.session_dir is None


class _HostWhoseStoresDoNotAnswer(_HostWithAChildInAnUnknownState):
    """A harness host whose root has one running child, and whose store reads
    never finish.
    """

    def child_session_records(self, root_session_id: str) -> list[object]:
        [record] = super().child_session_records(root_session_id)
        cast(Any, record).state = SimpleNamespace(type="running")
        return [record]

    async def read(self, params: Any) -> object:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_a_subagent_whose_stores_do_not_answer_is_still_listed(
    tmp_path: Path,
) -> None:
    """*Prepare*: A root with a running child, whose stores never answer.
    *Do*: Read the root's subagents with a short deadline.
    *Assert*: The read returns by the deadline, listing the child as running,
    without usage, cost or spawn call ID.
    """
    # Prepare
    host = _HostWhoseStoresDoNotAnswer()

    # Do
    subagents = await asyncio.wait_for(_read(host, tmp_path, timeout=0.1), timeout=5)

    # Assert
    [subagent] = subagents
    assert subagent.session_id == "child-1"
    assert subagent.status is SubagentRunStatus.RUNNING
    assert subagent.usage is None
    assert subagent.cost_usd is None
    assert subagent.spawn_call_id is None


class _HostWithIdleChildrenThatCachedInput(_HostWithAChildInAnUnknownState):
    """A harness host whose root has idle children, each of which read 1,000
    input tokens, 400 of them from the cache.
    """

    def __init__(self, *child_session_ids: str) -> None:
        self._child_session_ids = child_session_ids or ("child-1",)

    def child_session_records(self, root_session_id: str) -> list[object]:
        [template] = super().child_session_records(root_session_id)
        idle = SimpleNamespace(
            type="idle", last_outcome=SimpleNamespace(type="completed")
        )
        return [
            SimpleNamespace(**{
                **vars(template),
                "child_session_id": child,
                "state": idle,
            })
            for child in self._child_session_ids
        ]

    async def read(self, params: Any) -> object:
        if params.session_id == "root":
            return await super().read(params)
        usage = SimpleNamespace(
            input_tokens=1_000,
            output_tokens=10,
            cached_input_tokens=400,
            total_tokens=1_010,
        )
        return SimpleNamespace(
            snapshot=SimpleNamespace(
                state=SimpleNamespace(session=SimpleNamespace(token_usage=usage))
            )
        )


@pytest.mark.asyncio
async def test_a_subagents_cached_input_is_priced_at_its_models_cached_rate(
    tmp_path: Path,
) -> None:
    """*Prepare*: A root with an idle child that read part of its input from
    the cache, on a model whose price discounts cached input.
    *Do*: Read the root's subagents.
    *Assert*: The child's usage carries the cached tokens, and its cost
    prices them at the cached rate.
    """
    # Prepare
    host = _HostWithIdleChildrenThatCachedInput()
    steps = SubagentSteps()
    steps.record(_request_sent("large-model", turn_id="child-1:turn:1"))

    # Do
    [subagent] = await _read(host, tmp_path, steps=steps)

    # Assert
    assert subagent.status is SubagentRunStatus.IDLE
    assert subagent.usage is not None
    assert subagent.usage.cached_input_tokens == 400
    assert subagent.cost_usd == pytest.approx(_cost_at(_ROOT_MODEL))


@pytest.mark.asyncio
async def test_each_subagent_is_priced_at_its_own_models_rates(tmp_path: Path) -> None:
    """*Prepare*: A root on one model with two idle children with the same
    usage: one whose agent called a cheaper configured model, one whose agent
    called the root's model.
    *Do*: Read the root's subagents.
    *Assert*: Each child names its model and is priced at that model's rates,
    under its alias, without falling back.
    """
    # Prepare
    host = _HostWithIdleChildrenThatCachedInput("on-small", "on-root")
    steps = SubagentSteps()
    for sent in (
        _request_sent("small-model", turn_id="on-small:turn:1"),
        _request_sent("small-model", turn_id="on-small:turn:2"),
        _request_sent("large-model", turn_id="on-root:turn:1"),
        _request_sent("large-model", turn_id="root-turn-1"),
    ):
        steps.record(sent)

    # Do
    on_small, on_root = await _read(host, tmp_path, steps=steps)

    # Assert
    assert on_small.model == "small-model"
    assert on_small.pricing_model == "small"
    assert on_small.pricing_fallback is False
    assert on_small.cost_usd == pytest.approx(_cost_at(_SMALL_MODEL))
    assert on_small.steps == 2
    assert on_root.model == "large-model"
    assert on_root.pricing_model == "root"
    assert on_root.pricing_fallback is False
    assert on_root.cost_usd == pytest.approx(_cost_at(_ROOT_MODEL))


@pytest.mark.asyncio
async def test_a_subagent_whose_model_is_unknown_is_priced_at_the_roots_rates(
    tmp_path: Path,
) -> None:
    """*Prepare*: A root with three idle children: one whose agent called a
    model the config does not have, one whose calls this process did not
    see, and one whose agent called two models.
    *Do*: Read the root's subagents.
    *Assert*: Each is priced at the root's rates, under the root's alias,
    and marked as a fallback; only the single unconfigured model is named.
    """
    # Prepare
    host = _HostWithIdleChildrenThatCachedInput("unconfigured", "unseen", "mixed")
    steps = SubagentSteps()
    for sent in (
        _request_sent("unconfigured-model", turn_id="unconfigured:turn:1"),
        _request_sent("small-model", turn_id="mixed:turn:1"),
        _request_sent("large-model", turn_id="mixed:turn:2"),
    ):
        steps.record(sent)

    # Do
    unconfigured, unseen, mixed = await _read(host, tmp_path, steps=steps)

    # Assert
    for subagent in (unconfigured, unseen, mixed):
        assert subagent.pricing_model == "root"
        assert subagent.pricing_fallback is True
        assert subagent.cost_usd == pytest.approx(_cost_at(_ROOT_MODEL))
    assert unconfigured.model == "unconfigured-model"
    assert unseen.model is None
    assert mixed.model is None


@pytest.mark.asyncio
async def test_a_subagent_without_usage_has_no_pricing(tmp_path: Path) -> None:
    """*Prepare*: A root whose child has no store to read usage from, and
    whose calls this process did not see.
    *Do*: Read the root's subagents.
    *Assert*: The child has no cost, so no pricing model and no fallback.
    """
    # Prepare
    host = _HostWithAChildInAnUnknownState()

    # Do
    [subagent] = await _read(host, tmp_path)

    # Assert
    assert subagent.cost_usd is None
    assert subagent.pricing_model is None
    assert subagent.pricing_fallback is False
