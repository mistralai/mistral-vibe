"""Pure subagent request parsing, identities, limits, and model results."""

import hashlib
from typing import Literal, cast

import rfc8785
from pydantic import Field, JsonValue

from mistralai_vibe_local_harness.protocol import RustRuntimeBuiltinToolCallAction
from mistralai_vibe_local_harness.vibe._subagents._models import (
    ChildSessionRecord,
    ChildTombstone,
    CreationCleanupPendingChild,
    CreationFailedChild,
    DeletingIdleChild,
    DeletingRunningChild,
    IdleChild,
    RunningChild,
    SubagentFailure,
    SubagentModel,
    SubagentRuntimeState,
    TurnFailedChild,
)

SUBAGENT_TOOL_NAMES = frozenset(
    {
        "subagent.list",
        "subagent.spawn",
        "subagent.wait",
        "subagent.send_message",
        "subagent.interrupt",
        "subagent.stop",
    }
)


class ListInput(SubagentModel):
    pass


class SpawnInput(SubagentModel):
    agent_name: str = Field(alias="agentName", min_length=1)
    message: str = Field(min_length=1)
    agent_type: str | None = Field(default=None, alias="agentType", min_length=1)


class WaitInput(SubagentModel):
    agent_name: str = Field(alias="agentName", min_length=1)
    timeout_ms: int = Field(alias="timeoutMs", ge=1)


class MessageInput(SubagentModel):
    agent_name: str = Field(alias="agentName", min_length=1)
    message: str = Field(min_length=1)


class AgentInput(SubagentModel):
    agent_name: str = Field(alias="agentName", min_length=1)


def request_digest(action: RustRuntimeBuiltinToolCallAction) -> str:
    payload = {"name": action.call.name, "arguments": action.call.arguments}
    return hashlib.sha256(rfc8785.dumps(payload)).hexdigest()


def child_session_id(parent_session_id: str, action_id: str) -> str:
    digest = hashlib.sha256(f"{parent_session_id}\0{action_id}".encode()).hexdigest()
    return f"child-{digest[:24]}"


def failure(code: str, message: str, *, retryable: bool) -> SubagentFailure:
    return SubagentFailure(code=code, message=message, retryable=retryable)


def success_output() -> dict[str, JsonValue]:
    return {"type": "success"}


def error_output(error: SubagentFailure) -> dict[str, JsonValue]:
    return {"type": "error", "error": f"{error.code}: {error.message}"}


def listed_agent(
    child: ChildSessionRecord,
    status: Literal["running", "idle", "stopped", "failed"],
    error: str | None = None,
) -> JsonValue:
    item: dict[str, JsonValue] = {
        "agentName": child.agent_name,
        "agentType": child.agent_type,
        "status": status,
        "pendingTurn": status == "running",
    }
    if error is not None:
        item["error"] = error
    return cast(JsonValue, item)


def live_child_count(state: SubagentRuntimeState) -> int:
    return sum(
        not isinstance(child.state, CreationFailedChild | ChildTombstone)
        for child in state.children.values()
    )


def running_child_count(state: SubagentRuntimeState) -> int:
    return sum(
        isinstance(child.state, RunningChild | DeletingRunningChild)
        for child in state.children.values()
    )


def child_unavailable_failure(child: ChildSessionRecord) -> SubagentFailure:
    state = child.state
    if isinstance(state, ChildTombstone):
        return failure("subagent_stopped", "Subagent is permanently closed", retryable=False)
    if isinstance(state, DeletingRunningChild | DeletingIdleChild):
        return failure("subagent_closing", "Subagent is closing", retryable=True)
    if isinstance(state, CreationCleanupPendingChild):
        return failure("subagent_closing", "Subagent cleanup is pending", retryable=True)
    if isinstance(state, CreationFailedChild):
        return state.failure
    if isinstance(state, TurnFailedChild):
        return state.outcome.failure
    return failure("subagent_not_ready", "Subagent has no available generation", retryable=True)


def child_is_sendable(child: ChildSessionRecord) -> bool:
    if isinstance(child.state, RunningChild | IdleChild):
        return True
    return isinstance(child.state, TurnFailedChild) and child.state.reusable


__all__ = [
    "AgentInput",
    "ListInput",
    "MessageInput",
    "SUBAGENT_TOOL_NAMES",
    "SpawnInput",
    "WaitInput",
    "child_is_sendable",
    "child_session_id",
    "child_unavailable_failure",
    "error_output",
    "failure",
    "listed_agent",
    "live_child_count",
    "request_digest",
    "running_child_count",
    "success_output",
]
