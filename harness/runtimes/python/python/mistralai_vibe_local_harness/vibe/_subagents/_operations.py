"""Pure subagent request parsing, identities, limits, and model results."""

from __future__ import annotations

import hashlib
from typing import Literal, cast

from pydantic import Field, JsonValue

from mistralai_vibe_local_harness.protocol import RustRuntimeBuiltinToolCallAction
from mistralai_vibe_local_harness.vibe._storage import sha256_json
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


class ListInput(SubagentModel):
    pass


class SpawnInput(SubagentModel):
    agent_name: str = Field(alias="agentName", min_length=1)
    message: str = Field(min_length=1)
    agent_type: str | None = Field(default=None, alias="agentType", min_length=1)
    instructions: str | None = Field(default=None, min_length=1)


class WaitInput(SubagentModel):
    agent_name: str = Field(alias="agentName", min_length=1)
    timeout_ms: int = Field(alias="timeoutMs", ge=1)


class MessageInput(SubagentModel):
    agent_name: str = Field(alias="agentName", min_length=1)
    message: str = Field(min_length=1)


class AgentInput(SubagentModel):
    agent_name: str = Field(alias="agentName", min_length=1)


class InterruptInput(AgentInput):
    reason: str | None = Field(default=None, min_length=1)


def request_digest(action: RustRuntimeBuiltinToolCallAction) -> str:
    return sha256_json({"name": action.call.name, "arguments": action.call.arguments})


def child_session_id(
    parent_session_id: str, action_id: str, admission_sequence: int
) -> str:
    """The session ID of the child an ``agents.spawn`` Action admits.

    Core names a spawn Action after the model's call ID, which a provider can
    reuse in a later turn while the earlier child lives on, so the Action ID
    alone does not name one child. The admission sequence only ever grows, and
    a resumed spawn reads its session ID back from its receipt rather than
    deriving it again.
    """
    seed = f"{parent_session_id}\0{action_id}\0{admission_sequence}"
    return f"child-{hashlib.sha256(seed.encode()).hexdigest()[:24]}"


CHILD_WATCH_FAILED = "subagent_child_watch_failed"


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
        return failure(
            "subagent_stopped", "Subagent is permanently closed", retryable=False
        )
    if isinstance(state, DeletingRunningChild | DeletingIdleChild):
        return failure("subagent_closing", "Subagent is closing", retryable=True)
    if isinstance(state, CreationCleanupPendingChild):
        return failure(
            "subagent_closing", "Subagent cleanup is pending", retryable=True
        )
    if isinstance(state, CreationFailedChild):
        return state.failure
    if isinstance(state, TurnFailedChild):
        return state.outcome.failure
    return failure(
        "subagent_not_ready", "Subagent has no available generation", retryable=True
    )


def child_is_sendable(child: ChildSessionRecord) -> bool:
    if isinstance(child.state, RunningChild | IdleChild):
        return True
    # A failed Turn leaves the child's Session valid. Only a child whose Turn its
    # parent could not observe stays unavailable.
    return (
        isinstance(child.state, TurnFailedChild)
        and child.state.outcome.failure.code != CHILD_WATCH_FAILED
    )


__all__ = [
    "CHILD_WATCH_FAILED",
    "AgentInput",
    "ListInput",
    "MessageInput",
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
