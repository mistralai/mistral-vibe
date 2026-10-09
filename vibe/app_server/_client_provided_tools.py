"""Run tools a client of this process serves itself.

Internal: nothing on the app-server wire declares these tools. A host that
embeds the harness passes :class:`ClientTools` to the process, the way it
passes a sandbox; ``vibe -p --agent-socket`` is the one such host today.

The tools reach the Core as provided-tool groups, one per namespace, and run
through a provided-tool executor like Vibe's own. The executor awaits the
host's runner: its ``output`` is the tool result, and its ``error``, an
exception or no answer within the timeout is a tool error the model reads.
The tool call's own history entry records the call and its result.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
import json
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import JsonValue, ValidationError

from mistralai_vibe_local_harness.protocol import (  # pyright: ignore[reportMissingImports]
    RustProtocolError,
    RustProvidedToolCallAction,
    RustProvidedToolDefinition,
    RustTextContentBlock,
    RustToolFailedEvent,
    RustToolFailureResult,
    RustToolGroupDefinition,
    RustToolSucceededEvent,
    RustToolSuccessResult,
)
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.vibe import (  # pyright: ignore[reportMissingImports]
        ProvidedToolExecutor,
        ProvidedToolExecutorFactory,
    )

DEFAULT_NAMESPACE = "client"
"""The namespace of a client tool that names none."""

CLIENT_TOOL_TIMEOUT_SECONDS = 600.0
"""How long a call waits for the client's answer before it is a tool error."""

# The groups Vibe registers itself; a client namespace cannot take them over.
RESERVED_NAMESPACES = frozenset({"vibe", "ui"})

type ClientToolModelAccess = Literal["programmatic", "direct", "both"]

_EXPOSURE: dict[
    ClientToolModelAccess, Literal["programmatic", "direct", "direct_and_programmatic"]
] = {
    "programmatic": "programmatic",
    "direct": "direct",
    "both": "direct_and_programmatic",
}


class ClientToolDeclarationError(ValueError):
    """A client tool Vibe cannot offer."""


@dataclass(frozen=True, slots=True)
class ClientToolDefinition:
    """A tool the client runs. ``model_access`` says whether the model calls it
    directly, from code it runs, or both.
    """

    name: str
    namespace: str = DEFAULT_NAMESPACE
    description: str = ""
    input_schema: Mapping[str, JsonValue] = field(default_factory=dict)
    output_schema: Mapping[str, JsonValue] | None = None
    model_access: ClientToolModelAccess = "programmatic"


@dataclass(frozen=True, slots=True)
class ClientToolCall:
    """One call of a client tool. ``call_id`` is unique to the call;
    ``name`` is ``namespace.name``.
    """

    call_id: str
    name: str
    input: JsonValue
    tool_call_id: str


@dataclass(frozen=True, slots=True)
class ClientToolError:
    message: str
    code: str | None = None
    details: JsonValue = None


@dataclass(frozen=True, slots=True)
class ClientToolResult:
    """The client's answer: ``output`` is the tool result unless ``error`` says
    the call failed. ``annotations`` stay with the result for the session's
    readers; the model does not see them.
    """

    output: JsonValue = None
    annotations: Mapping[str, JsonValue] = field(default_factory=dict)
    error: ClientToolError | None = None


class ClientToolRunner(Protocol):
    async def call_tool(self, call: ClientToolCall) -> ClientToolResult: ...


@dataclass(frozen=True, slots=True)
class ClientTools:
    """The tools a client serves, the runner that calls them, and how long a
    call may take.
    """

    definitions: Sequence[ClientToolDefinition]
    runner: ClientToolRunner
    timeout: float = CLIENT_TOOL_TIMEOUT_SECONDS


class ClientProvidedTools:
    """One session's client tools, ready for the Core."""

    def __init__(self, tools: ClientTools | None = None) -> None:
        self._tools = tools
        self._groups = () if tools is None else _tool_groups(tools.definitions)

    @property
    def groups(self) -> list[RustToolGroupDefinition]:
        return list(self._groups)

    @property
    def group_names(self) -> list[str]:
        return [group.name for group in self._groups]

    def check_mcp_server_names(self, server_names: Iterable[str]) -> None:
        """Raise :class:`ClientToolDeclarationError` when a client namespace
        is also the name of one of the session's MCP servers.

        Their tools would not collide, but the model could not tell which
        tools are whose.
        """
        clashes = sorted(set(self.group_names).intersection(server_names))
        if clashes:
            raise ClientToolDeclarationError(
                "Client tool namespaces must not be named like an MCP server: "
                + ", ".join(clashes)
            )

    def executor_factory(self) -> ProvidedToolExecutorFactory:
        def build(_session_id: str) -> ProvidedToolExecutor:
            async def execute(
                action: RustProvidedToolCallAction,
            ) -> RustToolSucceededEvent | RustToolFailedEvent:
                return await self._call(action)

            return execute

        return build

    async def _call(
        self, action: RustProvidedToolCallAction
    ) -> RustToolSucceededEvent | RustToolFailedEvent:
        tools = self._tools
        name = f"{action.call.group_name}.{action.call.tool_name}"
        if tools is None:
            return _failed(action, "client_tool_unknown", f"No client tool {name}")
        call = ClientToolCall(
            call_id=f"client_tool:{action.action_id}",
            name=name,
            input=dict(action.call.arguments),
            tool_call_id=action.call_id,
        )
        deadline = asyncio.timeout(tools.timeout)
        try:
            async with deadline:
                result = await tools.runner.call_tool(call)
        except TimeoutError as e:
            if not deadline.expired():
                # The client gave up on the call itself.
                return _failed(action, "client_tool_timeout", f"{name} timed out: {e}")
            return _failed(
                action,
                "client_tool_timeout",
                f"{name} did not answer within {tools.timeout:g} seconds",
            )
        except Exception as e:
            # A call that fails to run is a tool error the model reads, not
            # the end of the session: the model can retry it or do without.
            logger.warning("The client tool %s failed", name, exc_info=True)
            return _failed(action, "client_tool_failed", f"{name} failed: {e}")
        if result.error is not None:
            return _failed(
                action,
                result.error.code or "client_tool_failed",
                result.error.message,
                details=result.error.details,
            )
        return _succeeded(action, result)


def check_client_tools(tools: Sequence[ClientToolDefinition]) -> None:
    """Raise :class:`ClientToolDeclarationError` when Vibe cannot offer ``tools``."""
    _tool_groups(tools)


def _tool_groups(
    tools: Sequence[ClientToolDefinition],
) -> tuple[RustToolGroupDefinition, ...]:
    by_namespace: dict[str, list[RustProvidedToolDefinition]] = {}
    for tool in tools:
        namespace = tool.namespace or DEFAULT_NAMESPACE
        if namespace in RESERVED_NAMESPACES:
            raise ClientToolDeclarationError(
                f"Tool {namespace}.{tool.name}: the {namespace!r} namespace is "
                "Vibe's own"
            )
        by_namespace.setdefault(namespace, []).append(
            RustProvidedToolDefinition(
                name=tool.name,
                description=tool.description,
                input_schema=dict(tool.input_schema) or {"type": "object"},
                output_schema=dict(tool.output_schema) if tool.output_schema else None,
                exposure=_EXPOSURE[tool.model_access],
            )
        )
    try:
        return tuple(
            RustToolGroupDefinition(
                name=namespace,
                description="Tools the client provides.",
                tools=definitions,
            )
            for namespace, definitions in by_namespace.items()
        )
    except ValidationError as e:
        raise ClientToolDeclarationError(
            "; ".join(str(error["msg"]) for error in e.errors())
        ) from e


def _succeeded(
    action: RustProvidedToolCallAction, result: ClientToolResult
) -> RustToolSucceededEvent:
    output = result.output
    text = output if isinstance(output, str) else json.dumps(output)
    return RustToolSucceededEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolSuccessResult.model_validate({
            "content": [RustTextContentBlock(text=text)],
            "structured_content": output,
            "_meta": dict(result.annotations) or None,
        }),
    )


def _failed(
    action: RustProvidedToolCallAction,
    code: str,
    message: str,
    *,
    details: JsonValue = None,
) -> RustToolFailedEvent:
    return RustToolFailedEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolFailureResult(
            error=RustProtocolError(
                code=code, message=message, retryable=False, details=details
            )
        ),
    )


__all__ = [
    "CLIENT_TOOL_TIMEOUT_SECONDS",
    "DEFAULT_NAMESPACE",
    "ClientProvidedTools",
    "ClientToolCall",
    "ClientToolDeclarationError",
    "ClientToolDefinition",
    "ClientToolError",
    "ClientToolResult",
    "ClientToolRunner",
    "ClientTools",
    "check_client_tools",
]
