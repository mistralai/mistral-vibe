"""Tools a client of the harness process runs itself.

The host passes them to the process as :class:`ClientTools`, like a sandbox;
nothing on the app-server wire declares them. Each call goes to the host's
runner. The model reads its answer, an error or a timeout as the tool's
result; the history keeps one tool entry for the call.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
import json
from pathlib import Path
import sys
from typing import Any

import pytest

pytest.importorskip("mistralai_vibe_local_harness.vibe")

from mistralai_vibe_local_harness.protocol import (
    RustCompletionResult,
    RustCompletionResultPart,
    RustCompletionResultToolCallPart,
    RustCompletionSucceededEvent,
    RustLLMCallAction,
    RustTextContentBlock,
    RustTokenUsage,
)
from tests.stubs.local_sandbox import EXECUTE_COMPLETION, build_trusted_project
from vibe.app_server._client_provided_tools import (
    ClientProvidedTools,
    ClientToolCall,
    ClientToolDeclarationError,
    ClientToolDefinition,
    ClientToolError,
    ClientToolResult,
    ClientTools,
)
from vibe.app_server.events import HistoryEntryAdded
from vibe.app_server.local import LocalHarnessHost, LocalHarnessOptions
from vibe.app_server.models import (
    FailedEffectState,
    PublicCallbackEntry,
    PublicEffectEntry,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolErrorCode,
    SessionMCPStdioServer,
    SessionOptions,
)
from vibe.app_server.run_export import HeadlessUsageError
from vibe.app_server.session import AppServerSession
from vibe.core.agents.models import BuiltinAgentName

pytestmark = [
    pytest.mark.timeout(60),
    pytest.mark.skipif(
        sys.platform == "win32", reason="shares the POSIX sandbox test fixtures"
    ),
]

_LOOKUP = ClientToolDefinition(
    namespace="kb",
    name="lookup",
    description="Look a term up in the knowledge base.",
    input_schema={
        "type": "object",
        "properties": {"term": {"type": "string"}},
        "required": ["term"],
    },
)
_PROGRAM = """
async function main() {
  const found = await tools.kb.lookup({term: "harness"});
  return {found};
}
"""
_FOUND = ClientToolResult(
    output={"definition": "the thing that runs the agent"},
    annotations={"source": "test"},
)


class _Runner:
    """Answers every call with ``answer``, recording the calls."""

    def __init__(
        self, answer: Callable[[ClientToolCall], Awaitable[ClientToolResult]]
    ) -> None:
        self._answer = answer
        self.calls: list[ClientToolCall] = []

    async def call_tool(self, call: ClientToolCall) -> ClientToolResult:
        self.calls.append(call)
        return await self._answer(call)


async def _found(_call: ClientToolCall) -> ClientToolResult:
    return _FOUND


class _ToolCallingModel:
    """Makes one tool call, then answers "done", recording what it was offered."""

    def __init__(self, name: str, arguments: dict[str, Any]) -> None:
        self._name = name
        self._arguments = arguments
        self.offered: list[list[str]] = []
        self.final_messages: list[dict[str, Any]] = []

    @property
    def final_input(self) -> str:
        return json.dumps(self.final_messages)

    async def __call__(
        self,
        action: RustLLMCallAction,
        messages: list[Any],
        tools: list[Any],
        _config: object,
        *,
        retry_sink: object | None = None,
        delta_sink: object | None = None,
    ) -> RustCompletionSucceededEvent:
        self.offered.append([_tool_name(tool) for tool in tools])
        dumped = [
            message.model_dump(mode="json", by_alias=True) for message in messages
        ]
        parts: list[RustCompletionResultPart]
        if not any(message.get("role") == "tool" for message in dumped):
            parts = [
                RustCompletionResultToolCallPart(
                    id="call-1",
                    name=self._name,
                    arguments_json=json.dumps(self._arguments),
                )
            ]
            finish_reason = "tool_call"
        else:
            self.final_messages = dumped
            parts = [RustTextContentBlock(text="done")]
            finish_reason = "stop"
        return RustCompletionSucceededEvent(
            action_id=action.action_id,
            result=RustCompletionResult(
                parts=parts,
                finish_reason=finish_reason,
                usage=RustTokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            ),
        )


def _tool_name(tool: Any) -> str:
    dumped = tool.model_dump(mode="json") if hasattr(tool, "model_dump") else tool
    return str(dumped.get("name") or dumped.get("function", {}).get("name"))


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return build_trusted_project(tmp_path / "project", monkeypatch)


def _options(
    project: Path, client_tools: ClientTools, *, legacy_harness: bool = False
) -> LocalHarnessOptions:
    return LocalHarnessOptions(
        session_options=SessionOptions(
            cwd=str(project),
            agent=BuiltinAgentName.AUTO_APPROVE,
            trust_workspace=True,
            headless=True,
        ),
        experimental_harness=not legacy_harness,
        legacy_harness=legacy_harness,
        client_tools=client_tools,
    )


async def _run(
    project: Path,
    runner: _Runner,
    *,
    tools: list[ClientToolDefinition] | None = None,
    timeout: float = 600.0,
) -> tuple[AppServerSession, list[str]]:
    """Run one turn with the client tools ``tools`` answered by ``runner``.

    Returns the session and the ids of the history entries the client was
    told about.
    """
    host = LocalHarnessHost()
    client_tools = ClientTools(
        definitions=tools or [_LOOKUP], runner=runner, timeout=timeout
    )
    session = await host.start(_options(project, client_tools))
    added: list[str] = []
    try:
        await session.resources.runtime.wait_until_ready()
        async for event in session.act("look it up"):
            match event:
                case HistoryEntryAdded(entry=entry):
                    added.append(entry.id)
                case _:
                    pass
    finally:
        await session.close()
        await host.close()
    return session, added


def _failed_codes(session: AppServerSession) -> list[str | None]:
    return [
        entry.state.error.code
        for entry in session.history
        if isinstance(entry, PublicEffectEntry)
        and isinstance(entry.state, FailedEffectState)
    ]


@pytest.mark.asyncio
async def test_a_program_calls_a_client_tool_and_reads_its_output(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A client tool `kb.lookup`, programmatic by default.
    *Do*: Run a program that calls it.
    *Assert*: The tool is not offered to the model directly, the runner gets
    its name and input, and the program reads the output.
    """
    # Prepare
    model = _ToolCallingModel("run_typescript", {"code": _PROGRAM})
    monkeypatch.setattr(EXECUTE_COMPLETION, model)
    runner = _Runner(_found)

    # Do
    await _run(project, runner)

    # Assert
    assert not any("lookup" in name for name in model.offered[0])
    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call.name == "kb.lookup"
    assert call.input == {"term": "harness"}
    assert call.call_id
    assert call.tool_call_id
    assert "the thing that runs the agent" in model.final_input


@pytest.mark.asyncio
async def test_a_direct_tool_is_offered_to_the_model(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A tool in the default namespace that opts into direct calls.
    *Do*: Have the model call it.
    *Assert*: The model was offered it, the runner gets it in the `client`
    namespace with the model's call id, and the model reads its output.
    """
    # Prepare
    tool = ClientToolDefinition(
        name="lookup", input_schema=_LOOKUP.input_schema, model_access="both"
    )
    model = _ToolCallingModel("lookup", {"term": "harness"})
    monkeypatch.setattr(EXECUTE_COMPLETION, model)
    runner = _Runner(_found)

    # Do
    await _run(project, runner, tools=[tool])

    # Assert
    assert "lookup" in model.offered[0]
    assert [call.name for call in runner.calls] == ["client.lookup"]
    assert runner.calls[0].tool_call_id == "call-1"
    assert "the thing that runs the agent" in model.final_input


@pytest.mark.asyncio
async def test_a_client_error_is_a_tool_error_the_model_reads(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A runner that answers `kb.lookup` with an error.
    *Do*: Run a program that calls it.
    *Assert*: The turn goes on, and the model reads the error as the tool's result.
    """
    # Prepare
    model = _ToolCallingModel("run_typescript", {"code": _PROGRAM})
    monkeypatch.setattr(EXECUTE_COMPLETION, model)

    async def fail(_call: ClientToolCall) -> ClientToolResult:
        return ClientToolResult(
            error=ClientToolError(code="kb_offline", message="The KB is offline")
        )

    # Do
    session, _ = await _run(project, _Runner(fail))

    # Assert
    assert "The KB is offline" in model.final_input
    assert _failed_codes(session) == ["kb_offline"]


@pytest.mark.asyncio
async def test_a_runner_that_raises_is_a_tool_error_the_model_reads(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A runner that raises, as a lost connection does.
    *Do*: Have the model call a direct tool.
    *Assert*: The turn goes on, and the model reads the failure.
    """
    # Prepare
    tool = replace(_LOOKUP, model_access="direct")
    model = _ToolCallingModel("lookup", {"term": "harness"})
    monkeypatch.setattr(EXECUTE_COMPLETION, model)

    async def lose(_call: ClientToolCall) -> ClientToolResult:
        raise ConnectionError("the server went away")

    # Do
    session, _ = await _run(project, _Runner(lose), tools=[tool])

    # Assert
    assert "the server went away" in model.final_input
    assert _failed_codes(session) == ["client_tool_failed"]


@pytest.mark.asyncio
async def test_an_unanswered_call_times_out_as_a_tool_error(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A runner slower than the host timeout.
    *Do*: Run a program that calls `kb.lookup`.
    *Assert*: The model reads a timeout as the tool's result.
    """
    # Prepare
    model = _ToolCallingModel("run_typescript", {"code": _PROGRAM})
    monkeypatch.setattr(EXECUTE_COMPLETION, model)

    async def answer_late(_call: ClientToolCall) -> ClientToolResult:
        await asyncio.sleep(5)
        return _FOUND

    # Do
    session, _ = await _run(project, _Runner(answer_late), timeout=0.5)

    # Assert
    assert "kb.lookup did not answer within 0.5 seconds" in model.final_input
    assert "the thing that runs the agent" not in model.final_input
    assert _failed_codes(session) == ["client_tool_timeout"]


@pytest.mark.asyncio
async def test_a_call_is_one_tool_entry(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A direct client tool `kb.lookup`.
    *Do*: Have the model call it.
    *Assert*: The history has one tool entry for the call and no callback
    entry, and the client was told of no other entry for it.
    """
    # Prepare
    tool = replace(_LOOKUP, model_access="direct")
    model = _ToolCallingModel("lookup", {"term": "harness"})
    monkeypatch.setattr(EXECUTE_COMPLETION, model)

    # Do
    session, added = await _run(project, _Runner(_found), tools=[tool])

    # Assert
    effects = [
        entry for entry in session.history if isinstance(entry, PublicEffectEntry)
    ]
    assert len(effects) == 1
    assert not any(isinstance(entry, PublicCallbackEntry) for entry in session.history)
    assert not any("client_tool" in entry_id for entry_id in added)


@pytest.mark.asyncio
async def test_the_legacy_harness_refuses_client_tools(project: Path) -> None:
    """*Do*: Start a legacy-harness session with a client tool.
    *Assert*: The start is refused as a usage error.
    """
    host = LocalHarnessHost()
    client_tools = ClientTools(definitions=[_LOOKUP], runner=_Runner(_found))
    try:
        with pytest.raises(HeadlessUsageError, match="Unified Harness"):
            await host.start(_options(project, client_tools, legacy_harness=True))
    finally:
        await host.close()


@pytest.mark.parametrize(
    "tools",
    [
        [replace(_LOOKUP, namespace="vibe")],
        [_LOOKUP, _LOOKUP],
        [replace(_LOOKUP, name="not-an-identifier")],
    ],
    ids=["reserved-namespace", "duplicate", "bad-name"],
)
def test_a_tool_vibe_cannot_offer_is_refused(tools: list[ClientToolDefinition]) -> None:
    """*Do*: Declare a tool in Vibe's namespace, one twice, and one with a bad name.
    *Assert*: Each declaration is refused.
    """
    with pytest.raises(ClientToolDeclarationError):
        ClientProvidedTools(ClientTools(definitions=tools, runner=_Runner(_found)))


def test_a_namespace_named_like_an_mcp_server_is_refused() -> None:
    """*Do*: Check client tools in the `kb` namespace against MCP servers
    named `kb` and `docs`, and against `docs` alone.
    *Assert*: Only the clash is refused, naming the namespace.
    """
    tools = ClientProvidedTools(
        ClientTools(definitions=[_LOOKUP], runner=_Runner(_found))
    )

    with pytest.raises(ClientToolDeclarationError, match="kb"):
        tools.check_mcp_server_names(["docs", "kb"])
    tools.check_mcp_server_names(["docs"])


@pytest.mark.asyncio
async def test_a_session_whose_mcp_server_clashes_with_a_namespace_does_not_start(
    project: Path,
) -> None:
    """*Prepare*: Client tools in the `kb` namespace, and an MCP server named
    `kb` for the session.
    *Do*: Start the session.
    *Assert*: The start is refused as invalid params, naming the clash,
    before the MCP server is launched.
    """
    # Prepare
    host = LocalHarnessHost()
    options = _options(
        project, ClientTools(definitions=[_LOOKUP], runner=_Runner(_found))
    )
    options = replace(
        options,
        session_options=options.session_options.model_copy(
            update={
                "mcp_servers": [
                    SessionMCPStdioServer(name="kb", command="/nonexistent/kb-mcp")
                ]
            }
        ),
    )

    # Do
    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await host.start(options)
    finally:
        await host.close()

    # Assert
    assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert "MCP server: kb" in exc_info.value.error.message
