"""Closed durable models for parent-owned stateful subagent orchestration."""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from mistralai_vibe_local_harness.protocol import (
    RustHarnessNotification,
    RustRuntimeBuiltinToolName,
)

_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")

# Core rejects a whole configuration carrying more agent types than this.
MAX_DECLARED_AGENT_TYPES = 128
_MAX_INSTRUCTION_CHARS = 64 * 1024


class SubagentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _SessionIdentityModel(SubagentModel):
    @field_validator(
        "session_id", "root_session_id", "parent_session_id", check_fields=False
    )
    @classmethod
    def validate_session_id(cls, value: str | None) -> str | None:
        if value is not None and _SESSION_ID_PATTERN.fullmatch(value) is None:
            raise ValueError(f"invalid Session ID: {value!r}")
        return value


class RootSessionIdentity(_SessionIdentityModel):
    kind: Literal["root"] = "root"
    session_id: str
    root_session_id: str
    parent_session_id: None = None
    depth: Literal[0] = 0

    @model_validator(mode="after")
    def validate_ownership(self) -> Self:
        if self.session_id != self.root_session_id:
            raise ValueError("a root Session must identify itself as its root")
        return self


class ForkSessionIdentity(_SessionIdentityModel):
    kind: Literal["fork"] = "fork"
    session_id: str
    root_session_id: str
    parent_session_id: str
    depth: Literal[0] = 0

    @model_validator(mode="after")
    def validate_ownership(self) -> Self:
        if self.session_id == self.parent_session_id:
            raise ValueError("a fork Session cannot identify itself as its parent")
        return self


class SubagentSessionIdentity(_SessionIdentityModel):
    kind: Literal["subagent"] = "subagent"
    session_id: str
    root_session_id: str
    parent_session_id: str
    depth: Literal[1] = 1

    @model_validator(mode="after")
    def validate_ownership(self) -> Self:
        if self.session_id in {self.root_session_id, self.parent_session_id}:
            raise ValueError("a subagent Session must have a distinct identity")
        if self.parent_session_id != self.root_session_id:
            raise ValueError("a depth-one subagent must be owned by its root Session")
        return self


SessionIdentity = Annotated[
    RootSessionIdentity | ForkSessionIdentity | SubagentSessionIdentity,
    Field(discriminator="kind"),
]


class SubagentFailure(SubagentModel):
    code: str = Field(min_length=1)
    message: str
    retryable: bool


class CompletedChildTurnOutcome(SubagentModel):
    type: Literal["completed"] = "completed"
    generation: int = Field(ge=1)
    turn_id: str = Field(min_length=1)
    completed_at_unix_ms: int = Field(ge=0)
    output: list[JsonValue]
    final_answer: str


class FailedChildTurnOutcome(SubagentModel):
    type: Literal["failed"] = "failed"
    generation: int = Field(ge=1)
    turn_id: str = Field(min_length=1)
    completed_at_unix_ms: int = Field(ge=0)
    failure: SubagentFailure


class InterruptedChildTurnOutcome(SubagentModel):
    type: Literal["interrupted"] = "interrupted"
    generation: int = Field(ge=1)
    turn_id: str = Field(min_length=1)
    completed_at_unix_ms: int = Field(ge=0)
    reason: str


ChildTurnOutcome = Annotated[
    CompletedChildTurnOutcome | FailedChildTurnOutcome | InterruptedChildTurnOutcome,
    Field(discriminator="type"),
]


class PendingChildNotification(SubagentModel):
    sequence: int = Field(ge=1)
    agent_name: str = Field(min_length=1)
    generation: int = Field(ge=1)
    notification: RustHarnessNotification


class ParentNotificationQueue(SubagentModel):
    next_sequence: int = Field(default=1, ge=1)
    last_committed_sequence: int = Field(default=0, ge=0)
    pending: list[PendingChildNotification] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_sequences(self) -> Self:
        sequences = [item.sequence for item in self.pending]
        expected = list(range(self.last_committed_sequence + 1, self.next_sequence))
        if sequences != expected:
            raise ValueError(
                "pending child notifications must form one contiguous sequence"
            )
        return self


class ChildGenerationRef(SubagentModel):
    generation: int = Field(ge=1)
    turn_id: str = Field(min_length=1)


class ReservedChild(SubagentModel):
    type: Literal["reserved"] = "reserved"


class RunningChild(SubagentModel):
    type: Literal["running"] = "running"
    active: ChildGenerationRef


class IdleChild(SubagentModel):
    type: Literal["idle"] = "idle"
    last_outcome: CompletedChildTurnOutcome | InterruptedChildTurnOutcome


class CreationFailedChild(SubagentModel):
    type: Literal["creation_failed"] = "creation_failed"
    failure: SubagentFailure


class CreationCleanupPendingChild(SubagentModel):
    type: Literal["creation_cleanup_pending"] = "creation_cleanup_pending"
    failure: SubagentFailure


class TurnFailedChild(SubagentModel):
    type: Literal["turn_failed"] = "turn_failed"
    outcome: FailedChildTurnOutcome
    reusable: bool


class DeletingRunningChild(SubagentModel):
    type: Literal["deleting_running"] = "deleting_running"
    active: ChildGenerationRef


class DeletingIdleChild(SubagentModel):
    type: Literal["deleting_idle"] = "deleting_idle"


class ChildTombstone(SubagentModel):
    type: Literal["tombstone"] = "tombstone"


ChildLifecycleState = Annotated[
    ReservedChild
    | RunningChild
    | IdleChild
    | CreationFailedChild
    | CreationCleanupPendingChild
    | TurnFailedChild
    | DeletingRunningChild
    | DeletingIdleChild
    | ChildTombstone,
    Field(discriminator="type"),
]


class ChildSessionRecord(SubagentModel):
    agent_name: str = Field(min_length=1)
    agent_type: str | None
    child_session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN.pattern)
    spawn_action_id: str = Field(min_length=1)
    spawn_request_digest: str = Field(min_length=1)
    template_digest: str = Field(min_length=1)
    policy_ceiling_digest: str = Field(min_length=1)
    depth: Literal[1] = 1
    state: ChildLifecycleState
    last_notified_generation: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_notification_cursor(self) -> Self:
        generation = lifecycle_generation(self.state)
        if generation is not None and self.last_notified_generation > generation:
            raise ValueError("child notification cursor exceeds its latest generation")
        return self


class SpawnTarget(SubagentModel):
    type: Literal["spawn"] = "spawn"
    child_session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN.pattern)
    command: ChildGenerationRef
    child_command_id: str = Field(min_length=1)


class WaitTarget(SubagentModel):
    type: Literal["wait"] = "wait"
    generation: int = Field(ge=1)
    deadline_unix_ms: int = Field(ge=0)


class SendIntentTarget(SubagentModel):
    type: Literal["send"] = "send"
    child_session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN.pattern)


class SendStartTarget(SubagentModel):
    type: Literal["send_start"] = "send_start"
    command: ChildGenerationRef
    child_command_id: str = Field(min_length=1)


class SendSteerTarget(SubagentModel):
    type: Literal["send_steer"] = "send_steer"
    command: ChildGenerationRef
    child_command_id: str = Field(min_length=1)


class InterruptTarget(SubagentModel):
    type: Literal["interrupt"] = "interrupt"
    command: ChildGenerationRef
    child_command_id: str = Field(min_length=1)


class CloseIdleTarget(SubagentModel):
    type: Literal["close_idle"] = "close_idle"
    child_session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN.pattern)


class CloseRunningTarget(SubagentModel):
    type: Literal["close_running"] = "close_running"
    child_session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN.pattern)
    command: ChildGenerationRef
    child_command_id: str = Field(min_length=1)


ChildCommandTarget = (
    SpawnTarget
    | SendStartTarget
    | SendSteerTarget
    | InterruptTarget
    | CloseRunningTarget
)
SubagentOperationTarget = Annotated[
    SpawnTarget
    | WaitTarget
    | SendIntentTarget
    | SendStartTarget
    | SendSteerTarget
    | InterruptTarget
    | CloseIdleTarget
    | CloseRunningTarget,
    Field(discriminator="type"),
]


class PreparedReceipt(SubagentModel):
    type: Literal["prepared"] = "prepared"
    target: SubagentOperationTarget


class ChildCommandAcceptedReceipt(SubagentModel):
    type: Literal["child_command_accepted"] = "child_command_accepted"
    target: ChildCommandTarget


class CloseOutcomeRecordedReceipt(SubagentModel):
    type: Literal["outcome_recorded"] = "outcome_recorded"
    target: CloseRunningTarget
    outcome: ChildTurnOutcome

    @model_validator(mode="after")
    def validate_outcome_target(self) -> Self:
        if (
            self.outcome.generation != self.target.command.generation
            or self.outcome.turn_id != self.target.command.turn_id
        ):
            raise ValueError("close outcome does not match its active generation")
        return self


class CloseNotificationCommittedReceipt(SubagentModel):
    type: Literal["notification_committed"] = "notification_committed"
    target: CloseRunningTarget
    notification_id: str = Field(min_length=1)


class CloseCleanupStartedReceipt(SubagentModel):
    type: Literal["cleanup_started"] = "cleanup_started"
    target: CloseIdleTarget | CloseRunningTarget


class SucceededReceipt(SubagentModel):
    type: Literal["succeeded"] = "succeeded"
    target: SubagentOperationTarget
    result: JsonValue


class FailedReceipt(SubagentModel):
    type: Literal["failed"] = "failed"
    target: SubagentOperationTarget
    failure: SubagentFailure


ActiveSubagentReceiptState = (
    PreparedReceipt
    | ChildCommandAcceptedReceipt
    | CloseOutcomeRecordedReceipt
    | CloseNotificationCommittedReceipt
    | CloseCleanupStartedReceipt
    | SucceededReceipt
    | FailedReceipt
)


class AbandoningReceipt(SubagentModel):
    type: Literal["abandoning"] = "abandoning"
    previous: ActiveSubagentReceiptState


SubagentReceiptState = Annotated[
    ActiveSubagentReceiptState | AbandoningReceipt, Field(discriminator="type")
]


class SubagentOperationReceipt(SubagentModel):
    action_id: str = Field(min_length=1)
    admission_sequence: int = Field(ge=0)
    agent_name: str = Field(min_length=1)
    request_digest: str = Field(min_length=1)
    state: SubagentReceiptState


class ChildCommandResult(SubagentModel):
    turn_id: str = Field(min_length=1)


class ParentOriginatedChildCommandReceipt(SubagentModel):
    operation_key: str = Field(min_length=1)
    parent_session_id: str = Field(min_length=1, pattern=_SESSION_ID_PATTERN.pattern)
    target: ChildCommandTarget
    result: ChildCommandResult

    @model_validator(mode="after")
    def validate_result_target(self) -> Self:
        if self.result.turn_id != self.target.command.turn_id:
            raise ValueError("child command result does not match its target Turn")
        return self

    def validate_child_ownership(self, identity: SubagentSessionIdentity) -> None:
        if self.parent_session_id != identity.parent_session_id:
            raise ValueError("parent command receipt belongs to another parent Session")
        _validate_command_target(
            self.target,
            child_session_id=identity.session_id,
            operation_key=self.operation_key,
        )


class ResolvedToolGrant(SubagentModel):
    binding_id: str = Field(min_length=1)
    contract_digest: str = Field(min_length=1)
    permission: Literal["never", "ask", "always"]


class ResolvedContentGrant(SubagentModel):
    binding_id: str = Field(min_length=1)
    content_digest: str = Field(min_length=1)
    allowed_tool_names: list[str]


class ResolvedSourceGrant(SubagentModel):
    binding_id: str = Field(min_length=1)
    authority_digest: str = Field(min_length=1)
    model_access: Literal["direct", "programmatic", "both"]
    allowed_tool_names: list[str]


class ResolvedHookGrant(SubagentModel):
    binding_id: str = Field(min_length=1)
    hook_type: str
    matcher_digest: str = Field(min_length=1)
    mandatory: bool


class ResolvedSubagentPolicyCeiling(SubagentModel):
    workdir: str
    read_roots: list[str]
    write_roots: list[str]
    completion_grants: dict[str, str]
    tool_grants: dict[str, ResolvedToolGrant]
    skill_grants: dict[str, ResolvedContentGrant]
    mcp_grants: dict[str, ResolvedSourceGrant]
    allowed_connector_ids: list[str]
    connector_policy_digest: str | None
    allowed_connector_authentication: list[Literal["interactive", "existing_only"]]
    allowed_connector_execution_identities: list[Literal["auto", "deployment"]]
    hook_grants: dict[str, ResolvedHookGrant]
    sandbox_binding_id: str
    sandbox_authority_digest: str
    network_access: bool
    allowed_environment_names: list[str]
    approval_bypass: bool
    max_turn_iterations: int | None = Field(gt=0)
    max_output_tokens: int | None = Field(gt=0)

    @model_validator(mode="after")
    def validate_sorted_sets(self) -> Self:
        for label, values in (
            ("read roots", self.read_roots),
            ("write roots", self.write_roots),
            ("connector IDs", self.allowed_connector_ids),
            ("connector authentication", self.allowed_connector_authentication),
            (
                "connector execution identities",
                self.allowed_connector_execution_identities,
            ),
            ("environment names", self.allowed_environment_names),
        ):
            if values != sorted(set(values)):
                raise ValueError(f"{label} must be sorted and unique")
        return self


class DeclaredAgentTypeProfile(SubagentModel):
    # ``tool_ceiling`` is a ceiling, never a grant: a builtin absent from it is denied
    # in the child, and one present in it is resolved against the parent's own mode by
    # taking the stricter of the two. The text fields mirror what Core's agent-type
    # validation demands, because a blank one there invalidates the whole
    # configuration rather than one profile.
    agent_type: str = Field(min_length=1)
    description: str = Field(min_length=1)
    profile_path: str = Field(min_length=1)
    instructions: str | None = Field(
        default=None, min_length=1, max_length=_MAX_INSTRUCTION_CHARS
    )
    tool_ceiling: dict[RustRuntimeBuiltinToolName, Literal["allow", "ask", "deny"]] = (
        Field(default_factory=dict)
    )
    authority_digest: str = Field(min_length=1)

    @field_validator("agent_type", "description", "profile_path")
    @classmethod
    def validate_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("declared agent type text must not be blank")
        return value


class SubagentLimits(SubagentModel):
    max_depth: Literal[1] = 1
    max_children: int = Field(default=8, gt=0)
    max_concurrent_turns: int = Field(default=4, gt=0)
    max_lifetime_names: int = Field(default=128, gt=0)


class SubagentRuntimeState(SubagentModel):
    policy_ceiling: ResolvedSubagentPolicyCeiling
    next_operation_sequence: int = Field(default=0, ge=0)
    children: dict[str, ChildSessionRecord] = Field(default_factory=dict)
    operation_receipts: dict[str, SubagentOperationReceipt] = Field(
        default_factory=dict
    )
    notifications: ParentNotificationQueue = Field(
        default_factory=ParentNotificationQueue
    )

    @model_validator(mode="after")
    def validate_keys_and_sequences(self) -> Self:
        if list(self.children) != sorted(self.children):
            raise ValueError("child records must be stored in lexical agent-name order")
        if any(name != child.agent_name for name, child in self.children.items()):
            raise ValueError("child record key must match agent_name")
        if list(self.operation_receipts) != sorted(self.operation_receipts):
            raise ValueError(
                "subagent receipts must be stored in lexical Action-ID order"
            )
        if any(
            action_id != receipt.action_id
            for action_id, receipt in self.operation_receipts.items()
        ):
            raise ValueError("subagent receipt key must match action_id")
        sequences = [
            receipt.admission_sequence for receipt in self.operation_receipts.values()
        ]
        if len(sequences) != len(set(sequences)):
            raise ValueError("subagent admission sequences must be unique")
        if sequences and self.next_operation_sequence <= max(sequences):
            raise ValueError(
                "next subagent operation sequence must follow existing receipts"
            )
        for receipt in self.operation_receipts.values():
            child = self.children.get(receipt.agent_name)
            if child is None:
                raise ValueError("subagent receipt refers to an unknown child")
            _validate_operation_target(
                _receipt_target(receipt.state),
                child_session_id=child.child_session_id,
                operation_key=receipt.action_id,
            )
        for child in self.children.values():
            generation = lifecycle_generation(child.state)
            if generation is not None:
                turn_id = _lifecycle_turn_id(child.state)
                if turn_id != _turn_id(child.child_session_id, generation):
                    raise ValueError(
                        "child lifecycle Turn belongs to another Session or generation"
                    )
        return self


class ActiveSessionLifecycle(SubagentModel):
    type: Literal["active"] = "active"


class DeletingSessionTree(SubagentModel):
    type: Literal["deleting_tree"] = "deleting_tree"
    ordered_child_session_ids: list[str]
    deleted_child_session_ids: list[str]

    @model_validator(mode="after")
    def validate_deletion_progress(self) -> Self:
        if self.ordered_child_session_ids != sorted(
            set(self.ordered_child_session_ids)
        ):
            raise ValueError("child deletion plan must be unique and lexically ordered")
        if (
            self.deleted_child_session_ids
            != self.ordered_child_session_ids[: len(self.deleted_child_session_ids)]
        ):
            raise ValueError("deleted child IDs must be a prefix of the deletion plan")
        return self


SessionRuntimeLifecycle = Annotated[
    ActiveSessionLifecycle | DeletingSessionTree, Field(discriminator="type")
]


def lifecycle_generation(state: ChildLifecycleState) -> int | None:
    if isinstance(state, RunningChild | DeletingRunningChild):
        return state.active.generation
    if isinstance(state, IdleChild):
        return state.last_outcome.generation
    if isinstance(state, TurnFailedChild):
        return state.outcome.generation
    return None


def _receipt_target(state: SubagentReceiptState) -> SubagentOperationTarget:
    return (
        state.previous.target if isinstance(state, AbandoningReceipt) else state.target
    )


def _validate_operation_target(
    target: SubagentOperationTarget, *, child_session_id: str, operation_key: str
) -> None:
    if isinstance(target, SendIntentTarget | CloseIdleTarget):
        if target.child_session_id != child_session_id:
            raise ValueError(
                "subagent operation target belongs to another child Session"
            )
        return
    if isinstance(target, WaitTarget):
        return
    _validate_command_target(
        target, child_session_id=child_session_id, operation_key=operation_key
    )


def _validate_command_target(
    target: ChildCommandTarget, *, child_session_id: str, operation_key: str
) -> None:
    if isinstance(target, SpawnTarget | CloseRunningTarget):
        if target.child_session_id != child_session_id:
            raise ValueError("child command target belongs to another child Session")
    if target.command.turn_id != _turn_id(child_session_id, target.command.generation):
        raise ValueError("child command Turn belongs to another Session or generation")
    if isinstance(target, SpawnTarget) and target.command.generation != 1:
        raise ValueError("spawn command must start child generation one")
    suffix = (
        "start"
        if isinstance(target, SpawnTarget | SendStartTarget)
        else "steer"
        if isinstance(target, SendSteerTarget)
        else "interrupt"
    )
    if target.child_command_id != f"{operation_key}:{suffix}":
        raise ValueError("child command ID does not match its parent operation")


def _lifecycle_turn_id(state: ChildLifecycleState) -> str | None:
    if isinstance(state, RunningChild | DeletingRunningChild):
        return state.active.turn_id
    if isinstance(state, IdleChild):
        return state.last_outcome.turn_id
    if isinstance(state, TurnFailedChild):
        return state.outcome.turn_id
    return None


def _turn_id(child_session_id: str, generation: int) -> str:
    return f"{child_session_id}:turn:{generation}"
