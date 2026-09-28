"""Typed Vibe extension for starting a Turn before workspace preparation.

The Host owns worktree administration. Harness Core receives only the prepared
Turn input after this Runtime capability completes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import ConfigDict, Field, JsonValue
from pydantic.alias_generators import to_camel

from mistralai_vibe_local_harness.session_protocol import (
    ContentBlock,
    PublicError,
    SessionId,
    SessionProtocolModel,
    TurnId,
    UnixTimeMilliseconds,
    UserDisplayContentAnnotation,
)


class DeferredTurnModel(SessionProtocolModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        frozen=True,
        from_attributes=True,
    )


class TurnMentionStats(DeferredTurnModel):
    count: int = 0
    context_types: dict[str, int] = Field(default_factory=dict)
    file_extensions: dict[str, int] = Field(default_factory=dict)


class TurnStartRequest(DeferredTurnModel):
    idempotency_key: str | None = None
    session_id: SessionId
    message: tuple[ContentBlock, ...] = Field(min_length=1)
    injected: bool = False
    client_user_message_id: str | None = None
    auto_title: str | None = None
    user_display_content: UserDisplayContentAnnotation | None = None
    mention_stats: TurnMentionStats | None = None


class WorktreeEffectCallDisplay(DeferredTurnModel):
    summary: str
    content: str | None = None
    suffix: str = ""
    verb: str = ""
    message: str | None = None
    settled_verb: str = ""
    settled_message: str | None = None
    status_text: str


class WorktreeEffectResultDisplay(DeferredTurnModel):
    success: bool
    verb: str = ""
    message: str
    warnings: tuple[str, ...] = ()
    approval_note: str | None = None
    suffix: str = ""


class WorktreeEffectInput(DeferredTurnModel):
    name: str
    branch: str
    path: str


class PendingWorktreeEffectDetail(DeferredTurnModel):
    kind: Literal["worktree"] = "worktree"
    tool_name: Literal["worktree"] = "worktree"
    input: None = None
    display: WorktreeEffectCallDisplay


class CompletedWorktreeEffectDetail(DeferredTurnModel):
    kind: Literal["worktree"] = "worktree"
    tool_name: Literal["worktree"] = "worktree"
    input: WorktreeEffectInput
    display: WorktreeEffectCallDisplay


class RunningWorktreeEffectState(DeferredTurnModel):
    status: Literal["running"] = "running"
    output_text: str = ""


class CompletedWorktreeEffectState(DeferredTurnModel):
    status: Literal["completed"] = "completed"
    output: JsonValue = None
    output_text: str = ""
    duration_ms: float = 0.0
    display: WorktreeEffectResultDisplay


class FailedWorktreeEffectState(DeferredTurnModel):
    status: Literal["failed"] = "failed"
    error: PublicError
    output: JsonValue = None
    output_text: str = ""
    duration_ms: float = 0.0
    display: WorktreeEffectResultDisplay


class _WorktreeHistoryEntry(DeferredTurnModel):
    type: Literal["effect"] = "effect"
    id: str
    session_id: SessionId
    turn_id: TurnId | None = None
    created_at: UnixTimeMilliseconds
    updated_at: UnixTimeMilliseconds
    related_entry_id: str | None = None
    title: Literal["worktree"] = "worktree"


class RunningWorktreeHistoryEntry(_WorktreeHistoryEntry):
    generation_status: Literal["in_progress"] = "in_progress"
    detail: PendingWorktreeEffectDetail
    state: RunningWorktreeEffectState


class CompletedWorktreeHistoryEntry(_WorktreeHistoryEntry):
    generation_status: Literal["completed"] = "completed"
    detail: CompletedWorktreeEffectDetail
    state: CompletedWorktreeEffectState


class FailedWorktreeHistoryEntry(_WorktreeHistoryEntry):
    generation_status: Literal["completed"] = "completed"
    detail: PendingWorktreeEffectDetail
    state: FailedWorktreeEffectState


type PublicHistoryEntry = (
    RunningWorktreeHistoryEntry
    | CompletedWorktreeHistoryEntry
    | FailedWorktreeHistoryEntry
)


@dataclass(frozen=True, slots=True)
class PreparedRuntimeInput:
    turn: TurnStartRequest


@dataclass(frozen=True, slots=True)
class DeferredTurnPreparationContext:
    session_id: SessionId
    turn_id: TurnId
    started_at: UnixTimeMilliseconds


@dataclass(frozen=True, slots=True)
class DeferredTurnPreparationResult:
    runtime_input: PreparedRuntimeInput
    history_entries: tuple[PublicHistoryEntry, ...] = ()
    after_promotion: Callable[[], Awaitable[None]] | None = None


class DeferredTurnPreparationError(Exception):
    def __init__(
        self, error: Exception, history_entries: tuple[PublicHistoryEntry, ...] = ()
    ) -> None:
        super().__init__(str(error))
        self.error = error
        self.history_entries = history_entries


@dataclass(frozen=True, slots=True)
class DeferredTurnPreparation:
    pending_history_entries: tuple[PublicHistoryEntry, ...]
    run: Callable[
        [DeferredTurnPreparationContext], Awaitable[DeferredTurnPreparationResult]
    ]


@dataclass(frozen=True, slots=True)
class DeferredTurnStartParams:
    session_id: SessionId
    turn: TurnStartRequest
    prepare: DeferredTurnPreparation


@dataclass(frozen=True, slots=True)
class DeferredTurnStartResult:
    turn_id: TurnId
    session_id: SessionId
    started_at: UnixTimeMilliseconds
    last_event_id: int
    after_response: Callable[[], None]
    on_response_abandoned: Callable[[], None]


class DeferredTurnStartCapability(Protocol):
    async def start_deferred_turn(
        self, params: DeferredTurnStartParams
    ) -> DeferredTurnStartResult: ...


__all__ = [
    "CompletedWorktreeHistoryEntry",
    "DeferredTurnPreparation",
    "DeferredTurnPreparationContext",
    "DeferredTurnPreparationError",
    "DeferredTurnPreparationResult",
    "DeferredTurnStartCapability",
    "DeferredTurnStartParams",
    "DeferredTurnStartResult",
    "FailedWorktreeHistoryEntry",
    "PreparedRuntimeInput",
    "PublicHistoryEntry",
    "RunningWorktreeHistoryEntry",
    "TurnMentionStats",
    "TurnStartRequest",
]
