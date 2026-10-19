"""Validate Core agent Actions before calling the local child-session controller."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue, field_validator

from mistralai_vibe_local_harness.protocol import (
    RustProtocolError,
    RustRuntimeBuiltinToolCallAction,
    RustTextContentBlock,
    RustToolFailedEvent,
    RustToolFailureResult,
)
from mistralai_vibe_local_harness.vibe._subagents._models import (
    ChildTombstone,
    CreationCleanupPendingChild,
    CreationFailedChild,
    DeletingIdleChild,
    DeletingRunningChild,
    IdleChild,
    ReservedChild,
    RunningChild,
    SubagentModel,
    SubagentRuntimeState,
    TurnFailedChild,
)

CORE_AGENT_TOOL_NAMES = frozenset({
    "agents.list",
    "agents.spawn",
    "agents.send_message",
    "agents.interrupt",
})


class CoreListInput(SubagentModel):
    pass


class CoreSpawnInput(SubagentModel):
    name: str = Field(min_length=1)
    message: str = Field(min_length=1)
    config_name: str | None = Field(default=None, min_length=1)

    @field_validator("name")
    @classmethod
    def validate_child_name(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value:
            raise ValueError("child name must be a single path segment")
        return value


class CoreMessageInput(SubagentModel):
    agent_path: str = Field(min_length=1)
    message: str = Field(min_length=1)


class CoreInterruptInput(SubagentModel):
    name: str = Field(min_length=1)


def controller_action(
    action: RustRuntimeBuiltinToolCallAction,
) -> RustRuntimeBuiltinToolCallAction:
    """Translate the current Core contract into the local controller's private inputs."""
    arguments: dict[str, JsonValue]
    match action.call.name:
        case "agents.list":
            CoreListInput.model_validate(action.call.arguments)
            arguments = {}
        case "agents.spawn":
            spawn = CoreSpawnInput.model_validate(action.call.arguments)
            arguments = {
                "agentName": spawn.name,
                "message": spawn.message,
                "agentType": spawn.config_name,
            }
        case "agents.send_message":
            message = CoreMessageInput.model_validate(action.call.arguments)
            name = message.agent_path.removeprefix("/root/")
            if not name or "/" in name or name in {".", ".."}:
                raise ValueError(
                    "the local Runtime can message only direct child paths"
                )
            arguments = {"agentName": name, "message": message.message}
        case "agents.interrupt":
            interrupt = CoreInterruptInput.model_validate(action.call.arguments)
            arguments = {"agentName": interrupt.name}
        case _:
            raise ValueError(f"unsupported Core agent operation: {action.call.name}")
    return action.model_copy(
        update={"call": action.call.model_copy(update={"arguments": arguments})}
    )


def core_list_output(state: SubagentRuntimeState) -> dict[str, JsonValue]:
    agents: list[JsonValue] = []
    for child in sorted(
        state.children.values(), key=lambda child: child.admission_sequence
    ):
        status: Literal["running", "idle", "failed"]
        error: str | None = None
        match child.state:
            case (
                ReservedChild() | CreationFailedChild() | CreationCleanupPendingChild()
            ):
                continue
            case RunningChild():
                status = "running"
            case IdleChild():
                status = "idle"
            case TurnFailedChild(outcome=outcome):
                status, error = "failed", outcome.failure.message
            case ChildTombstone():
                status, error = "failed", "Child Session is closed and unavailable"
            case DeletingIdleChild() | DeletingRunningChild():
                status, error = "failed", "Child Session is closing"
        item: dict[str, JsonValue] = {
            "name": child.agent_name,
            "config_name": child.agent_type,
            "status": status,
            "pending_turn": status == "running",
        }
        if error is not None:
            item["error"] = error
        agents.append(item)
    return {"agents": agents}


def core_failure(
    action: RustRuntimeBuiltinToolCallAction, code: str, message: str
) -> RustToolFailedEvent:
    return RustToolFailedEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolFailureResult(
            content=[RustTextContentBlock(text=message)],
            error=RustProtocolError(code=code, message=message, retryable=False),
        ),
    )
