from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path

import pytest

from vibe.app_server._client_provided_tools import (
    ClientToolCall,
    ClientToolCaller,
    ClientToolDefinition,
    ClientToolError,
    ClientToolExecutor,
    ClientToolResult,
    ClientTools,
)
from vibe.app_server._connection_protocol import AgentPlugin, ClientToolDefinitionParams
from vibe.app_server._plugins import SessionPlugins, resolve_session_plugins
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import (
    BoundSessionClientToolRunner,
    HarnessProcess,
    _BoundSession,
    client_tool_groups_for,
)
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.skills.models import SkillScope


def _plugin(root: Path, name: str = "browser") -> Path:
    plugin = root / name
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({
            "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
            "name": name,
            "version": "1.0.0",
            "extensions": {"ai.mistral.vibe": {"schemaVersion": 1}},
        })
    )
    return plugin


def _definition() -> ClientToolDefinitionParams:
    return ClientToolDefinitionParams(
        namespace="browser",
        name="echo",
        description="Echo a value",
        input_schema={"type": "object", "properties": {"value": {"type": "string"}}},
        output_schema={"type": "object"},
    )


class Runner:
    def __init__(self) -> None:
        self.calls: list[ClientToolCall] = []
        self.result = ClientToolResult(
            output={"value": "done"}, annotations={"trace": "one"}
        )

    async def call_tool(self, call: ClientToolCall) -> ClientToolResult:
        self.calls.append(call)
        return self.result


async def bound_plugins(tmp_path: Path) -> SessionPlugins:
    root = tmp_path / "builtins"
    _plugin(root)
    return await resolve_session_plugins(
        HarnessFilesManager(sources=(), cwd=tmp_path), builtin_plugin_roots=[root]
    )


def _without_browser_plugins(
    bound: SessionPlugins, scope: SkillScope | None
) -> SessionPlugins:
    resolution = bound.materialized.resolution
    if scope is None:
        plugins: tuple = ()
    else:
        plugins = tuple(replace(plugin, scope=scope) for plugin in resolution.plugins)
    return replace(
        bound,
        materialized=replace(
            bound.materialized, resolution=replace(resolution, plugins=plugins)
        ),
    )


def _executor(runner: Runner, bound: dict[str, _BoundSession]) -> ClientToolExecutor:
    return ClientToolExecutor(
        ClientTools(
            definitions=[
                ClientToolDefinition(
                    namespace="browser",
                    name="echo",
                    description="Echo a value",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                )
            ],
            runner=BoundSessionClientToolRunner(bound, runner),
        )
    )


def _call(session_id: str = "child", name: str = "echo") -> ClientToolCall:
    return ClientToolCall(
        call_id="client_tool:action",
        name=f"browser.{name}",
        input={"value": "hi"},
        tool_call_id="tool-call",
        caller=ClientToolCaller(session_id, "root"),
    )


@pytest.mark.asyncio
async def test_client_tool_groups_for_filters_by_declared_builtin_plugins(
    tmp_path: Path,
) -> None:
    bound = await bound_plugins(tmp_path)
    groups = _executor(Runner(), {}).groups
    assert [
        group.name
        for group in client_tool_groups_for(groups, frozenset({"browser"}), bound)
    ] == ["browser"]
    # Not declared by the client at initialize.
    assert client_tool_groups_for(groups, frozenset(), bound) == []
    # Disabled in this session.
    assert (
        client_tool_groups_for(
            groups, frozenset({"browser"}), _without_browser_plugins(bound, None)
        )
        == []
    )
    # Not a BUILTIN plugin in this session.
    assert (
        client_tool_groups_for(
            groups,
            frozenset({"browser"}),
            _without_browser_plugins(bound, SkillScope.GLOBAL),
        )
        == []
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["ok", "unbound", "disabled", "scope", "session"])
async def test_runner_checks_caller_session_plugin(tmp_path: Path, reason: str) -> None:
    bound = await bound_plugins(tmp_path)
    if reason == "disabled":
        bound = _without_browser_plugins(bound, None)
    elif reason == "scope":
        bound = _without_browser_plugins(bound, SkillScope.GLOBAL)
    bound_map = {} if reason == "unbound" else {"child": _BoundSession(bound)}
    runner = Runner()
    tools = BoundSessionClientToolRunner(bound_map, runner)
    result = await tools.call_tool(_call("other" if reason == "session" else "child"))
    if reason == "ok":
        assert result.error is None
        assert result.output == {"value": "done"}
        assert runner.calls == [_call()]
    else:
        assert result.error is not None
        assert result.error.code == "client_tool_unavailable"
        assert not runner.calls


@pytest.mark.asyncio
async def test_runner_revokes_in_flight_call_when_session_releases(
    tmp_path: Path,
) -> None:
    class BlockingRunner:
        def __init__(self) -> None:
            self.cancelled = False

        async def call_tool(self, call: ClientToolCall) -> ClientToolResult:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            raise AssertionError("unreachable")

    bound = await bound_plugins(tmp_path)
    entry = _BoundSession(bound)
    runner = BlockingRunner()
    tools = BoundSessionClientToolRunner({"child": entry}, runner)
    calling = asyncio.create_task(tools.call_tool(_call()))
    await asyncio.sleep(0)
    entry.revoked.set()
    result = await calling
    assert result.error is not None
    assert result.error.code == "client_tool_unavailable"
    assert runner.cancelled


def _bare_process() -> HarnessProcess:
    process = HarnessProcess.__new__(HarnessProcess)
    process._shared = False
    process._client_tools = ClientToolExecutor(None)
    process._client_plugin_names = frozenset()
    process._bound_session_plugins = {}
    return process


def test_configure_agent_plugins_registers_one_client_tools(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path / "builtins")
    process = _bare_process()
    process.configure_agent_plugins(
        [AgentPlugin(name="browser", root=str(plugin))], [_definition()], Runner()
    )
    assert process._client_tools.group_names == ["browser"]
    assert process._client_plugin_names == frozenset({"browser"})
    with pytest.raises(ValueError, match="cannot change"):
        process.configure_agent_plugins(
            [AgentPlugin(name="browser", root=str(plugin))], [_definition()], Runner()
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plugins,match",
    [
        (
            [
                AgentPlugin(name="browser", root="/a"),
                AgentPlugin(name="browser", root="/b"),
            ],
            "distinct names",
        ),
        (
            [
                AgentPlugin(name="browser", root="/a"),
                AgentPlugin(name="docs", root="/a"),
            ],
            "distinct names",
        ),
    ],
)
async def test_configure_agent_plugins_rejects_duplicate_names_or_roots(
    tmp_path: Path, plugins: list[AgentPlugin], match: str
) -> None:
    process = _bare_process()
    with pytest.raises(ValueError, match=match):
        process.configure_agent_plugins(plugins, [_definition()], Runner())


@pytest.mark.asyncio
async def test_configure_agent_plugins_requires_declared_plugin(tmp_path: Path) -> None:
    plugin = _plugin(tmp_path / "builtins")
    process = _bare_process()
    with pytest.raises(ValueError, match="declared Agent Plugin"):
        process.configure_agent_plugins(
            [AgentPlugin(name="docs", root=str(plugin))], [_definition()], Runner()
        )


@pytest.mark.asyncio
async def test_common_client_executor_preserves_errors_and_caller_identity(
    tmp_path: Path,
) -> None:
    from mistralai_vibe_local_harness.protocol import (
        RustProvidedToolCall,
        RustProvidedToolCallAction,
        RustToolFailedEvent,
    )

    bound = await bound_plugins(tmp_path)
    runner = Runner()
    executor = _executor(runner, {"child": _BoundSession(bound)})

    async def caller(session_id: str) -> ClientToolCaller:
        return ClientToolCaller(session_id, "root")

    runner.result = ClientToolResult(
        error=ClientToolError("closed", "browser_interrupted", {"id": "one"})
    )
    execute = executor.executor_factory(caller)("child")
    result = await execute(
        RustProvidedToolCallAction(
            action_id="action",
            turn_id="turn",
            call_id="tool-call",
            call=RustProvidedToolCall(
                group_name="browser", tool_name="echo", arguments={}
            ),
        )
    )
    assert isinstance(result, RustToolFailedEvent)
    assert result.result.error.code == "browser_interrupted"
    assert runner.calls[0].caller == ClientToolCaller("child", "root")
    assert runner.calls[0].call_id == "client_tool:action"


@pytest.mark.parametrize("input", [None, {}, {"value": None}, [None]])
@pytest.mark.parametrize("by_alias", [False, True])
def test_client_tool_call_serialization_preserves_required_input(
    input, by_alias: bool
) -> None:
    from vibe.app_server._connection_protocol import (
        ClientToolCallerParams,
        ClientToolCallParams,
    )

    params = ClientToolCallParams(
        call_id="call",
        name="browser.echo",
        input=input,
        tool_call_id="tool",
        caller=ClientToolCallerParams(session_id="child", root_session_id="root"),
    )
    serialized = params.model_dump(mode="json", by_alias=by_alias, exclude_none=True)
    assert "input" in serialized
    assert serialized["input"] == input
    assert serialized["caller"] == (
        {"sessionId": "child", "rootSessionId": "root"}
        if by_alias
        else {"session_id": "child", "root_session_id": "root"}
    )
    assert (
        json.loads(params.model_dump_json(by_alias=by_alias, exclude_none=True))
        == serialized
    )


@pytest.mark.asyncio
async def test_client_tool_runner_uses_correlated_app_server_requests(
    process_services: ProcessServices, tmp_path: Path
) -> None:
    from vibe.app_server._connection_protocol import ClientCapabilities, ClientInfo
    from vibe.app_server._runtime import create_harness_server
    from vibe.app_server.client import AppServerClient
    from vibe.app_server.protocol import (
        AppServerResponseError,
        Notification,
        ProtocolErrorCode,
        ServerRequest,
    )
    from vibe.app_server.server import _ConnectionClientToolRunner
    from vibe.app_server.transport import memory_transport_pair

    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        process=HarnessProcess(services=process_services),
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    with pytest.raises(AppServerResponseError) as invalid:
        await client.initialize(
            ClientInfo(name="desktop-test", version="1"),
            ClientCapabilities(client_tool_definitions=[_definition()]),
        )
    assert invalid.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert invalid.value.error.message == "Client plugins require tools/call"
    await client.initialize(
        ClientInfo(name="desktop-test", version="1"),
        ClientCapabilities(client_tools=["tools/call"]),
    )
    await client.notify("initialized")
    await client.request(
        "session/start", {"agentConfig": {"cwd": str(tmp_path), "headless": True}}
    )
    for _ in range(100):
        if server._server._connection_attached:
            break
        await asyncio.sleep(0)
    assert server._server._connection_attached
    runner = _ConnectionClientToolRunner(server._server)
    incoming = client.incoming()

    async def next_message():
        async for message in incoming:
            if message.method in {"tools/call", "tools/cancel"}:
                return message
        raise AssertionError("Connection closed before client tool message")

    call = ClientToolCall(
        "client_tool:action",
        "browser.echo",
        {"value": "hi"},
        "tool",
        ClientToolCaller("child", "root"),
    )
    try:
        calling = asyncio.create_task(runner.call_tool(call))
        request = await next_message()
        assert isinstance(request, ServerRequest)
        assert request.method == "tools/call"
        assert request.params == {
            "callId": "client_tool:action",
            "name": "browser.echo",
            "input": {"value": "hi"},
            "toolCallId": "tool",
            "caller": {"sessionId": "child", "rootSessionId": "root"},
        }
        await client.respond(
            request.id, {"output": {"value": "done"}, "annotations": {"trace": "one"}}
        )
        assert await calling == ClientToolResult(
            output={"value": "done"}, annotations={"trace": "one"}
        )
        calling = asyncio.create_task(runner.call_tool(replace(call, input=None)))
        request = await next_message()
        assert isinstance(request, ServerRequest)
        assert "input" in request.params
        assert request.params["input"] is None
        await client.respond(request.id, {"output": None})
        assert (await calling).output is None
        calling = asyncio.create_task(runner.call_tool(call))
        request = await next_message()
        assert isinstance(request, ServerRequest)
        await client.respond(request.id, {"output": {"ok": True}, "unexpected": True})
        assert (await calling).error is not None
        calling = asyncio.create_task(runner.call_tool(call))
        request = await next_message()
        assert isinstance(request, ServerRequest)
        calling.cancel()
        with pytest.raises(asyncio.CancelledError):
            await calling
        cancelled = await next_message()
        assert isinstance(cancelled, Notification)
        assert cancelled.method == "tools/cancel"
        assert cancelled.params == {"callId": call.call_id}
    finally:
        await incoming.aclose()
        await client.close()


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_pinned_plugin_calls_client_over_existing_rpc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    from tests.stubs.local_sandbox import EXECUTE_COMPLETION, ScriptedModel
    from vibe.app_server._connection_protocol import ClientCapabilities, ClientInfo
    from vibe.app_server._runtime import create_harness_server
    from vibe.app_server.client import AppServerClient
    from vibe.app_server.protocol import ServerRequest
    from vibe.app_server.transport import memory_transport_pair

    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    plugin = _plugin(tmp_path / "builtins")
    model = ScriptedModel({
        "plugin-task": [
            ("search_tool_functions", {"mode": "best_match", "query": "echo"}),
            (
                "search_tool_functions",
                {"mode": "details", "functions": ["browser.echo"]},
            ),
            (
                "run_typescript",
                {
                    "code": 'async function main() { return await tools.browser.echo({value: "hi"}); }'
                },
            ),
        ]
    })
    monkeypatch.setattr(EXECUTE_COMPLETION, model)
    process = HarnessProcess(
        services=process_services, additional_builtin_plugin_roots=[plugin.parent]
    )
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport, transport_kind="in_process", process=process
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    incoming = client.incoming()
    calls = []
    try:
        await client.initialize(
            ClientInfo(name="desktop-test", version="1"),
            ClientCapabilities(
                client_tools=["tools/call"],
                agent_plugins=[AgentPlugin(name="browser", root=str(plugin))],
                client_tool_definitions=[_definition()],
            ),
        )
        await client.notify("initialized")
        from vibe.app_server.protocol import AppServerResponseError, ProtocolErrorCode

        with pytest.raises(AppServerResponseError) as conflict:
            await client.request(
                "session/start",
                {
                    "agentConfig": {
                        "cwd": str(tmp_path),
                        "headless": True,
                        "trustWorkspace": True,
                        "mcpServers": [
                            {
                                "name": "browser",
                                "transport": "stdio",
                                "command": "/nonexistent/browser-mcp",
                            }
                        ],
                    }
                },
            )
        assert conflict.value.error.code is ProtocolErrorCode.INVALID_PARAMS
        assert "MCP server: browser" in conflict.value.error.message
        started = await client.request(
            "session/start",
            {
                "agentConfig": {
                    "cwd": str(tmp_path),
                    "headless": True,
                    "agent": "auto-approve",
                    "trustWorkspace": True,
                }
            },
        )
        session_id = started["state"]["session"]["id"]
        from mistralai_vibe_local_harness.vibe import UnifiedHarnessSessionBackendHost

        host = process._experimental_harness_host
        assert isinstance(host, UnifiedHarnessSessionBackendHost)
        assert host._provided_tool_groups["browser"].mode == "allow"
        await client.request(
            "turn/start",
            {
                "sessionId": session_id,
                "message": [{"type": "text", "text": "plugin-task"}],
            },
        )
        async for message in incoming:
            if isinstance(message, ServerRequest):
                assert message.method == "tools/call"
                calls.append(message.params)
                assert message.params["name"] == "browser.echo"
                assert message.params["input"] == {"value": "hi"}
                assert message.params["caller"] == {
                    "sessionId": session_id,
                    "rootSessionId": session_id,
                }
                await client.respond(message.id, {"output": {"echo": "from-desktop"}})
            elif message.method == "turn/completed":
                break
        assert len(calls) == 1, model.final_input("plugin-task")
        assert "from-desktop" in model.final_input("plugin-task")
        assert calls[0]["callId"]
        assert calls[0]["toolCallId"]
    finally:
        await incoming.aclose()
        await client.close()
