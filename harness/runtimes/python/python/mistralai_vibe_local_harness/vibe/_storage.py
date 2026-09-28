"""Private Harness Runtime persistence and restoration."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import secrets
import shutil
from threading import RLock
from typing import Annotated, Any, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    field_validator,
    model_validator,
)
import rfc8785

from mistralai_vibe_local_harness import HarnessSession
from mistralai_vibe_local_harness.protocol import (
    RustAcceptedApplyResult,
    RustActionsNextAction,
    RustAudioContentBlock,
    RustCapabilitiesChange,
    RustDispatchActionDirective,
    RustEmbeddedResourceContentBlock,
    RustHarnessCapabilitySet,
    RustHarnessConfig,
    RustHarnessHookBinding,
    RustHarnessInput,
    RustHarnessSettings,
    RustImageContentBlock,
    RustKeepActionDirective,
    RustLLMCallAction,
    RustModelToolCallPart,
    RustPluginContextDefinition,
    RustPluginsChange,
    RustReasoningPart,
    RustReconfigureEvent,
    RustRefreshActionDirective,
    RustResourceLinkContentBlock,
    RustSessionTransition,
    RustSettingsChange,
    RustTextContentBlock,
    parse_apply_result,
)
from mistralai_vibe_local_harness.session_protocol import JsonObject, PublicSessionState
from mistralai_vibe_local_harness.vibe._errors import (
    HarnessCommandConflictError,
    HarnessInvalidSessionStoreError,
    HarnessReplayDivergenceError,
    HarnessSessionBusyError,
    HarnessStoreRequiresNewerReaderError,
)
from mistralai_vibe_local_harness.vibe._file_image_fallback import (
    export_file_image_fallback,
)
from mistralai_vibe_local_harness.vibe._process_actions import (
    process_id as expected_process_id,
)
from mistralai_vibe_local_harness.vibe._subagents import (
    ActiveSessionLifecycle,
    ForkSessionIdentity,
    ParentOriginatedChildCommandReceipt,
    RootSessionIdentity,
    SessionIdentity,
    SessionRuntimeLifecycle,
    SubagentRuntimeState,
    SubagentSessionIdentity,
)

STORE_FORMAT = "mistral.vibe.unified-session-store/v1"
# Readers accept lower minor versions and reject higher ones.
# 2: projection deltas; 3: checkpoint-derived interop exports;
# 4: chunked transcripts; 5: checkpoint capability baselines;
# 6: self-describing reservations and abandoned receipts;
# 7: checkpoint configuration baselines and recorded transition shapes.
# Downgrading after a newer build writes the store is unsupported.
STORE_FORMAT_MINOR = 7
# Sealing a chunk once its contents pass this size makes a chunk's identity a
# function of the list prefix alone, so appending leaves every sealed chunk
# byte-identical and only the open tail is rewritten.
_CHUNK_TARGET_BYTES = 64 * 1024
_CHUNKS_DIRNAME = "chunks"
_QUARANTINE_DIRNAME = "quarantine"
logger = logging.getLogger(__name__)
_CHUNK_FILE_PATTERN = re.compile(r"^[0-9a-f]{64}\.json$")
# A load re-reads most of the transcript it read last time, so without a cache
# the pool turns one document read into one read per chunk. Chunks are named by
# their digest, so a cached body can never be stale; overflowing the budget
# costs re-reads, never correctness.
_CHUNK_CACHE_BYTES = 16 * 1024 * 1024
_CHECKPOINT_MESSAGES_PATH = ("context", "messages")
_PROJECTION_HISTORY_PATH = ("snapshot", "history", "entries")
_GENERATION_PATTERN = re.compile(r"^[0-9]{16}$")
_MAX_GENERATION = 9_999_999_999_999_999
_RUNTIME_STATE_VERSION = 3
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_TIMESTAMP_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$"
)
_JOURNAL_PATH_PATTERN = re.compile(r"^journal/[0-9]{16}\.jsonl$")
_JOURNAL_SEGMENT_PATTERN = re.compile(r"^[0-9]{16}\.jsonl$")
# One past what `load` reads, because `list_sessions` and `_resolve_legacy_import_id`
# read a store they hold no lease on: retaining the predecessor gives such a reader a
# whole publication of slack before `_load_current` has to retry.
_RETAINED_GENERATIONS = 2
_MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
_MAX_SAFE_JSON_INTEGER = 2**53 - 1
_CANONICAL_INTEGER_RANGE = range(-_MAX_SAFE_JSON_INTEGER, _MAX_SAFE_JSON_INTEGER + 1)

type Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type Generation = Annotated[str, Field(pattern=r"^[0-9]{16}$")]
type Timestamp = Annotated[
    str,
    Field(
        pattern=(
            r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:"
            r"[0-9]{2}\.[0-9]{3}Z$"
        )
    ),
]


class _StoredModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HarnessStoreCapacityError(RuntimeError):
    pass


class CurrentPointerV1(_StoredModel):
    store_format: Literal["mistral.vibe.unified-session-store/v1"] = STORE_FORMAT
    # Optional so pre-delta (minor-1) pointers restore unambiguously to 1.
    store_format_minor: int = Field(default=1, ge=1)
    session_id: str
    generation: Generation
    snapshot_sequence: int = Field(ge=0)
    manifest_sha256: Sha256

    @classmethod
    def newer_minor(cls, document: JsonValue) -> int | None:
        """Return the pointer minor when it is newer than this reader understands.

        A newer writer records a higher ``store_format_minor`` and may add
        pointer fields alongside it. Strict validation would reject those unknown
        fields and hide the cause, so a reader reads the minor from the raw
        document first: a higher minor means the store needs a newer reader. A
        pointer at this reader's minor still passes through strict validation, so
        an unexpected field there is rejected as a broken store.
        """
        if not isinstance(document, dict):
            return None
        minor = document.get("store_format_minor")
        if isinstance(minor, bool) or not isinstance(minor, int):
            return None
        return minor if minor > STORE_FORMAT_MINOR else None


class StoredFileV1(_StoredModel):
    path: str
    sha256: Sha256
    chunks: tuple[Sha256, ...] | None = None
    """Ordered pool chunks whose concatenation refills the document's transcript.

    ``path`` and ``sha256`` then describe the envelope, which holds that list
    empty. Absent on records written before the pool existed and on records
    whose document carries no transcript, both of which are stored whole.
    """

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if value in {"", ".", ".."} or "/" in value or "\\" in value:
            raise ValueError("generation record path must be one file name")
        return value


class StoredCheckpointV1(StoredFileV1):
    checkpoint_version: Literal[1] = 1


class RecoveryJournalSegmentV1(_StoredModel):
    path: str
    first_sequence: int = Field(ge=1)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if _JOURNAL_PATH_PATTERN.fullmatch(value) is None:
            raise ValueError("invalid recovery journal segment path")
        return value


class GenerationManifestV1(_StoredModel):
    manifest_version: Literal[1] = 1
    session_id: str
    generation: Generation
    created_at: Timestamp
    snapshot_sequence: int = Field(ge=0)
    execution_state: Literal["quiescent", "recoverable"]
    checkpoint: StoredCheckpointV1
    runtime_state: StoredFileV1
    projection_state: StoredFileV1
    # Only a generation written before minor 3 names an export document; the reader
    # derives that document now, and leaves the record it no longer needs unread.
    interop_export: StoredFileV1 | None = None
    recovery_journal_segment: RecoveryJournalSegmentV1

    @model_validator(mode="after")
    def validate_sequence(self) -> Self:
        if self.recovery_journal_segment.first_sequence != self.snapshot_sequence + 1:
            raise ValueError("journal segment must begin after the snapshot sequence")
        if self.runtime_state.chunks is not None:
            raise ValueError("the runtime state carries no transcript to pool")
        # A quiescent generation may or may not name an export, depending on which
        # build wrote it. A recoverable one never had a committed history to export.
        if self.execution_state == "recoverable" and self.interop_export is not None:
            raise ValueError("a recoverable generation cannot have an interop export")
        # Only a build that predates the pool ever named an export, so such a record
        # is always monolithic; the chunk collector relies on that to ignore it.
        if self.interop_export is not None and self.interop_export.chunks is not None:
            raise ValueError("an interop export record predates the chunk pool")
        return self


class CommandReceiptV1(_StoredModel):
    client_command_id: str
    method: str
    params_sha256: Sha256
    state: Literal["reserved", "succeeded", "failed"]
    response: JsonValue = None
    params: dict[str, JsonValue] | None = None
    """The parameters a reserved command is resumed from.

    A reservation outlives the journal record that made it: folding the journal
    into a snapshot keeps the receipt and drops the record, so a receipt that
    described its command only by digest left nothing to resume the command
    from. Settled receipts drop the parameters again, and a reserved receipt
    restored from a generation written before this field existed has none --
    that reservation is unresumable and is reported as orphaned instead.
    """

    @model_validator(mode="after")
    def validate_params_digest(self) -> Self:
        if self.params is None:
            return self
        if self.state != "reserved":
            raise ValueError("a settled command receipt carries no parameters")
        if self.params_sha256 != sha256_json(self.params):
            raise ValueError("command parameter digest does not match its content")
        return self


class RuntimeActionV1(_StoredModel):
    action_id: str
    kind: Literal[
        "completion", "tool", "hook", "process", "callback", "child", "filesystem"
    ]
    state: Literal["pending", "running", "succeeded", "failed"]
    request_sha256: Sha256
    request: JsonValue
    lease_id: str | None = None
    recovery_mode: Literal[
        "reconnect_or_fail", "redeliver", "idempotent_retry", "reconcile", "fail"
    ]
    process_manager_instance_id: str | None = None
    process_request_sha256: Sha256 | None = None
    prepared_process_start: PreparedProcessStartV1 | None = None
    result: JsonValue = None
    request_pruned: bool = False
    """Whether a settled completion's ``request`` was dropped.

    ``request_sha256`` still describes the original request. Generations written
    before this field existed restore with it false and keep their request.
    """
    result_pruned: bool = False
    """Whether a settled completion's ``result`` was dropped.

    Only ``_resolve_action``'s redelivery short-circuit reads a result back, and
    only for Actions Core still holds, which the prune excludes. The flag separates
    "pruned" from "never produced one" so that an unexpected read fails with an
    explanation instead of validating ``None``.
    """

    @model_validator(mode="after")
    def validate_request_digest(self) -> Self:
        if self.request_pruned:
            if self.request is not None:
                raise ValueError("a pruned action request must be empty")
            if self.kind != "completion":
                raise ValueError("only a completion action request can be pruned")
            if self.state in {"pending", "running"}:
                raise ValueError("an unsettled action request cannot be pruned")
        elif self.request_sha256 != sha256_json(self.request):
            raise ValueError("action request digest does not match its content")
        if self.result_pruned:
            if self.result is not None:
                raise ValueError("a pruned action result must be empty")
            if self.kind != "completion":
                raise ValueError("only a completion action result can be pruned")
            if self.state in {"pending", "running"}:
                raise ValueError("an unsettled action result cannot be pruned")
        dispatch_values = (
            self.process_manager_instance_id,
            self.process_request_sha256,
        )
        if any(value is None for value in dispatch_values) != all(
            value is None for value in dispatch_values
        ):
            raise ValueError("process dispatch identity must be complete")
        if self.prepared_process_start is not None:
            prepared = self.prepared_process_start
            if (
                self.process_manager_instance_id is None
                or prepared.start_action_id != self.action_id
                or prepared.start_call_id != _action_call_id(self.request)
                or prepared.manager_instance_id != self.process_manager_instance_id
                or _action_name(self.request) != "process.start"
            ):
                raise ValueError("prepared process start disagrees with its Action")
        return self


class RuntimeCallbackV1(_StoredModel):
    callback_id: str
    kind: str
    state: Literal["pending", "resolved", "failed"]
    routing: dict[str, JsonValue]
    result: JsonValue = None


class ProviderOperationV1(_StoredModel):
    operation_id: str
    provider: str
    state: Literal["pending", "running", "succeeded", "failed"]
    request_sha256: Sha256
    recovery_mode: Literal["reconnect_or_fail", "idempotent_retry", "fail"]
    idempotency_key: str | None = None
    result: JsonValue = None
    """Always empty on generations written since results stopped being duplicated here.

    It held a verbatim copy of the owning Action's result and nothing ever read it.
    The field survives because ``_StoredModel`` forbids extras, so older generations
    must still validate; ``_discard_provider_operation_results`` empties those as
    soon as the session next publishes.
    """


class PreparedProcessStartV1(_StoredModel):
    process_id: str
    start_action_id: str
    start_call_id: str
    manager_instance_id: str
    command: str
    cwd: str
    command_environment: Literal["unix", "git_bash", "powershell"]
    created_at: Timestamp


class ManagedProcessV1(_StoredModel):
    process_id: str
    start_action_id: str
    start_call_id: str
    manager_instance_id: str
    command: str
    cwd: str
    command_environment: Literal["unix", "git_bash", "powershell"]
    pty_backend: Literal["posix", "ConPTY", "WinPTY"] | None
    start_outcome: Literal["accepted", "failed"]
    start_failure_stage: Literal["post_spawn", "persistence", "recovery"] | None
    status: Literal["running", "completed", "failed", "stopped", "orphaned"]
    exit_code: int | None = None
    created_at: Timestamp
    started_at: Timestamp | None
    finished_at: Timestamp | None

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:  # noqa: PLR0912 - one branch per lifecycle invariant
        if self.start_outcome == "accepted":
            if (
                self.start_failure_stage is not None
                or self.pty_backend is None
                or self.started_at is None
            ):
                raise ValueError("accepted process start has incomplete launch facts")
        elif self.start_failure_stage in {"post_spawn", "persistence"}:
            if (
                self.pty_backend is None
                or self.started_at is None
                or self.status not in {"completed", "failed", "orphaned"}
            ):
                raise ValueError("failed spawned process has invalid launch facts")
        elif self.start_failure_stage == "recovery":
            if (
                self.pty_backend is not None
                or self.started_at is not None
                or self.status != "orphaned"
            ):
                raise ValueError("recovered uncertain start must be orphaned")
        else:
            raise ValueError("failed process start requires a failure stage")

        if self.pty_backend == "posix" and self.command_environment != "unix":
            raise ValueError("POSIX PTY requires a Unix command environment")
        if (
            self.pty_backend in {"ConPTY", "WinPTY"}
            and self.command_environment == "unix"
        ):
            raise ValueError("Windows PTY requires a Windows command environment")
        if self.status == "running":
            if self.finished_at is not None or self.exit_code is not None:
                raise ValueError("running process cannot have terminal facts")
        elif self.finished_at is None:
            raise ValueError("terminal process requires a finish timestamp")
        elif self.status == "completed":
            if self.exit_code is None or not -(2**63) <= self.exit_code <= 2**63 - 1:
                raise ValueError("completed process requires a signed 64-bit exit code")
        elif self.exit_code is not None:
            raise ValueError("non-completed process cannot have an exit code")

        if self.started_at is not None and self.created_at > self.started_at:
            raise ValueError("process start time precedes creation")
        minimum_finished_at = self.started_at or self.created_at
        if self.finished_at is not None and minimum_finished_at > self.finished_at:
            raise ValueError("process finish time precedes its launch")
        return self


class SubmittedProcessNotificationV1(_StoredModel):
    process_id: str
    notification_id: str


class PluginLockEntryV1(_StoredModel):
    name: str
    """The provider's key, opaque here."""

    content_digest: Sha256
    """The package store's CAS key, and the only thing the SDK can act on."""


class PluginLockV1(_StoredModel):
    lock_version: Literal[1] = 1

    snapshot_digest: Sha256 | None = None
    """Absent when the session pinned no plugins, so there is nothing to name."""

    plugins: list[PluginLockEntryV1]

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        _require_sorted_unique(self.plugins, lambda item: item.name, "plugin name")
        return self


class ChildDependencyV1(_StoredModel):
    session_id: str
    stateful: bool
    state: Literal["pending", "running", "completed", "failed"]


class SessionPin(StrEnum):
    """A user-selected choice pinned to a session and restored when it reopens.

    Each member's value is the ``SessionMetadataV1`` field that stores it, so the
    storage, lookup, and propagation of pins stay generic: adding a new pin is a
    new member here plus its field below. The value stays flat in metadata, so
    older snapshots load unchanged. Members are declared in the order a restore
    applies them: a later pin can depend on what an earlier one selected.
    """

    ACTIVE_MODEL = "active_model"
    AGENT_NAME = "agent_name"
    REASONING_EFFORT = "reasoning_effort"


class SessionMetadataV1(_StoredModel):
    cwd: str | None
    # The concrete model alias pinned to the session; see ``SessionPin``.
    active_model: str | None = None
    # The running mode (agent profile name) the session is in. Backward-compatible:
    # older snapshots load as ``None`` and keep their configured default agent.
    agent_name: str | None = None
    reasoning_effort: str | None = None
    root_session_id: str
    parent_session_id: str | None = None
    subagent_spawn_key: str | None = None
    subagent_template_digest: str | None = None
    subagent_policy_ceiling_digest: str | None = None
    # Per-session hook bindings, re-supplied to the Core on every restore so the journal
    # replay matches the transitions the bindings originally produced. Handlers are
    # Runtime code and are attached from the Host registry by binding ID, not stored here.
    hook_bindings: list[RustHarnessHookBinding] = Field(default_factory=list)

    def pin(self, pin: SessionPin) -> str | None:
        return getattr(self, pin.value)

    def pin_values(self) -> dict[str, str | None]:
        """The stored pins as constructor kwargs, so propagation copies them as a set."""
        return {pin.value: getattr(self, pin.value) for pin in SessionPin}


class _RuntimeStateBase(_StoredModel):
    session_id: str
    snapshot_sequence: int = Field(ge=0)
    command_receipts: list[CommandReceiptV1]
    actions: list[RuntimeActionV1]
    callbacks: list[RuntimeCallbackV1]
    provider_operations: list[ProviderOperationV1]
    processes: list[ManagedProcessV1]
    submitted_process_notifications: list[SubmittedProcessNotificationV1]
    plugin_lock: PluginLockV1
    children: list[ChildDependencyV1]
    import_provenance: ImportProvenanceV1 | None = None

    @model_validator(mode="after")
    def validate_stable_order(self) -> Self:
        _require_sorted_unique(
            self.command_receipts,
            lambda item: item.client_command_id,
            "client command ID",
        )
        _require_sorted_unique(self.actions, lambda item: item.action_id, "action ID")
        _require_sorted_unique(
            self.callbacks, lambda item: item.callback_id, "callback ID"
        )
        _require_sorted_unique(
            self.provider_operations,
            lambda item: item.operation_id,
            "provider operation ID",
        )
        _require_sorted_unique(
            self.processes, lambda item: item.process_id, "process ID"
        )
        _require_unique(
            self.processes, lambda item: item.start_action_id, "process start Action ID"
        )
        _require_sorted_unique(
            self.submitted_process_notifications,
            lambda item: item.process_id,
            "submitted process notification",
        )
        _validate_process_relationships(self.session_id, self.actions, self.processes)
        processes = {item.process_id: item for item in self.processes}
        for submitted in self.submitted_process_notifications:
            process = processes.get(submitted.process_id)
            if process is None or process.status == "running":
                raise ValueError("process notification must name a terminal process")
            if submitted.notification_id != _process_notification_id(
                process.process_id
            ):
                raise ValueError("process notification has an invalid identity")
        _require_sorted_unique(
            self.children, lambda item: item.session_id, "child session ID"
        )
        return self

    @property
    def quiescent(self) -> bool:
        return not (
            any(receipt.state == "reserved" for receipt in self.command_receipts)
            or any(action.state in {"pending", "running"} for action in self.actions)
            or any(callback.state == "pending" for callback in self.callbacks)
            or any(
                operation.state in {"pending", "running"}
                for operation in self.provider_operations
            )
            or any(process.status == "running" for process in self.processes)
            or any(
                process.status != "running"
                and not any(
                    submitted.process_id == process.process_id
                    for submitted in self.submitted_process_notifications
                )
                for process in self.processes
            )
            or any(
                child.stateful and child.state in {"pending", "running"}
                for child in self.children
            )
        )


class RuntimeStateV3(_RuntimeStateBase):
    runtime_state_version: Literal[3] = 3
    storage_lifetime: Literal["persistent", "ephemeral"] = "persistent"
    session_metadata: SessionMetadataV1
    identity: SessionIdentity
    lifecycle: SessionRuntimeLifecycle = Field(default_factory=ActiveSessionLifecycle)
    # The Core checkpoint deliberately omits delivery state, so the Runtime owns the
    # accepted-input cursor and hands it back on restore. Older generations predate the
    # field and load as zero, which is the fresh sequence they were written against.
    core_last_input_id: int = Field(default=0, ge=0, le=_MAX_SAFE_JSON_INTEGER)
    core_capabilities: RustHarnessCapabilitySet | None = None
    """The capability set Core held when this generation's checkpoint was taken.

    A journalled input carries the same baseline beside it, but a fold leaves no
    input to carry it, and the checkpoint does not describe itself: a restore
    would then have to assume Core is on whatever set the caller supplied, which
    is how a session comes back on capabilities Core never had. Generations
    written before this field have none and keep the older assumption.
    """
    core_settings: RustHarnessSettings | None = None
    """The settings Core held when this generation's checkpoint was taken.

    Recorded for the reason the capability set is: Core consults its settings to
    decide a transition, so a replay under the restoring process's settings can
    reach a different decision than the one the journal recorded.
    """
    core_plugins: list[RustPluginContextDefinition] | None = None
    """The plugin contexts Core held when this generation's checkpoint was taken."""

    pending_transition: RustSessionTransition | None = None
    subagents: SubagentRuntimeState | None = None
    parent_command_receipts: dict[str, ParentOriginatedChildCommandReceipt] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def validate_identity_and_ownership(self) -> Self:
        if self.identity.session_id != self.session_id:
            raise ValueError("Runtime identity belongs to another session")
        metadata = self.session_metadata
        if (
            metadata.root_session_id != self.identity.root_session_id
            or metadata.parent_session_id != self.identity.parent_session_id
        ):
            raise ValueError("Runtime identity and session metadata disagree")
        if isinstance(self.identity, SubagentSessionIdentity):
            if not all((
                metadata.subagent_spawn_key,
                metadata.subagent_template_digest,
                metadata.subagent_policy_ceiling_digest,
            )):
                raise ValueError(
                    "a subagent requires its durable Host binding identity"
                )
            if self.subagents is not None:
                raise ValueError("a depth-one subagent cannot own subagents")
            if not isinstance(self.lifecycle, ActiveSessionLifecycle):
                raise ValueError("a subagent cannot own root tree-deletion state")
        elif self.parent_command_receipts:
            raise ValueError("a root or fork cannot retain parent command receipts")
        elif any((
            metadata.subagent_spawn_key,
            metadata.subagent_template_digest,
            metadata.subagent_policy_ceiling_digest,
        )):
            raise ValueError("a root or fork cannot carry a subagent Host binding")
        if list(self.parent_command_receipts) != sorted(self.parent_command_receipts):
            raise ValueError(
                "parent command receipts must be stored in lexical key order"
            )
        if any(
            operation_key != receipt.operation_key
            for operation_key, receipt in self.parent_command_receipts.items()
        ):
            raise ValueError("parent command receipt key must match operation_key")
        if isinstance(self.identity, SubagentSessionIdentity):
            for receipt in self.parent_command_receipts.values():
                receipt.validate_child_ownership(self.identity)
        return self

    @property
    def quiescent(self) -> bool:
        return self.pending_transition is None and super().quiescent


class ProjectionStateV1(_StoredModel):
    projection_state_version: Literal[1] = 1
    session_id: str
    snapshot_sequence: int = Field(ge=0)
    watermark: int = Field(ge=0)
    snapshot: PublicSessionState


class AppendEntryOp(_StoredModel):
    """Append one public history entry at the tail."""

    op: Literal["append_entry"] = "append_entry"
    entry: JsonObject


class ReplaceEntryOp(_StoredModel):
    """Replace the public history entry with the matching ``id`` in place."""

    op: Literal["replace_entry"] = "replace_entry"
    id: str
    entry: JsonObject


class RemoveEntryOp(_StoredModel):
    """Drop the public history entry with the matching ``id``.

    The projector removes open-effect entries when a turn completes
    (``_projection.py``); replay must reproduce that removal or a restored
    projection would keep entries the live path dropped.
    """

    op: Literal["remove_entry"] = "remove_entry"
    id: str


class SetEnvelopeOp(_StoredModel):
    """Set every public-state field except ``history.entries``.

    The envelope (session metadata, latest turn, callbacks, turn queue, history
    cursor) is ~1 KB, so carrying it whole whenever any of it changes is both
    trivially complete and negligible next to the history payload the entry ops
    keep incremental. ``state.history.entries`` is empty here; the entry ops own
    it.
    """

    op: Literal["set_envelope"] = "set_envelope"
    state: PublicSessionState


class SetHistoryEntriesOp(_StoredModel):
    """Replace the whole history entry list.

    A correctness fallback for the rare advance the incremental entry ops cannot
    reconstruct by id — notably when the projector emits two entries that share
    an id (the subagent catch-up path appends an identical user message twice),
    where a by-id diff cannot tell "add a second copy" from "already present."
    Carrying the entries whole for that one advance keeps replay byte-exact; it is
    equivalent to the legacy full snapshot for entries only.
    """

    op: Literal["set_history_entries"] = "set_history_entries"
    entries: list[JsonObject]


ProjectionOp = Annotated[
    AppendEntryOp
    | ReplaceEntryOp
    | RemoveEntryOp
    | SetEnvelopeOp
    | SetHistoryEntriesOp,
    Field(discriminator="op"),
]
ProjectionDelta = tuple[ProjectionOp, ...]


def _projection_envelope(state: PublicSessionState) -> PublicSessionState:
    return state.model_copy(
        update={"history": state.history.model_copy(update={"entries": []})}
    )


def compute_projection_delta(
    prior: PublicSessionState, new: PublicSessionState
) -> ProjectionDelta:
    """Describe how ``new`` differs from ``prior`` as structured operations.

    Ops are derived from the independently authored ``new`` snapshot, so a
    forgotten mutation cannot silently vanish: ``apply_projection_delta(prior,
    delta)`` would then diverge from ``new`` and the projector's oracle test
    fails. The projector never reorders surviving entries and only appends new
    ones at the tail, so removals (prior order) followed by appends/replaces
    (``new`` order) reconstruct ``new`` exactly.

    Entry ids are not guaranteed unique (the subagent catch-up path can append an
    identical entry twice), so the incremental entry ops are self-checked against
    ``new``; if they do not reconstruct it, they are replaced by a single
    ``set_history_entries`` op that carries the entries whole. This makes
    "``apply_projection_delta(prior, compute_projection_delta(prior, new))``
    equals ``new``" a guaranteed postcondition for any input.
    """
    prior_entries = prior.history.entries
    new_entries = new.history.entries
    prior_by_id = {cast(str, entry.get("id")): entry for entry in prior_entries}
    new_ids = {cast(str, entry.get("id")) for entry in new_entries}
    entry_ops: list[ProjectionOp] = []
    for entry in prior_entries:
        entry_id = cast(str, entry.get("id"))
        if entry_id not in new_ids:
            entry_ops.append(RemoveEntryOp(id=entry_id))
    for entry in new_entries:
        entry_id = cast(str, entry.get("id"))
        existing = prior_by_id.get(entry_id)
        if existing is None:
            entry_ops.append(AppendEntryOp(entry=entry))
        elif existing != entry:
            entry_ops.append(ReplaceEntryOp(id=entry_id, entry=entry))
    # Self-check the incremental entry ops; fall back to a whole-list replacement
    # when a by-id diff cannot reproduce duplicate ids or any other quirk. The
    # check itself can raise — duplicate ids produce two remove_entry ops for the
    # same id, and the second finds nothing to remove — so any failure to
    # reconstruct ``new`` is treated as "fall back", never propagated.
    try:
        reconstructs = apply_projection_delta(prior, entry_ops).history.entries == list(
            new_entries
        )
    except ValueError:
        reconstructs = False
    if not reconstructs:
        entry_ops = [SetHistoryEntriesOp(entries=list(new_entries))]
    ops: list[ProjectionOp] = list(entry_ops)
    if _projection_envelope(prior) != _projection_envelope(new):
        ops.append(SetEnvelopeOp(state=_projection_envelope(new)))
    return tuple(ops)


def apply_projection_delta(
    prior: PublicSessionState, delta: Iterable[ProjectionOp]
) -> PublicSessionState:
    """Reconstruct the advanced projection by folding ``delta`` over ``prior``.

    Shared by the live projector (round-trip oracle) and journal replay so a
    delta reconstructs the same state online and on restore. A missing entry
    reference is a hard :class:`ValueError`, matching strict replay.
    """
    entries: list[JsonObject] = list(prior.history.entries)
    envelope = prior
    for op in delta:
        match op:
            case AppendEntryOp(entry=entry):
                entries.append(entry)
            case ReplaceEntryOp(id=entry_id, entry=entry):
                if not _replace_projection_entry(entries, entry_id, entry):
                    raise ValueError("projection delta replaces an absent entry")
            case RemoveEntryOp(id=entry_id):
                remaining = [item for item in entries if item.get("id") != entry_id]
                if len(remaining) == len(entries):
                    raise ValueError("projection delta removes an absent entry")
                entries = remaining
            case SetHistoryEntriesOp(entries=full_entries):
                entries = list(full_entries)
            case SetEnvelopeOp(state=state):
                envelope = state
    return envelope.model_copy(
        update={"history": envelope.history.model_copy(update={"entries": entries})}
    )


def _replace_projection_entry(
    entries: list[JsonObject], entry_id: str, replacement: JsonObject
) -> bool:
    for index, entry in enumerate(entries):
        if entry.get("id") == entry_id:
            entries[index] = replacement
            return True
    return False


class UnifiedInteropSourceV1(_StoredModel):
    backend: Literal["unified"] = "unified"
    session_id: str
    generation: Generation
    snapshot_sequence: int = Field(ge=0)


class LegacyInteropSourceV1(_StoredModel):
    backend: Literal["legacy"] = "legacy"
    session_id: str
    store_revision: str


InteropSourceV1 = Annotated[
    UnifiedInteropSourceV1 | LegacyInteropSourceV1, Field(discriminator="backend")
]


class ImportProvenanceV1(_StoredModel):
    source: InteropSourceV1
    history_sha256: Sha256
    imported_at: Timestamp


class InteropSystemMessageV1(_StoredModel):
    role: Literal["system"] = "system"
    content: list[RustTextContentBlock] = Field(min_length=1)


class InteropFileImageFallbackV1(_StoredModel):
    type: Literal["file"] = "file"
    name: str


class InteropImageContentBlockV1(RustImageContentBlock):
    file_fallback: InteropFileImageFallbackV1 | None = None


InteropContentBlockV1 = Annotated[
    RustTextContentBlock
    | InteropImageContentBlockV1
    | RustAudioContentBlock
    | RustResourceLinkContentBlock
    | RustEmbeddedResourceContentBlock,
    Field(discriminator="type"),
]


class InteropUserMessageV1(_StoredModel):
    role: Literal["user"] = "user"
    content: list[InteropContentBlockV1] = Field(min_length=1)


class InteropAssistantContentPartV1(_StoredModel):
    type: Literal["content"] = "content"
    content: InteropContentBlockV1


InteropAssistantPartV1 = Annotated[
    InteropAssistantContentPartV1 | RustReasoningPart | RustModelToolCallPart,
    Field(discriminator="type"),
]


class InteropAssistantMessageV1(_StoredModel):
    role: Literal["assistant"] = "assistant"
    parts: list[InteropAssistantPartV1] = Field(min_length=1)


class InteropToolMessageV1(_StoredModel):
    role: Literal["tool"] = "tool"
    tool_call_id: str
    name: str
    outcome: Literal["success", "failure"]
    content: list[InteropContentBlockV1]
    meta: dict[str, JsonValue] | None = Field(default=None, alias="_meta")


InteropHistoryMessageV1 = Annotated[
    InteropSystemMessageV1
    | InteropUserMessageV1
    | InteropAssistantMessageV1
    | InteropToolMessageV1,
    Field(discriminator="role"),
]
_INTEROP_HISTORY_ADAPTER = TypeAdapter(list[InteropHistoryMessageV1])
_CONTENT_PART_TYPES = {"text", "image", "audio", "resource_link", "resource"}


class CommittedHistoryV1(_StoredModel):
    interop_version: Literal[1] = 1
    source: InteropSourceV1
    history: list[InteropHistoryMessageV1]
    history_sha256: Sha256

    @model_validator(mode="after")
    def validate_fingerprint(self) -> Self:
        if self.history_sha256 != history_fingerprint(self.history):
            raise ValueError("committed history fingerprint does not match its content")
        return self


RuntimeActionV1.model_rebuild()
RuntimeStateV3.model_rebuild()


class CommandReservedPayloadV1(_StoredModel):
    client_command_id: str
    method: str
    params: dict[str, JsonValue]
    params_sha256: Sha256

    @model_validator(mode="after")
    def validate_params_digest(self) -> Self:
        if self.params_sha256 != sha256_json(self.params):
            raise ValueError("command parameter digest does not match its content")
        return self


class TransitionShapeV1(_StoredModel):
    """What a transition decided, without what it decided it about.

    A digest says only that replay diverged. This says where: the directives the
    Core issued, the observations it emitted, and the turn it left behind. Every
    field is drawn from a closed protocol vocabulary, so a diagnostic may name
    them in a log or an error while conversation content stays in the store.
    """

    turn: str
    next: Literal["none", "actions"]
    directives: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)


def transition_shape(transition: RustSessionTransition) -> TransitionShapeV1:
    directives: list[str] = []
    if isinstance(transition.next, RustActionsNextAction):
        for directive in transition.next.directives:
            match directive:
                case RustDispatchActionDirective(action=action):
                    directives.append(f"dispatch:{action.type}")
                case RustRefreshActionDirective(action=action):
                    directives.append(f"refresh:{action.type}")
                case RustKeepActionDirective():
                    directives.append("keep")
    return TransitionShapeV1(
        turn=transition.turn.status,
        next="actions"
        if isinstance(transition.next, RustActionsNextAction)
        else "none",
        directives=directives,
        observations=[observation.type for observation in transition.observations],
    )


def shape_divergence(
    recorded: TransitionShapeV1 | None, replayed: TransitionShapeV1
) -> dict[str, JsonValue]:
    """Name the parts of the decision replay did not reproduce.

    A journal written before shapes were recorded has nothing to compare, which
    the report says rather than guesses at.
    """
    if recorded is None:
        return {"recorded": None, "replayed": replayed.model_dump(mode="json")}
    differences: dict[str, JsonValue] = {}
    for field in ("turn", "next", "directives", "observations"):
        expected = getattr(recorded, field)
        actual = getattr(replayed, field)
        if expected != actual:
            differences[field] = {"recorded": expected, "replayed": actual}
    return differences


class CoreInputPayloadV1(_StoredModel):
    input: RustHarnessInput
    transition_sha256: Sha256
    replay_transition_sha256: Sha256 | None = None
    checkpoint_capabilities: RustHarnessCapabilitySet | None = None
    checkpoint_settings: RustHarnessSettings | None = None
    """Settings in force when this generation's first input was applied.

    Recorded beside the capability baseline because the Core consults both to
    decide a transition, and a restoring process computes both from whatever the
    current build ships rather than from what this journal was written under.
    """

    checkpoint_plugins: list[RustPluginContextDefinition] | None = None
    """Plugin contexts in force when this generation's first input was applied."""

    transition_shape: TransitionShapeV1 | None = None
    """The decision this input produced, for diagnosing a replay that diverges."""


class ActionIntentPayloadV1(_StoredModel):
    action_id: str
    kind: Literal[
        "completion", "tool", "hook", "process", "callback", "child", "filesystem"
    ]
    request_sha256: Sha256
    request: JsonValue
    recovery_mode: Literal[
        "reconnect_or_fail", "redeliver", "idempotent_retry", "reconcile", "fail"
    ]
    lease_id: str | None = None

    @model_validator(mode="after")
    def validate_request_digest(self) -> Self:
        if self.request_sha256 != sha256_json(self.request):
            raise ValueError("action request digest does not match its content")
        return self


class ProcessOperationDispatchedPayloadV1(_StoredModel):
    action_id: str
    manager_instance_id: str
    request_sha256: Sha256
    prepared_start: PreparedProcessStartV1 | None = None


class ProcessStateChangedPayloadV1(_StoredModel):
    process: ManagedProcessV1


class ProcessNotificationSubmittedPayloadV1(_StoredModel):
    process_id: str
    notification_id: str


class ActionResultPayloadV1(_StoredModel):
    action_id: str
    state: Literal["succeeded", "failed"]
    result: JsonValue


class CallbackRegisteredPayloadV1(_StoredModel):
    callback_id: str
    kind: str
    routing: dict[str, JsonValue]


class CallbackResolvedPayloadV1(_StoredModel):
    callback_id: str
    state: Literal["resolved", "failed"]
    result: JsonValue


class ReceiptSucceededPayloadV1(_StoredModel):
    client_command_id: str
    response: JsonValue


class ReceiptFailedPayloadV1(_StoredModel):
    client_command_id: str
    reason: str


class ProjectionAdvancedPayloadV1(_StoredModel):
    watermark: int = Field(ge=0)
    snapshot: PublicSessionState


class ProjectionDeltaPayloadV1(_StoredModel):
    watermark: int = Field(ge=0)
    delta: ProjectionDelta


class _JournalRecordBase(_StoredModel):
    recovery_journal_record_version: Literal[1] = 1
    sequence: int = Field(ge=1)
    previous_record_sha256: Sha256 | None
    record_sha256: Sha256


class CommandReservedRecordV1(_JournalRecordBase):
    type: Literal["command_reserved"] = "command_reserved"
    payload: CommandReservedPayloadV1


class CoreInputRecordV1(_JournalRecordBase):
    type: Literal["core_input"] = "core_input"
    payload: CoreInputPayloadV1


class ActionIntentRecordV1(_JournalRecordBase):
    type: Literal["action_intent"] = "action_intent"
    payload: ActionIntentPayloadV1


class ActionResultRecordV1(_JournalRecordBase):
    type: Literal["action_result"] = "action_result"
    payload: ActionResultPayloadV1


class ProcessOperationDispatchedRecordV1(_JournalRecordBase):
    type: Literal["process_operation_dispatched"] = "process_operation_dispatched"
    payload: ProcessOperationDispatchedPayloadV1


class ProcessStateChangedRecordV1(_JournalRecordBase):
    type: Literal["process_state_changed"] = "process_state_changed"
    payload: ProcessStateChangedPayloadV1


class ProcessNotificationSubmittedRecordV1(_JournalRecordBase):
    type: Literal["process_notification_submitted"] = "process_notification_submitted"
    payload: ProcessNotificationSubmittedPayloadV1


class CallbackRegisteredRecordV1(_JournalRecordBase):
    type: Literal["callback_registered"] = "callback_registered"
    payload: CallbackRegisteredPayloadV1


class CallbackResolvedRecordV1(_JournalRecordBase):
    type: Literal["callback_resolved"] = "callback_resolved"
    payload: CallbackResolvedPayloadV1


class ReceiptSucceededRecordV1(_JournalRecordBase):
    type: Literal["receipt_succeeded"] = "receipt_succeeded"
    payload: ReceiptSucceededPayloadV1


class ReceiptFailedRecordV1(_JournalRecordBase):
    type: Literal["receipt_failed"] = "receipt_failed"
    payload: ReceiptFailedPayloadV1


class ProjectionAdvancedRecordV1(_JournalRecordBase):
    type: Literal["projection_advanced"] = "projection_advanced"
    payload: ProjectionAdvancedPayloadV1


class ProjectionDeltaRecordV1(_JournalRecordBase):
    type: Literal["projection_delta"] = "projection_delta"
    payload: ProjectionDeltaPayloadV1


JournalRecordV1 = Annotated[
    CommandReservedRecordV1
    | CoreInputRecordV1
    | ActionIntentRecordV1
    | ActionResultRecordV1
    | ProcessOperationDispatchedRecordV1
    | ProcessStateChangedRecordV1
    | ProcessNotificationSubmittedRecordV1
    | CallbackRegisteredRecordV1
    | CallbackResolvedRecordV1
    | ReceiptSucceededRecordV1
    | ReceiptFailedRecordV1
    | ProjectionAdvancedRecordV1
    | ProjectionDeltaRecordV1,
    Field(discriminator="type"),
]

_JOURNAL_RECORD_ADAPTER = TypeAdapter(JournalRecordV1)
_PAYLOAD_TYPES: dict[str, type[_StoredModel]] = {
    "command_reserved": CommandReservedPayloadV1,
    "core_input": CoreInputPayloadV1,
    "action_intent": ActionIntentPayloadV1,
    "action_result": ActionResultPayloadV1,
    "process_operation_dispatched": ProcessOperationDispatchedPayloadV1,
    "process_state_changed": ProcessStateChangedPayloadV1,
    "process_notification_submitted": ProcessNotificationSubmittedPayloadV1,
    "callback_registered": CallbackRegisteredPayloadV1,
    "callback_resolved": CallbackResolvedPayloadV1,
    "receipt_succeeded": ReceiptSucceededPayloadV1,
    "receipt_failed": ReceiptFailedPayloadV1,
    "projection_advanced": ProjectionAdvancedPayloadV1,
    "projection_delta": ProjectionDeltaPayloadV1,
}


@dataclass(frozen=True, slots=True)
class CommandReservation:
    newly_reserved: bool
    completed: bool = False
    response: JsonValue = None


@dataclass(frozen=True, slots=True)
class PendingInternalCommand:
    client_command_id: str
    method: str
    params: dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class OrphanedInternalCommand:
    """A reservation whose command no reader can reconstruct or resume.

    Its caller is long gone and its content is unavailable, so the only way out
    is to settle the receipt as failed: leaving it reserved holds the store off
    quiescence for the rest of the session's life.
    """

    client_command_id: str
    reason: str


def restore_core_from_checkpoint(
    config: RustHarnessConfig,
    checkpoint: dict[str, JsonValue],
    *,
    resume_input_id: int = 0,
) -> HarnessSession:
    """Build a Core from a configuration and a checkpoint, reading nothing from disk.

    The checkpoint holds no delivery state, so `resume_input_id` is the last input ID
    the Runtime durably accepted for this session; zero starts a fresh sequence. The
    Core still opens with a full model-input replacement either way.
    """
    return HarnessSession.restore(
        config.model_dump_json(by_alias=True, exclude_none=False),
        canonical_json(checkpoint).decode(),
        resume_input_id,
    )


@dataclass(frozen=True, slots=True)
class StoredSession:
    manifest: GenerationManifestV1
    checkpoint: dict[str, JsonValue]
    runtime_state: RuntimeStateV3
    projection_state: ProjectionStateV1
    journal: tuple[JournalRecordV1, ...]
    published_runtime_state: RuntimeStateV3 | None = None
    """The generation's own Runtime state, before its journal was folded in.

    ``runtime_state`` is the effective state, which is what nearly every reader
    wants. Folding a prefix of the journal instead needs the state the fold
    started from, which only this records.
    """

    published_projection_state: ProjectionStateV1 | None = None
    """The generation's own projection, before its journal was folded in."""

    @property
    def interop_export(self) -> CommittedHistoryV1 | None:
        """The cross-harness view of this generation, for the rare reader that wants one.

        Derived on demand rather than held: only a fork or a migration asks for
        it, while every publication would otherwise have to write it. Deriving it
        costs a pass over the transcript, so callers that use it twice should
        hold onto the result.
        """
        if self.manifest.execution_state != "quiescent":
            return None
        return committed_history_from_checkpoint(
            UnifiedInteropSourceV1(
                session_id=self.manifest.session_id,
                generation=self.manifest.generation,
                snapshot_sequence=self.manifest.snapshot_sequence,
            ),
            self.checkpoint,
        )

    def restore_core(self, config: RustHarnessConfig) -> HarnessSession:
        core, _transitions = self.restore_core_with_transitions(config)
        return core

    @property
    def _checkpoint_payload(self) -> CoreInputPayloadV1 | None:
        """The payload carrying this generation's configuration baseline."""
        return next(
            (
                record.payload
                for record in self.journal
                if isinstance(record, CoreInputRecordV1)
            ),
            None,
        )

    @property
    def checkpoint_capabilities(self) -> RustHarnessCapabilitySet | None:
        """Capabilities recorded beside this generation's first replayed input.

        A generation folded from an empty journal has no such input, and answers
        from the set stamped on the runtime state instead.
        """
        payload = self._checkpoint_payload
        if payload is None:
            return self.runtime_state.core_capabilities
        return payload.checkpoint_capabilities

    @property
    def checkpoint_settings(self) -> RustHarnessSettings | None:
        """Settings recorded beside this generation's first replayed input.

        Falls back to the generation's own stamp for the same reason the
        capability baseline does: a fold leaves no input to carry it.
        """
        payload = self._checkpoint_payload
        if payload is None:
            return self.runtime_state.core_settings
        return payload.checkpoint_settings

    @property
    def checkpoint_plugins(self) -> list[RustPluginContextDefinition] | None:
        """Plugin contexts recorded beside this generation's first replayed input."""
        payload = self._checkpoint_payload
        if payload is None:
            return self.runtime_state.core_plugins
        return payload.checkpoint_plugins

    def replay_config(self, config: RustHarnessConfig) -> RustHarnessConfig:
        """Restate the configuration this generation's journal was written under.

        The Core decides a transition from its state and its configuration, so a
        replay that supplies the restoring process's configuration asks it to
        reach a recorded decision from inputs that never produced it. Each
        dimension the journal pinned is restored here; a dimension no baseline
        names stays as the caller supplied it, which is all an older journal can
        offer.
        """
        pinned: dict[str, Any] = {}
        if self.checkpoint_capabilities is not None:
            pinned["capabilities"] = self.checkpoint_capabilities
        if self.checkpoint_settings is not None:
            pinned["settings"] = self.checkpoint_settings
        if self.checkpoint_plugins is not None:
            pinned["plugins"] = list(self.checkpoint_plugins)
        return config.model_copy(update=pinned, deep=True) if pinned else config

    def replayed_capabilities(
        self, fallback: RustHarnessCapabilitySet
    ) -> RustHarnessCapabilitySet:
        """Return the capability set in force after this generation's journal."""
        capabilities = self.checkpoint_capabilities or fallback
        for record in self.journal:
            if not isinstance(record, CoreInputRecordV1):
                continue
            command = record.payload.input.command
            if not isinstance(command, RustReconfigureEvent):
                continue
            for change in command.changes:
                if isinstance(change, RustCapabilitiesChange):
                    capabilities = change.value
        return capabilities

    def replayed_config(self, config: RustHarnessConfig) -> RustHarnessConfig:
        """Return the configuration in force after this generation's journal.

        The pinned baseline states where the journal began; its reconfigure
        inputs state every change the Core accepted since. A Runtime restored
        from this holds the configuration its replayed Core is actually running.
        """
        replayed = self.replay_config(config)
        updates: dict[str, Any] = {}
        for record in self.journal:
            if not isinstance(record, CoreInputRecordV1):
                continue
            command = record.payload.input.command
            if not isinstance(command, RustReconfigureEvent):
                continue
            for change in command.changes:
                match change:
                    case RustCapabilitiesChange(value=value):
                        updates["capabilities"] = value
                    case RustSettingsChange(value=value):
                        updates["settings"] = value
                    case RustPluginsChange(value=value):
                        updates["plugins"] = list(value)
        return replayed.model_copy(update=updates, deep=True) if updates else replayed

    def truncated_at(self, sequence: int) -> StoredSession:
        """This generation with every record from ``sequence`` onward dropped.

        Replay is a fold, so a prefix of the journal is itself a consistent
        state: the one the session held just before the record that no longer
        replays. Everything after it stays on disk under the previous
        generation until the store collects it.
        """
        prefix = tuple(record for record in self.journal if record.sequence < sequence)
        base_runtime = self.published_runtime_state
        base_projection = self.published_projection_state
        if base_runtime is None or base_projection is None:
            raise HarnessInvalidSessionStoreError(
                self.manifest.session_id,
                "generation was read without its published state",
            )
        runtime_state, projection_state = _apply_journal(
            base_runtime, base_projection, prefix
        )
        return StoredSession(
            manifest=self.manifest,
            checkpoint=self.checkpoint,
            runtime_state=runtime_state,
            projection_state=projection_state,
            journal=prefix,
            published_runtime_state=base_runtime,
            published_projection_state=base_projection,
        )

    def restore_core_with_transitions(
        self, config: RustHarnessConfig
    ) -> tuple[HarnessSession, tuple[RustSessionTransition, ...]]:
        checkpoint_capabilities = self.checkpoint_capabilities
        replay_config = self.replay_config(config)
        core = restore_core_from_checkpoint(
            replay_config,
            self.checkpoint,
            resume_input_id=self.runtime_state.core_last_input_id,
        )
        transitions: list[RustSessionTransition] = []
        for record in self.journal:
            if not isinstance(record, CoreInputRecordV1):
                continue
            result = parse_apply_result(
                core.apply(
                    canonical_json(
                        record.payload.input.model_dump(mode="json", by_alias=True)
                    ).decode()
                )
            )
            if isinstance(
                result, RustAcceptedApplyResult
            ) and _transition_matches_recorded_digest(
                result.transition, record.payload, self.runtime_state.actions
            ):
                transitions.append(result.transition)
                continue
            divergence: dict[str, JsonValue] = (
                shape_divergence(
                    record.payload.transition_shape, transition_shape(result.transition)
                )
                if isinstance(result, RustAcceptedApplyResult)
                else {"rejected": result.rejection.code}
            )
            core.close()
            error = HarnessReplayDivergenceError(
                self.manifest.session_id, record.sequence
            )
            if error.details is not None:
                # Without this the only report is "diverged at N", and naming the
                # differing decision has so far meant re-recording the run by hand.
                error.details["divergence"] = divergence
                if checkpoint_capabilities is None:
                    error.details["capabilities_required"] = True
            raise error
        return core, tuple(transitions)


class SessionLease:
    # The on-disk lease format (seed byte, {id}.lock.json, .registry) is
    # shared with the twin in vibe/core/session/session_lease.py; change
    # both.

    def __init__(self, root: Path, session_id: str) -> None:
        _validate_session_id(session_id)
        self._path = root / "active" / f"{session_id}.lock"
        self._diagnostic_path = root / "active" / f"{session_id}.lock.json"
        self._session_id = session_id
        self._file: Any | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def diagnostic_path(self) -> Path:
        return self._diagnostic_path

    @property
    def held(self) -> bool:
        return self._file is not None

    def acquire(self) -> Self:
        if self._file is not None:
            raise RuntimeError("session lease is already acquired")
        _reject_symlink_components(self._path.parents[1], self._path.parent)
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with _lease_directory_lock(self._path.parent):
            self._path.touch(mode=0o600, exist_ok=True)
            file = self._path.open("a+b")
            try:
                _acquire_file_lock(file)
            except BlockingIOError as exc:
                file.close()
                raise HarnessSessionBusyError(self._session_id) from exc
            try:
                diagnostic = {
                    "lease_version": 1,
                    "session_id": self._session_id,
                    "process_id": os.getpid(),
                    "acquired_at": _timestamp(),
                }
                # The diagnostic sits beside the lock, not inside it: Windows
                # byte-range locks make the lock file unreadable while held.
                # Readers must tolerate a torn read during acquire.
                self._diagnostic_path.touch(mode=0o600, exist_ok=True)
                with self._diagnostic_path.open("wb") as diagnostic_file:
                    diagnostic_file.write(canonical_json(diagnostic) + b"\n")
                    diagnostic_file.flush()
                    os.fsync(diagnostic_file.fileno())
            except BaseException:
                # A lease that cannot publish its diagnostic is not acquired:
                # undo the lock so the caller can retry. BaseException, not
                # OSError: the guarded block also runs non-OS code (_timestamp,
                # getpid, canonical_json), and a Ctrl-C there must roll back too —
                # _lease_directory_lock uses the same width for that reason.
                # The unlock can itself fail on Windows; the close must still
                # run — closing the descriptor releases the OS-level lock on
                # every platform, so the lease cannot stay stuck.
                try:
                    _release_file_lock(file)
                finally:
                    file.close()
                with suppress(OSError):
                    self._path.unlink(missing_ok=True)
                raise
            self._file = file
        return self

    def release(self) -> None:
        if self._file is None:
            return
        file = self._file
        self._file = None
        with _lease_directory_lock(self._path.parent):
            try:
                _release_file_lock(file)
            finally:
                file.close()
            # An out-of-band reader can hold either file open on Windows and
            # make unlink fail; leftovers are harmless (the next acquire
            # reuses the lock file and overwrites the diagnostic).
            with suppress(OSError):
                self._path.unlink(missing_ok=True)
            with suppress(OSError):
                self._diagnostic_path.unlink(missing_ok=True)

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(self, *_exc: object) -> None:
        self.release()


def session_is_live(root: Path, session_id: str) -> bool:
    """Best-effort probe: does a live process currently hold this session's lease?

    Lets a passive reader tell a crashed session (whose file lock the OS
    dropped on exit) from one that another app-server or CLI instance is
    driving. Takes the lease non-blockingly and never removes the lock file —
    at most it seeds an empty one — so it cannot disturb the owner; a missing
    or unheld lock reports the session not live.
    """
    _validate_session_id(session_id)
    directory = root / "active"
    path = directory / f"{session_id}.lock"
    try:
        with _lease_directory_lock(directory), path.open("r+b") as file:
            try:
                _acquire_file_lock(file, blocking=False)
            except BlockingIOError:
                return True
            _release_file_lock(file)
            return False
    except (BlockingIOError, PermissionError):
        # Registry lock held past its retry budget, or lease-file access
        # denied. Report live — the fail-safe direction: callers only skip
        # idle cleanup for live sessions.
        return True
    except OSError:
        return False


@dataclass(frozen=True, slots=True)
class _GenerationCacheKey:
    """On-disk identity of a loaded generation.

    ``CURRENT`` is a couple of hundred bytes and the journal check is a single
    ``lstat``, so revalidating a cached load costs a handful of syscalls instead
    of reparsing and revalidating megabytes of snapshot and journal JSON.
    """

    current: bytes
    journal_path: Path
    journal_size: int
    journal_mtime_ns: int


@dataclass(frozen=True, slots=True)
class _CachedGeneration:
    key: _GenerationCacheKey
    session: StoredSession


@dataclass(frozen=True, slots=True)
class _Chunk:
    items: list[JsonValue]
    sha256: str
    sealed: bool
    body: bytes | None
    """Encoded contents, or none when the chunk was reused and never re-encoded."""


@dataclass(frozen=True, slots=True)
class _ChunkPlan:
    """How one publication split a transcript, kept so the next can reuse it."""

    chunks: tuple[_Chunk, ...]

    @property
    def digests(self) -> tuple[str, ...]:
        return tuple(chunk.sha256 for chunk in self.chunks)


@dataclass(frozen=True, slots=True)
class ChunkPublicationStats:
    """What one publication had to add to the chunk pool.

    ``written`` staying near zero as a session grows is the whole point of the
    pool, so logging it is what would catch a workload whose transcript is
    rewritten rather than appended to.
    """

    written: int = 0
    reused: int = 0
    bytes_written: int = 0
    sealed: int = 0

    def __add__(self, other: ChunkPublicationStats) -> ChunkPublicationStats:
        return ChunkPublicationStats(
            written=self.written + other.written,
            reused=self.reused + other.reused,
            bytes_written=self.bytes_written + other.bytes_written,
            sealed=self.sealed + other.sealed,
        )


class _ChunkCache:
    """Least-recently-used pool of verified chunk bodies.

    Hands back bytes rather than parsed items so callers cannot alias each
    other's transcripts through the cache.
    """

    def __init__(self, budget: int = _CHUNK_CACHE_BYTES) -> None:
        self._budget = budget
        self._bodies: OrderedDict[str, bytes] = OrderedDict()
        self._size = 0

    def read(self, chunk_root: Path, digest: str) -> bytes:
        cached = self._bodies.get(digest)
        if cached is not None:
            self._bodies.move_to_end(digest)
            return cached
        body = _read_document_body(chunk_root / f"{digest}.json")
        if _sha256(body) != digest:
            raise ValueError(f"stored chunk digest mismatch: {digest}")
        self._admit(digest, body)
        return body

    def _admit(self, digest: str, body: bytes) -> None:
        if len(body) > self._budget:
            return
        self._bodies[digest] = body
        self._size += len(body)
        while self._size > self._budget:
            self._size -= len(self._bodies.popitem(last=False)[1])


class UnifiedSessionStore:  # noqa: PLR0904 - cohesive session store surface
    def __init__(self, root: Path, session_id: str) -> None:
        _validate_session_id(session_id)
        self.root = root
        self.session_id = session_id
        self.session_root = root / "unified" / session_id
        self._journal_capacity_lock = RLock()
        self._journal_capacity_reservations: dict[str, int] = {}
        self._journal_reservation_owner: ContextVar[str | None] = ContextVar(
            f"journal-reservation-{session_id}", default=None
        )
        self._cache_lock = RLock()
        self._cache: _CachedGeneration | None = None
        # Purely an optimization: every reuse it enables is verified against the
        # transcript being published, so a stale or empty plan costs a re-chunk
        # and never correctness.
        self._chunk_plans: dict[str, _ChunkPlan] = {}
        self._chunk_cache = _ChunkCache()
        self.last_publication = ChunkPublicationStats()

    @property
    def exists(self) -> bool:
        _reject_symlink_components(self.root, self.session_root)
        return (self.session_root / "CURRENT").is_file()

    def delete(self) -> None:
        _reject_symlink_components(self.root, self.session_root)
        self._invalidate_cache()
        if self.session_root.exists():
            shutil.rmtree(self.session_root)

    def write_generation(  # noqa: PLR0914, PLR0915 - one cohesive generation write
        self,
        *,
        checkpoint: dict[str, JsonValue],
        runtime_state: RuntimeStateV3,
        projection_state: ProjectionStateV1,
        core_action_ids: frozenset[str] | None = None,
    ) -> GenerationManifestV1:
        _reject_symlink_components(self.root, self.session_root)
        sequence = runtime_state.snapshot_sequence
        if runtime_state.session_id != self.session_id:
            raise ValueError("runtime state belongs to another session")
        if projection_state.session_id != self.session_id:
            raise ValueError("projection state belongs to another session")
        if projection_state.snapshot_sequence != sequence:
            raise ValueError("runtime and projection snapshot sequences differ")
        if checkpoint.get("checkpoint_version") != 1:
            raise ValueError("unsupported Core checkpoint version")
        if runtime_state.quiescent:
            # The interop export is derived on read, so a checkpoint with nothing
            # to derive from would strand a fork long after the publication that
            # caused it. Checking the shape here keeps that failure at the write
            # that introduced it, without walking the transcript every turn.
            checkpoint_messages(checkpoint)
        runtime_state = _discard_provider_operation_results(runtime_state)
        if core_action_ids is not None:
            runtime_state = _prune_settled_completions(runtime_state, core_action_ids)

        generation_root = self.session_root / "generations"
        journal_root = self.session_root / "journal"
        _reject_symlink_components(self.session_root, generation_root)
        _reject_symlink_components(self.session_root, journal_root)
        generation = _next_generation(generation_root, sequence)
        final_dir = generation_root / generation
        self._invalidate_cache()
        self.session_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        generation_root.mkdir(mode=0o700, exist_ok=True)
        journal_root.mkdir(mode=0o700, exist_ok=True)
        journal_path = journal_root / f"{sequence + 1:016d}.jsonl"
        _create_empty_file(journal_path)

        chunk_root = self.session_root / _CHUNKS_DIRNAME
        _reject_symlink_components(self.session_root, chunk_root)
        chunk_root.mkdir(mode=0o700, exist_ok=True)

        staging = generation_root / f".staging-{generation}-{secrets.token_hex(8)}"
        staging.mkdir(mode=0o700)
        try:
            checkpoint_envelope, checkpoint_transcript = _detach_transcript(
                checkpoint, _CHECKPOINT_MESSAGES_PATH
            )
            projection_envelope, projection_transcript = _detach_projection_history(
                projection_state
            )
            checkpoint_plan = self._plan_transcript("checkpoint", checkpoint_transcript)
            projection_plan = self._plan_transcript("projection", projection_transcript)
            chunk_stats = sum(
                (
                    _write_chunks(chunk_root, plan)
                    for plan in (checkpoint_plan, projection_plan)
                ),
                ChunkPublicationStats(),
            )
            _fsync_directory(chunk_root)

            checkpoint_record = _write_generation_record(
                staging,
                "checkpoint.json",
                checkpoint_envelope,
                checkpoint_plan.digests if checkpoint_plan is not None else None,
            )
            runtime_record = _write_generation_record(
                staging,
                "runtime-state.json",
                runtime_state.model_dump(mode="json", by_alias=True),
            )
            projection_record = _write_generation_record(
                staging,
                "projection-state.json",
                projection_envelope,
                projection_plan.digests if projection_plan is not None else None,
            )
            manifest = GenerationManifestV1(
                session_id=self.session_id,
                generation=generation,
                created_at=_timestamp(),
                snapshot_sequence=sequence,
                execution_state=(
                    "quiescent" if runtime_state.quiescent else "recoverable"
                ),
                checkpoint=StoredCheckpointV1(
                    path=checkpoint_record.path,
                    sha256=checkpoint_record.sha256,
                    chunks=checkpoint_record.chunks,
                ),
                runtime_state=runtime_record,
                projection_state=projection_record,
                recovery_journal_segment=RecoveryJournalSegmentV1(
                    path=f"journal/{sequence + 1:016d}.jsonl",
                    first_sequence=sequence + 1,
                ),
            )
            # An envelope whose transcript was detached but whose descriptor names
            # no chunks reads back as a genuinely empty transcript, so a descriptor
            # rebuilt without its chunk list would lose the conversation silently.
            for descriptor, plan in (
                (manifest.checkpoint, checkpoint_plan),
                (manifest.projection_state, projection_plan),
            ):
                if (descriptor.chunks is None) != (plan is None):
                    raise ValueError("a detached transcript is missing its chunk list")
            manifest_bytes = _write_document(
                staging / "manifest.json",
                manifest.model_dump(mode="json", by_alias=True),
            )
            _fsync_directory(staging)
            _durable_replace(staging, final_dir)
            _fsync_directory(generation_root)
            current = CurrentPointerV1(
                session_id=self.session_id,
                store_format_minor=STORE_FORMAT_MINOR,
                generation=generation,
                snapshot_sequence=sequence,
                manifest_sha256=_sha256(manifest_bytes),
            )
            current_bytes = _replace_document(
                self.session_root / "CURRENT",
                current.model_dump(mode="json", by_alias=True),
            )
        except BaseException:
            if staging.exists():
                for child in staging.iterdir():
                    child.unlink()
                staging.rmdir()
            raise
        # Only now that the manifest naming them is durable: a plan recorded
        # ahead of the commit could let the next publication reuse a chunk this
        # one never published.
        for key, plan in (
            ("checkpoint", checkpoint_plan),
            ("projection", projection_plan),
        ):
            if plan is not None:
                self._chunk_plans[key] = plan
        self.last_publication = chunk_stats
        _discard_superseded(generation_root, journal_root)
        if chunk_stats.sealed:
            # Sweeping every publication would cost the pool's size per turn.
            # Sealing happens once per chunk of transcript growth, and between
            # sweeps the garbage is one superseded open tail per publication.
            _collect_chunks(self.session_root, generation_root)
        self._prime_cache(
            manifest=manifest,
            checkpoint=checkpoint,
            runtime_state=runtime_state,
            projection_state=projection_state,
            generation_dir=final_dir,
            manifest_sha256=current.manifest_sha256,
            current=current_bytes,
            journal_path=journal_path,
        )
        return manifest

    def _plan_transcript(
        self, key: str, transcript: list[JsonValue] | None
    ) -> _ChunkPlan | None:
        if transcript is None:
            return None
        return _plan_chunks(transcript, self._chunk_plans.get(key))

    def load(self) -> StoredSession:
        try:
            with self._cache_lock:
                cached = self._cache
                if (
                    cached is not None
                    and self._read_cache_key(cached.key.journal_path) == cached.key
                ):
                    return cached.session
                self._cache = None
                session, key = self._load_current()
                self._cache = _CachedGeneration(key=key, session=session)
                return session
        except HarnessInvalidSessionStoreError:
            raise
        except Exception as exc:
            raise HarnessInvalidSessionStoreError(
                self.session_id, f"Invalid Unified session store: {exc}"
            ) from exc

    def _load_current(self) -> tuple[StoredSession, _GenerationCacheKey]:
        pointer = self._current_bytes()
        try:
            return self._load()
        except Exception:
            # Only a moved pointer can mean a collection took the generation out
            # from under this read; anything else is a genuinely broken store.
            if pointer is None or self._current_bytes() == pointer:
                raise
            return self._load()

    def _current_bytes(self) -> bytes | None:
        try:
            return (self.session_root / "CURRENT").read_bytes()
        except OSError:
            return None

    def _invalidate_cache(self) -> None:
        with self._cache_lock:
            self._cache = None

    def _prime_cache(
        self,
        *,
        manifest: GenerationManifestV1,
        checkpoint: dict[str, JsonValue],
        runtime_state: RuntimeStateV3,
        projection_state: ProjectionStateV1,
        generation_dir: Path,
        manifest_sha256: str,
        current: bytes,
        journal_path: Path,
    ) -> None:
        # Reading the documents back to digest them keeps what the post-publication
        # `load` did for the writer -- proving the bytes landed -- without the parse.
        try:
            _verify_published_documents(generation_dir, manifest, manifest_sha256)
            # `load` returns what it read through `_apply_journal`, which sorts every
            # collection. A caller that hands over an unsorted state gets bytes no
            # reader would accept, so leave it to the load to say so.
            settled = _apply_journal(runtime_state, projection_state, ()) == (
                runtime_state,
                projection_state,
            )
            key = self._read_cache_key(journal_path)
        except (OSError, ValueError):
            # Anything found here, `load` finds too, and reports it the usual way.
            return
        if (
            not settled
            or key is None
            or key.current != current
            or key.journal_size != 0
        ):
            return
        with self._cache_lock:
            self._cache = _CachedGeneration(
                key=key,
                session=StoredSession(
                    manifest=manifest,
                    checkpoint=checkpoint,
                    runtime_state=runtime_state,
                    projection_state=projection_state,
                    journal=(),
                    published_runtime_state=runtime_state,
                    published_projection_state=projection_state,
                ),
            )

    def _read_cache_key(self, journal_path: Path) -> _GenerationCacheKey | None:
        # A hit skips the checks a full load runs, so a store directory swapped for a
        # link after the cache was filled would otherwise go unnoticed.
        _reject_symlink_components(self.root, self.session_root)
        _reject_symlink(self.session_root / "generations")
        _reject_symlink_components(self.session_root, journal_path)
        try:
            current = (self.session_root / "CURRENT").read_bytes()
            # lstat so a journal swapped for a symlink misses the cache and is
            # rejected by the full load rather than silently trusted.
            stat = os.lstat(journal_path)
        except OSError:
            return None
        return _GenerationCacheKey(
            current=current,
            journal_path=journal_path,
            journal_size=stat.st_size,
            journal_mtime_ns=stat.st_mtime_ns,
        )

    def _load(self) -> tuple[StoredSession, _GenerationCacheKey]:  # noqa: PLR0914 - one cohesive generation load
        _reject_symlink_components(self.root, self.session_root)
        current_path = self.session_root / "CURRENT"
        current_value, current_canonical = _read_document_bytes(current_path)
        newer_minor = CurrentPointerV1.newer_minor(current_value)
        if newer_minor is not None:
            raise HarnessStoreRequiresNewerReaderError(self.session_id, newer_minor)
        current = CurrentPointerV1.model_validate(current_value)
        if current.session_id != self.session_id:
            raise ValueError("CURRENT names another session")
        generation_dir = self.session_root / "generations" / current.generation
        _reject_symlink_components(self.session_root, generation_dir)
        manifest_path = generation_dir / "manifest.json"
        manifest_value, manifest_bytes = _read_document_bytes(manifest_path)
        if _sha256(manifest_bytes) != current.manifest_sha256:
            raise ValueError("manifest digest mismatch")
        manifest = GenerationManifestV1.model_validate(manifest_value)
        if (
            manifest.session_id != self.session_id
            or manifest.generation != current.generation
            or manifest.snapshot_sequence != current.snapshot_sequence
        ):
            raise ValueError("CURRENT and manifest disagree")

        chunk_root = self.session_root / _CHUNKS_DIRNAME
        checkpoint = cast(
            dict[str, JsonValue],
            _read_referenced_document(generation_dir, manifest.checkpoint),
        )
        if (
            checkpoint.get("checkpoint_version")
            != manifest.checkpoint.checkpoint_version
        ):
            raise ValueError("Core checkpoint version mismatch")
        if manifest.checkpoint.chunks is not None:
            _attach_transcript(
                checkpoint,
                _CHECKPOINT_MESSAGES_PATH,
                _read_chunked_transcript(
                    chunk_root, manifest.checkpoint.chunks, self._chunk_cache
                ),
            )
        runtime_state_value = _read_referenced_document(
            generation_dir, manifest.runtime_state
        )
        projection_value = _read_referenced_document(
            generation_dir, manifest.projection_state
        )
        if manifest.projection_state.chunks is not None:
            _attach_transcript(
                projection_value,
                _PROJECTION_HISTORY_PATH,
                _read_chunked_transcript(
                    chunk_root, manifest.projection_state.chunks, self._chunk_cache
                ),
            )
        projection_state = ProjectionStateV1.model_validate(projection_value)
        runtime_state = _load_runtime_state(runtime_state_value, projection_state)
        # No read of interop-export.json: a generation written before minor 3 still
        # carries one, and `StoredSession.interop_export` derives the same value from
        # the checkpoint it was written from.
        if runtime_state.session_id != self.session_id:
            raise ValueError("runtime state belongs to another session")
        if projection_state.session_id != self.session_id:
            raise ValueError("projection state belongs to another session")
        if runtime_state.snapshot_sequence != manifest.snapshot_sequence:
            raise ValueError("runtime state sequence does not match manifest")
        if projection_state.snapshot_sequence != manifest.snapshot_sequence:
            raise ValueError("projection state sequence does not match manifest")
        if runtime_state.quiescent != (manifest.execution_state == "quiescent"):
            raise ValueError("execution state does not match Runtime recovery state")

        journal_path = self.session_root / manifest.recovery_journal_segment.path
        _reject_symlink_components(self.session_root, journal_path)
        # Sample the journal before reading it: a concurrent append then makes the
        # key look stale and forces a reload, whereas sampling afterwards could
        # record a size that already covers records this load never saw.
        journal_stat = os.lstat(journal_path)
        journal = _read_journal(
            journal_path, manifest.recovery_journal_segment.first_sequence
        )
        effective_runtime, effective_projection = _apply_journal(
            runtime_state, projection_state, journal
        )
        session = StoredSession(
            manifest=manifest,
            checkpoint=checkpoint,
            runtime_state=effective_runtime,
            projection_state=effective_projection,
            journal=journal,
            published_runtime_state=runtime_state,
            published_projection_state=projection_state,
        )
        key = _GenerationCacheKey(
            current=current_canonical + b"\n",
            journal_path=journal_path,
            journal_size=journal_stat.st_size,
            journal_mtime_ns=journal_stat.st_mtime_ns,
        )
        return session, key

    def repair_diverged_generation(
        self, config: RustHarnessConfig, sequence: int
    ) -> GenerationManifestV1:
        """Republish the newest state whose journal still replays.

        A divergent record makes every record after it unreachable: the Core
        state they assume can no longer be rebuilt. Publishing the verified
        prefix trades the unreachable tail for a session that opens again.
        Whatever the prefix left in flight stays in flight, so the republished
        generation is recoverable exactly as a crash mid-turn leaves one, and
        recovery settles it on the usual terms.

        The discarded records are kept aside, because a store that cannot be
        replayed is the only evidence of why it could not be. They are set aside
        only once the repaired generation is published: until then the old
        generation is still the session, and it has to stay loadable.
        """
        stored = self.load()
        truncated = stored.truncated_at(sequence)
        core, transitions = truncated.restore_core_with_transitions(config)
        try:
            checkpoint = cast(dict[str, JsonValue], json.loads(core.checkpoint()))
            inspection = cast(dict[str, JsonValue], json.loads(core.inspect()))
        finally:
            core.close()
        # A prefix that ends mid-turn has to publish the transition its pending
        # Actions belong to, or recovery finds Actions to re-drive and nothing
        # saying what they were driving. The prefix may replay no Core input at
        # all -- the base generation was itself folded mid-turn, and the first
        # journalled record is the one that diverged -- and then the transition
        # to keep is the one that generation already published. A quiescent
        # prefix clears any stale one.
        if inspection.get("status") in {"running", "compacting"}:
            pending_transition = (
                transitions[-1]
                if transitions
                else truncated.runtime_state.pending_transition
            )
        else:
            pending_transition = None
        core_last_input_id = inspection.get("last_input_id")
        if not isinstance(core_last_input_id, int):
            raise HarnessInvalidSessionStoreError(
                self.session_id, "restored Core reported no input cursor"
            )
        # The fold advances the Runtime's sequence to the last record it read and
        # leaves the projection's where the generation published it; a publication
        # states one sequence for both, as every other publisher does here.
        sequence = truncated.runtime_state.snapshot_sequence
        # The prefix may itself have reconfigured the Core, and the checkpoint above
        # was taken after it. Stamping the base generation's configuration would
        # describe that checkpoint by the settings in force before the prefix ran,
        # and the repaired generation has no journal left to correct it.
        replayed = truncated.replayed_config(config)
        runtime_state = truncated.runtime_state.model_copy(
            update={
                "core_last_input_id": core_last_input_id,
                "pending_transition": pending_transition,
                "core_capabilities": replayed.capabilities,
                "core_settings": replayed.settings,
                "core_plugins": list(replayed.plugins),
            }
        )
        projection_state = _record_recovery_in_history(
            truncated.projection_state.model_copy(
                update={"snapshot_sequence": sequence}
            ),
            discarded=(
                len(stored.projection_state.snapshot.history.entries)
                - len(truncated.projection_state.snapshot.history.entries)
            ),
        )
        segment = self.session_root / stored.manifest.recovery_journal_segment.path
        self._quarantine_journal(stored.manifest.generation, segment)
        manifest = self.write_generation(
            checkpoint=checkpoint,
            runtime_state=runtime_state,
            projection_state=projection_state,
        )
        if sequence == stored.manifest.snapshot_sequence:
            # Retaining nothing leaves the new generation naming this very
            # segment, and an existing segment is adopted rather than emptied,
            # so the discarded records have to be cleared out of it.
            #
            # Only once CURRENT names the new generation. Both generations name
            # this path, so a process that dies while it still holds the
            # discarded records leaves a store that loads, diverges where it did
            # before, and is repaired again on the next open. Clearing it first
            # would instead leave CURRENT naming a journal that does not exist,
            # and no later open could get past that.
            _empty_journal_segment(segment)
        return manifest

    def _quarantine_journal(self, generation: str, segment: Path) -> None:
        quarantine = self.session_root / _QUARANTINE_DIRNAME
        try:
            _reject_symlink_components(self.session_root, quarantine)
            quarantine.mkdir(mode=0o700, exist_ok=True)
            copy = quarantine / f"{generation}.jsonl"
            copy.write_bytes(segment.read_bytes())
            copy.chmod(0o600)
        except OSError:
            # Diagnostics are worth less than the recovery they document.
            logger.warning(
                "Unified session could not quarantine a diverged journal",
                extra={"session_id": self.session_id, "generation": generation},
            )

    def append_record(self, record_type: str, payload: _StoredModel) -> JournalRecordV1:
        with self._journal_capacity_lock:
            return self._append_record_locked(record_type, payload)

    def _append_record_locked(
        self, record_type: str, payload: _StoredModel
    ) -> JournalRecordV1:
        expected_type = _PAYLOAD_TYPES.get(record_type)
        if expected_type is None or not isinstance(payload, expected_type):
            raise TypeError(f"invalid payload for recovery record {record_type!r}")
        stored = self.load()
        previous = stored.journal[-1] if stored.journal else None
        sequence = (
            previous.sequence + 1
            if previous is not None
            else stored.manifest.recovery_journal_segment.first_sequence
        )
        envelope: dict[str, JsonValue] = {
            "recovery_journal_record_version": 1,
            "sequence": sequence,
            "type": record_type,
            "previous_record_sha256": (
                previous.record_sha256 if previous is not None else None
            ),
            "payload": payload.model_dump(mode="json", by_alias=True),
        }
        envelope["record_sha256"] = sha256_json(envelope)
        record = _JOURNAL_RECORD_ADAPTER.validate_python(envelope)
        journal_path = self.session_root / stored.manifest.recovery_journal_segment.path
        owner = self._journal_reservation_owner.get()
        owner_bytes = (
            self._journal_capacity_reservations.get(owner, 0)
            if owner is not None
            else 0
        )
        protected_bytes = (
            sum(self._journal_capacity_reservations.values()) - owner_bytes
        )
        written = _append_journal_record(
            journal_path, record, protected_bytes=protected_bytes
        )
        self._extend_cache(stored, record, journal_path, written)
        if owner is not None and owner in self._journal_capacity_reservations:
            self._journal_capacity_reservations[owner] = max(0, owner_bytes - written)
        return record

    def _extend_cache(
        self,
        stored: StoredSession,
        record: JournalRecordV1,
        journal_path: Path,
        written: int,
    ) -> None:
        """Fold a freshly appended record into the cached generation.

        ``_apply_journal`` is a left fold, so folding one record over the cached
        effective state matches replaying the whole journal. Without this, every
        append would invalidate the cache it just used and the next read would
        reparse the entire store.
        """
        with self._cache_lock:
            cached = self._cache
            if cached is None or cached.session is not stored:
                return
            try:
                stat = os.lstat(journal_path)
            except OSError:
                self._cache = None
                return
            # Anything other than exactly our own bytes landing on the segment
            # means another writer raced us; fall back to a full reload.
            if cached.key.journal_path != journal_path or (
                stat.st_size != cached.key.journal_size + written
            ):
                self._cache = None
                return
            try:
                runtime_state, projection_state = _apply_journal(
                    stored.runtime_state, stored.projection_state, (record,)
                )
            except Exception:
                # The record is already durable; let the next full load surface
                # whatever it rejects, exactly as it would without a cache.
                self._cache = None
                return
            self._cache = _CachedGeneration(
                key=_GenerationCacheKey(
                    current=cached.key.current,
                    journal_path=journal_path,
                    journal_size=stat.st_size,
                    journal_mtime_ns=stat.st_mtime_ns,
                ),
                session=StoredSession(
                    manifest=stored.manifest,
                    checkpoint=stored.checkpoint,
                    runtime_state=runtime_state,
                    projection_state=projection_state,
                    journal=stored.journal + (record,),
                    published_runtime_state=stored.published_runtime_state,
                    published_projection_state=stored.published_projection_state,
                ),
            )

    def journal_bytes(self) -> int:
        stored = self.load()
        journal_path = self.session_root / stored.manifest.recovery_journal_segment.path
        _reject_symlink(journal_path)
        return journal_path.stat().st_size

    def has_journal_capacity(self, required_bytes: int) -> bool:
        if required_bytes < 0:
            raise ValueError("required journal capacity cannot be negative")
        with self._journal_capacity_lock:
            reserved = sum(self._journal_capacity_reservations.values())
            return (
                self.journal_bytes() + reserved + required_bytes <= _MAX_DOCUMENT_BYTES
            )

    def reserve_journal_capacity(self, reservations: dict[str, int]) -> None:
        if any(
            not owner or required_bytes < 0
            for owner, required_bytes in reservations.items()
        ):
            raise ValueError(
                "journal capacity reservations require an owner and nonnegative bytes"
            )
        with self._journal_capacity_lock:
            if set(reservations) & self._journal_capacity_reservations.keys():
                raise ValueError("journal capacity reservation already exists")
            required = sum(reservations.values())
            if not self.has_journal_capacity(required):
                raise HarnessStoreCapacityError(
                    "insufficient reserved journal capacity"
                )
            self._journal_capacity_reservations.update(reservations)

    def restore_journal_capacity(self, owner: str, required_bytes: int) -> None:
        if not owner or required_bytes < 0:
            raise ValueError(
                "journal capacity reservations require an owner and nonnegative bytes"
            )
        with self._journal_capacity_lock:
            if owner in self._journal_capacity_reservations:
                raise ValueError("journal capacity reservation already exists")
            self._journal_capacity_reservations[owner] = required_bytes

    def release_journal_capacity(self, owner: str) -> None:
        with self._journal_capacity_lock:
            self._journal_capacity_reservations.pop(owner, None)

    @property
    def current_journal_reservation_owner(self) -> str | None:
        return self._journal_reservation_owner.get()

    @contextmanager
    def use_journal_reservation(self, owner: str | None) -> Iterator[None]:
        if owner is None:
            yield
            return
        token = self._journal_reservation_owner.set(owner)
        try:
            yield
        finally:
            self._journal_reservation_owner.reset(token)

    def reserve_command(
        self, client_command_id: str, method: str, params: dict[str, JsonValue]
    ) -> CommandReservation:
        stored = self.load()
        params_sha256 = sha256_json(params)
        existing = next(
            (
                receipt
                for receipt in stored.runtime_state.command_receipts
                if receipt.client_command_id == client_command_id
            ),
            None,
        )
        if existing is not None:
            if existing.method != method or existing.params_sha256 != params_sha256:
                raise HarnessCommandConflictError(client_command_id)
            if existing.state == "failed":
                # An abandoned receipt already has its outcome. Reserving over it
                # would leave the journal with a success for a command that is
                # not reserved, which no reader can fold.
                raise HarnessCommandConflictError(
                    client_command_id, "already abandoned"
                )
            return CommandReservation(
                newly_reserved=False,
                completed=existing.state == "succeeded",
                response=existing.response if existing.state == "succeeded" else None,
            )
        self.append_record(
            "command_reserved",
            CommandReservedPayloadV1(
                client_command_id=client_command_id,
                method=method,
                params=params,
                params_sha256=params_sha256,
            ),
        )
        return CommandReservation(newly_reserved=True)

    def _reserved_internal_receipts(self, namespace: str) -> list[CommandReceiptV1]:
        """The namespace's open reservations, oldest first.

        An internal ID is its namespace followed by the zero-padded journal
        sequence that allocated it, and only that shape counts: a client picks
        its own command IDs, so a prefix match would let one addressed
        ``capabilities:...`` be mistaken for a command this Runtime issued.
        """
        pattern = re.compile(rf"^{re.escape(namespace)}:[0-9]{{16}}$")
        return [
            receipt
            for receipt in self.load().runtime_state.command_receipts
            if receipt.state == "reserved" and pattern.match(receipt.client_command_id)
        ]

    def pending_internal_command(self, namespace: str) -> PendingInternalCommand | None:
        """The namespace's resumable reservation, if it has one.

        A reservation written without the parameters to resume it is reported by
        ``orphaned_internal_commands`` instead, because recovery runs through
        here: stores an earlier build wrote that way would otherwise fail every
        restore for the rest of their lives. Two open reservations in one
        namespace is a different matter -- IDs are allocated one at a time and
        reused until answered, so it means something upstream is broken and the
        read says so rather than picking one.
        """
        reserved = self._reserved_internal_receipts(namespace)
        if len(reserved) > 1:
            raise HarnessInvalidSessionStoreError(
                self.session_id,
                "multiple pending internal commands share one namespace",
            )
        if not reserved:
            return None
        receipt = reserved[0]
        if receipt.params is None:
            return None
        return PendingInternalCommand(
            client_command_id=receipt.client_command_id,
            method=receipt.method,
            params=dict(receipt.params),
        )

    def orphaned_internal_commands(
        self, namespace: str
    ) -> tuple[OrphanedInternalCommand, ...]:
        """The namespace's open reservations no caller can finish.

        One shape qualifies: a reservation folded into a snapshot by a build
        that did not carry its parameters across. That population is closed --
        this build writes them -- so a session still reporting one after the
        upgrade means something new is dropping them.
        """
        return tuple(
            OrphanedInternalCommand(
                client_command_id=receipt.client_command_id,
                reason="the reservation was written without the parameters to resume it",
            )
            for receipt in self._reserved_internal_receipts(namespace)
            if receipt.params is None
        )

    def resolve_internal_command_id(self, namespace: str) -> str:
        """Reuse an interrupted operation or allocate its next durable sequence ID."""
        pending = self.pending_internal_command(namespace)
        if pending is not None:
            return pending.client_command_id
        stored = self.load()
        previous = stored.journal[-1] if stored.journal else None
        sequence = (
            previous.sequence + 1
            if previous is not None
            else stored.manifest.recovery_journal_segment.first_sequence
        )
        return f"{namespace}:{sequence:016d}"

    def succeed_command(
        self, client_command_id: str, response: JsonValue
    ) -> ReceiptSucceededRecordV1:
        record = self.append_record(
            "receipt_succeeded",
            ReceiptSucceededPayloadV1(
                client_command_id=client_command_id, response=response
            ),
        )
        if not isinstance(record, ReceiptSucceededRecordV1):
            raise TypeError("journal record parser returned the wrong type")
        return record

    def fail_command(
        self, client_command_id: str, reason: str
    ) -> ReceiptFailedRecordV1:
        """Settle a reservation whose command will never be applied.

        The outcome of an abandoned command is as durable as the outcome of one
        that ran: an unsettled reservation holds the store off quiescence, which
        blocks compaction, export and every operation derived from them.
        """
        record = self.append_record(
            "receipt_failed",
            ReceiptFailedPayloadV1(client_command_id=client_command_id, reason=reason),
        )
        if not isinstance(record, ReceiptFailedRecordV1):
            raise TypeError("journal record parser returned the wrong type")
        return record

    def record_core_input(
        self,
        input: RustHarnessInput,
        transition: RustSessionTransition,
        checkpoint_capabilities: RustHarnessCapabilitySet | None = None,
        checkpoint_settings: RustHarnessSettings | None = None,
        checkpoint_plugins: Sequence[RustPluginContextDefinition] | None = None,
    ) -> CoreInputRecordV1:
        stored = self.load()
        for existing in reversed(stored.journal):
            if not isinstance(existing, CoreInputRecordV1):
                continue
            if existing.payload.input.input_id != input.input_id:
                continue
            payload = CoreInputPayloadV1(
                input=input,
                transition_sha256=transition_sha256(transition),
                replay_transition_sha256=_replay_transition_sha256(transition),
                checkpoint_capabilities=existing.payload.checkpoint_capabilities,
                checkpoint_settings=existing.payload.checkpoint_settings,
                checkpoint_plugins=existing.payload.checkpoint_plugins,
                # Taken from the record rather than recomputed, like the
                # baselines above: a record written before shapes were stored has
                # none, and recomputing one here would make every retry against
                # an older journal look like a conflicting rewrite.
                transition_shape=existing.payload.transition_shape,
            )
            if existing.payload != payload:
                raise HarnessInvalidSessionStoreError(
                    self.session_id, "conflicting Core input persistence retry"
                )
            return existing
        opens_generation = not any(
            isinstance(record, CoreInputRecordV1) for record in stored.journal
        )
        payload = CoreInputPayloadV1(
            input=input,
            transition_sha256=transition_sha256(transition),
            replay_transition_sha256=_replay_transition_sha256(transition),
            checkpoint_capabilities=checkpoint_capabilities
            if opens_generation
            else None,
            checkpoint_settings=checkpoint_settings if opens_generation else None,
            checkpoint_plugins=(
                list(checkpoint_plugins)
                if opens_generation and checkpoint_plugins is not None
                else None
            ),
            transition_shape=transition_shape(transition),
        )
        try:
            record = self.append_record("core_input", payload)
        except Exception:
            for existing in reversed(self.load().journal):
                if (
                    isinstance(existing, CoreInputRecordV1)
                    and existing.payload == payload
                ):
                    return existing
            raise
        if not isinstance(record, CoreInputRecordV1):
            raise TypeError("journal record parser returned the wrong type")
        return record

    def record_action_intent(
        self,
        *,
        action_id: str,
        kind: Literal[
            "completion", "tool", "hook", "process", "callback", "child", "filesystem"
        ],
        request: JsonValue,
        recovery_mode: Literal[
            "reconnect_or_fail", "redeliver", "idempotent_retry", "reconcile", "fail"
        ],
        lease_id: str | None = None,
    ) -> ActionIntentRecordV1:
        record = self.append_record(
            "action_intent",
            ActionIntentPayloadV1(
                action_id=action_id,
                kind=kind,
                request_sha256=sha256_json(request),
                request=request,
                recovery_mode=recovery_mode,
                lease_id=lease_id,
            ),
        )
        if not isinstance(record, ActionIntentRecordV1):
            raise TypeError("journal record parser returned the wrong type")
        return record

    def record_process_operation_dispatched(
        self,
        *,
        action_id: str,
        manager_instance_id: str,
        request_sha256: str,
        prepared_start: PreparedProcessStartV1 | None = None,
    ) -> ProcessOperationDispatchedRecordV1 | None:
        payload = ProcessOperationDispatchedPayloadV1(
            action_id=action_id,
            manager_instance_id=manager_instance_id,
            request_sha256=request_sha256,
            prepared_start=prepared_start,
        )
        stored = self.load()
        action = next(
            (
                item
                for item in stored.runtime_state.actions
                if item.action_id == action_id
            ),
            None,
        )
        if action is not None and action.process_manager_instance_id is not None:
            if (
                action.process_manager_instance_id != manager_instance_id
                or action.process_request_sha256 != request_sha256
                or action.prepared_process_start != prepared_start
            ):
                raise HarnessInvalidSessionStoreError(
                    self.session_id, "conflicting process dispatch persistence retry"
                )
            return next(
                (
                    record
                    for record in reversed(stored.journal)
                    if isinstance(record, ProcessOperationDispatchedRecordV1)
                    and record.payload == payload
                ),
                None,
            )
        try:
            record = self.append_record("process_operation_dispatched", payload)
        except Exception:
            stored = self.load()
            action = next(
                (
                    item
                    for item in stored.runtime_state.actions
                    if item.action_id == action_id
                ),
                None,
            )
            if (
                action is not None
                and action.process_manager_instance_id == manager_instance_id
                and action.process_request_sha256 == request_sha256
                and action.prepared_process_start == prepared_start
            ):
                return next(
                    (
                        existing
                        for existing in reversed(stored.journal)
                        if isinstance(existing, ProcessOperationDispatchedRecordV1)
                        and existing.payload == payload
                    ),
                    None,
                )
            raise
        if not isinstance(record, ProcessOperationDispatchedRecordV1):
            raise TypeError("journal record parser returned the wrong type")
        return record

    def record_process_state(
        self, process: ManagedProcessV1
    ) -> ProcessStateChangedRecordV1 | None:
        stored = self.load()
        existing_process = next(
            (
                item
                for item in stored.runtime_state.processes
                if item.process_id == process.process_id
            ),
            None,
        )
        if existing_process == process:
            return next(
                (
                    record
                    for record in reversed(stored.journal)
                    if isinstance(record, ProcessStateChangedRecordV1)
                    and record.payload.process == process
                ),
                None,
            )
        try:
            record = self.append_record(
                "process_state_changed", ProcessStateChangedPayloadV1(process=process)
            )
        except Exception:
            stored = self.load()
            if any(item == process for item in stored.runtime_state.processes):
                return next(
                    (
                        existing
                        for existing in reversed(stored.journal)
                        if isinstance(existing, ProcessStateChangedRecordV1)
                        and existing.payload.process == process
                    ),
                    None,
                )
            raise
        if not isinstance(record, ProcessStateChangedRecordV1):
            raise TypeError("journal record parser returned the wrong type")
        return record

    def reconcile_orphaned_processes(self) -> tuple[ManagedProcessV1, ...]:
        stored = self.load()
        process_ids = {process.process_id for process in stored.runtime_state.processes}
        reconciled = [
            process.model_copy(
                update={
                    "status": "orphaned",
                    "exit_code": None,
                    "finished_at": max(
                        _timestamp(), process.started_at or process.created_at
                    ),
                }
            )
            for process in stored.runtime_state.processes
            if process.status == "running"
        ]
        reconciled.extend(
            ManagedProcessV1(
                process_id=prepared.process_id,
                start_action_id=prepared.start_action_id,
                start_call_id=prepared.start_call_id,
                manager_instance_id=prepared.manager_instance_id,
                command=prepared.command,
                cwd=prepared.cwd,
                command_environment=prepared.command_environment,
                pty_backend=None,
                start_outcome="failed",
                start_failure_stage="recovery",
                status="orphaned",
                exit_code=None,
                created_at=prepared.created_at,
                started_at=None,
                finished_at=max(_timestamp(), prepared.created_at),
            )
            for action in stored.runtime_state.actions
            if action.state in {"pending", "running"}
            and (prepared := action.prepared_process_start) is not None
            and prepared.process_id not in process_ids
        )
        for process in sorted(reconciled, key=lambda item: item.process_id):
            self.record_process_state(process)
        return tuple(reconciled)

    def record_process_notification_submitted(
        self, process_id: str, notification_id: str
    ) -> ProcessNotificationSubmittedRecordV1 | None:
        payload = ProcessNotificationSubmittedPayloadV1(
            process_id=process_id, notification_id=notification_id
        )
        stored = self.load()
        submitted = next(
            (
                item
                for item in stored.runtime_state.submitted_process_notifications
                if item.process_id == process_id
            ),
            None,
        )
        if submitted is not None:
            if submitted.notification_id != notification_id:
                raise HarnessInvalidSessionStoreError(
                    self.session_id,
                    "conflicting process notification persistence retry",
                )
            return next(
                (
                    record
                    for record in reversed(stored.journal)
                    if isinstance(record, ProcessNotificationSubmittedRecordV1)
                    and record.payload == payload
                ),
                None,
            )
        try:
            record = self.append_record("process_notification_submitted", payload)
        except Exception:
            stored = self.load()
            if any(
                item.process_id == process_id
                and item.notification_id == notification_id
                for item in stored.runtime_state.submitted_process_notifications
            ):
                return next(
                    (
                        existing
                        for existing in reversed(stored.journal)
                        if isinstance(existing, ProcessNotificationSubmittedRecordV1)
                        and existing.payload == payload
                    ),
                    None,
                )
            raise
        if not isinstance(record, ProcessNotificationSubmittedRecordV1):
            raise TypeError("journal record parser returned the wrong type")
        return record

    def record_action_result(
        self, action_id: str, result: JsonValue, *, failed: bool = False
    ) -> ActionResultRecordV1 | None:
        payload = ActionResultPayloadV1(
            action_id=action_id,
            state="failed" if failed else "succeeded",
            result=result,
        )
        for existing in reversed(self.load().journal):
            if not isinstance(existing, ActionResultRecordV1):
                continue
            if existing.payload.action_id != action_id:
                continue
            if existing.payload != payload:
                raise HarnessInvalidSessionStoreError(
                    self.session_id, "conflicting Action result persistence retry"
                )
            return existing
        try:
            record = self.append_record("action_result", payload)
        except Exception:
            stored = self.load()
            action = next(
                (
                    item
                    for item in stored.runtime_state.actions
                    if item.action_id == action_id
                ),
                None,
            )
            if (
                action is not None
                and action.state == payload.state
                and action.result == result
            ):
                return next(
                    (
                        existing
                        for existing in reversed(stored.journal)
                        if isinstance(existing, ActionResultRecordV1)
                        and existing.payload == payload
                    ),
                    None,
                )
            raise
        if not isinstance(record, ActionResultRecordV1):
            raise TypeError("journal record parser returned the wrong type")
        return record

    def register_callback(
        self, callback_id: str, kind: str, routing: dict[str, JsonValue]
    ) -> CallbackRegisteredRecordV1:
        record = self.append_record(
            "callback_registered",
            CallbackRegisteredPayloadV1(
                callback_id=callback_id, kind=kind, routing=routing
            ),
        )
        if not isinstance(record, CallbackRegisteredRecordV1):
            raise TypeError("journal record parser returned the wrong type")
        return record

    def resolve_callback(
        self, callback_id: str, result: JsonValue, *, failed: bool = False
    ) -> CallbackResolvedRecordV1:
        record = self.append_record(
            "callback_resolved",
            CallbackResolvedPayloadV1(
                callback_id=callback_id,
                state="failed" if failed else "resolved",
                result=result,
            ),
        )
        if not isinstance(record, CallbackResolvedRecordV1):
            raise TypeError("journal record parser returned the wrong type")
        return record

    def advance_projection(
        self, watermark: int, snapshot: PublicSessionState
    ) -> ProjectionAdvancedRecordV1:
        record = self.append_record(
            "projection_advanced",
            ProjectionAdvancedPayloadV1(watermark=watermark, snapshot=snapshot),
        )
        if not isinstance(record, ProjectionAdvancedRecordV1):
            raise TypeError("journal record parser returned the wrong type")
        return record

    def advance_projection_delta(
        self, watermark: int, delta: ProjectionDelta
    ) -> ProjectionDeltaRecordV1:
        """Journal a projection advance as a structured delta.

        The delta carries only what changed, so journal growth is linear in
        session length instead of re-serializing the full history each advance.
        """
        record = self.append_record(
            "projection_delta",
            ProjectionDeltaPayloadV1(watermark=watermark, delta=delta),
        )
        if not isinstance(record, ProjectionDeltaRecordV1):
            raise TypeError("journal record parser returned the wrong type")
        return record


def empty_runtime_state(  # noqa: PLR0913 - explicit Runtime state fields
    session_id: str,
    *,
    snapshot_sequence: int,
    plugin_lock: PluginLockV1,
    cwd: str | None = "",
    pins: dict[str, str | None] | None = None,
    root_session_id: str | None = None,
    parent_session_id: str | None = None,
    hook_bindings: list[RustHarnessHookBinding] | None = None,
    identity: SessionIdentity | None = None,
    subagents: SubagentRuntimeState | None = None,
    subagent_spawn_key: str | None = None,
    subagent_template_digest: str | None = None,
    subagent_policy_ceiling_digest: str | None = None,
    storage_lifetime: Literal["persistent", "ephemeral"] = "persistent",
) -> RuntimeStateV3:
    resolved_root_session_id = root_session_id or session_id
    if identity is None:
        identity = (
            RootSessionIdentity(
                session_id=session_id, root_session_id=resolved_root_session_id
            )
            if parent_session_id is None
            else ForkSessionIdentity(
                session_id=session_id,
                root_session_id=resolved_root_session_id,
                parent_session_id=parent_session_id,
            )
        )
    return RuntimeStateV3(
        session_id=session_id,
        storage_lifetime=storage_lifetime,
        session_metadata=SessionMetadataV1(
            cwd=cwd,
            root_session_id=resolved_root_session_id,
            parent_session_id=parent_session_id,
            subagent_spawn_key=subagent_spawn_key,
            subagent_template_digest=subagent_template_digest,
            subagent_policy_ceiling_digest=subagent_policy_ceiling_digest,
            hook_bindings=list(hook_bindings or []),
        ).model_copy(update=pins or {}),
        identity=identity,
        subagents=subagents,
        snapshot_sequence=snapshot_sequence,
        command_receipts=[],
        actions=[],
        callbacks=[],
        provider_operations=[],
        processes=[],
        submitted_process_notifications=[],
        plugin_lock=plugin_lock,
        children=[],
    )


def _load_runtime_state(
    value: JsonValue, _projection: ProjectionStateV1
) -> RuntimeStateV3:
    if not isinstance(value, dict):
        raise ValueError("runtime state must be an object")
    version = value.get("runtime_state_version")
    if version != _RUNTIME_STATE_VERSION:
        raise ValueError(f"unsupported Runtime state version: {version!r}")
    return RuntimeStateV3.model_validate(value)


def history_fingerprint(history: list[InteropHistoryMessageV1]) -> str:
    return sha256_json(
        cast(
            JsonValue,
            [
                item.model_dump(mode="json", by_alias=True, exclude_none=True)
                for item in history
            ],
        )
    )


def committed_history(
    source: InteropSourceV1, history: list[InteropHistoryMessageV1]
) -> CommittedHistoryV1:
    # Constructed rather than validated: the validator's one job is to re-derive
    # the fingerprint and compare, and the fingerprint was just computed here from
    # the same list. Both callers hand over history that came through
    # ``_INTEROP_HISTORY_ADAPTER``, so the check being skipped cannot fail. It
    # still runs where it means something -- on the parse path, where the digest
    # is a claim rather than something we derived.
    return CommittedHistoryV1.model_construct(
        source=source, history=history, history_sha256=history_fingerprint(history)
    )


def checkpoint_messages(checkpoint: dict[str, JsonValue]) -> list[JsonValue]:
    """The Core's context messages, or a reason the checkpoint has none to give."""
    context = checkpoint.get("context")
    if not isinstance(context, dict):
        raise ValueError("Core checkpoint has no context")
    raw_messages = cast(dict[str, JsonValue], context).get("messages")
    if not isinstance(raw_messages, list):
        raise ValueError("Core checkpoint context has no messages")
    return cast(list[JsonValue], raw_messages)


def committed_history_from_checkpoint(
    source: InteropSourceV1, checkpoint: dict[str, JsonValue]
) -> CommittedHistoryV1:
    """The interop export a generation would have carried, read off its checkpoint.

    The Core keeps assistant turns under `content` and the interop schema keeps
    them under `parts`; the two differ in shape rather than in what they hold, so
    the export a generation used to store is recoverable from the checkpoint
    stored beside it. Deriving it here is what lets a generation stop carrying a
    second copy of the whole conversation.
    """
    normalized: list[JsonValue] = []
    for item in checkpoint_messages(checkpoint):
        if not isinstance(item, dict):
            raise ValueError("Core checkpoint contains an invalid context message")
        raw_message = cast(dict[str, JsonValue], item).get("message")
        if not isinstance(raw_message, dict):
            raise ValueError("Core checkpoint contains an invalid context message")
        message = dict(cast(dict[str, JsonValue], raw_message))
        raw_content = message.get("content")
        if isinstance(raw_content, list):
            message["content"] = [
                export_file_image_fallback(cast(dict[str, JsonValue], block))
                if isinstance(block, dict)
                else block
                for block in cast(list[JsonValue], raw_content)
            ]
        if message.get("role") == "assistant":
            raw_parts = message.pop("content", [])
            if not isinstance(raw_parts, list):
                raise ValueError("Core checkpoint contains invalid assistant content")
            message["parts"] = [
                (
                    {
                        "type": "content",
                        "content": export_file_image_fallback(
                            cast(dict[str, JsonValue], part)
                        ),
                    }
                    if isinstance(part, dict)
                    and part.get("type") in _CONTENT_PART_TYPES
                    else part
                )
                for part in cast(list[JsonValue], raw_parts)
            ]
        normalized.append(cast(JsonValue, message))
    return committed_history(
        source, _INTEROP_HISTORY_ADAPTER.validate_python(normalized)
    )


def canonical_json(value: JsonValue) -> bytes:
    if _standard_encoder_is_canonical(value):
        try:
            return json.dumps(
                value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        except UnicodeEncodeError:
            pass
    return rfc8785.dumps(value)


def _standard_encoder_is_canonical(value: JsonValue) -> bool:
    """Whether `json.dumps` would emit exactly what RFC 8785 asks for.

    Everything the store persists is digested, and the documents being digested
    hold whole conversations, so canonicalization is most of what a load costs.
    The standard library encoder is several times faster than the pure-Python
    canonicalizer and agrees with it byte for byte -- but only away from the
    three places they part ways: floats (RFC 8785 formats numbers the way ES6
    does, which is not Python's `repr`), integers outside the range RFC 8785
    admits, and object keys past ASCII (RFC 8785 orders them by UTF-16 code
    unit, Python by code point). Anything holding one of those falls back.

    Exact type checks rather than `isinstance`, so that an exotic subclass takes
    the fallback instead of an encoder that may not agree about it.
    """
    pending: list[JsonValue] = [value]
    while pending:
        item = pending.pop()
        item_type = type(item)
        if item_type is str or item is None or item_type is bool:
            continue
        if item_type is dict:
            for key, nested in cast(dict[str, JsonValue], item).items():
                if type(key) is not str or not key.isascii():
                    return False
                pending.append(nested)
            continue
        if item_type is list:
            pending.extend(cast(list[JsonValue], item))
            continue
        if item_type is int:
            if item not in _CANONICAL_INTEGER_RANGE:
                return False
            continue
        return False
    return True


def sha256_json(value: JsonValue) -> str:
    return _sha256(canonical_json(value))


def transition_sha256(transition: RustSessionTransition) -> str:
    return sha256_json(transition.model_dump(mode="json", by_alias=True))


def _replay_transition_sha256(transition: RustSessionTransition) -> str:
    return _digest_with_llm_actions_normalized(
        transition, _normalize_llm_action_for_replay
    )


def _replay_transition_sha256_with_model_input(
    transition: RustSessionTransition,
) -> str:
    return _digest_with_llm_actions_normalized(
        transition, _normalize_llm_action_for_replay_with_model_input
    )


def _digest_with_llm_actions_normalized(
    transition: RustSessionTransition, normalize: Callable[[dict[str, JsonValue]], None]
) -> str:
    value = cast(
        dict[str, JsonValue], transition.model_dump(mode="json", by_alias=True)
    )
    next_action = value.get("next")
    if not isinstance(next_action, dict) or next_action.get("type") != "actions":
        return sha256_json(value)
    directives = next_action.get("directives")
    if not isinstance(directives, list):
        return sha256_json(value)
    for directive in directives:
        if not isinstance(directive, dict):
            continue
        action = directive.get("action")
        if not isinstance(action, dict) or action.get("type") != "llm_call":
            continue
        normalize(action)
    return sha256_json(value)


def _transition_matches_recorded_digest(
    transition: RustSessionTransition,
    payload: CoreInputPayloadV1,
    durable_actions: list[RuntimeActionV1],
) -> bool:
    if transition_sha256(transition) == payload.transition_sha256:
        return True
    if payload.replay_transition_sha256 is not None:
        recorded = payload.replay_transition_sha256
        return (
            # Journals recorded before the model input left the digest.
            _replay_transition_sha256_with_model_input(transition) == recorded
            or _replay_transition_sha256(transition) == recorded
        )
    legacy_digest = _legacy_transition_sha256(transition, durable_actions)
    return legacy_digest == payload.transition_sha256


def _legacy_transition_sha256(  # noqa: PLR0911 - one return per legacy transition shape
    transition: RustSessionTransition, durable_actions: list[RuntimeActionV1]
) -> str | None:
    value = cast(
        dict[str, JsonValue], transition.model_dump(mode="json", by_alias=True)
    )
    next_action = value.get("next")
    if not isinstance(next_action, dict) or next_action.get("type") != "actions":
        return None
    directives = next_action.get("directives")
    if not isinstance(directives, list):
        return None
    durable_by_id = {action.action_id: action for action in durable_actions}
    current_by_id = {
        action.action_id: action
        for action in transition.actions
        if isinstance(action, RustLLMCallAction)
    }
    replaced = False
    for directive in directives:
        if not isinstance(directive, dict):
            continue
        action = directive.get("action")
        if not isinstance(action, dict) or action.get("type") != "llm_call":
            continue
        action_id = action.get("action_id")
        if not isinstance(action_id, str):
            return None
        current = current_by_id.get(action_id)
        durable = durable_by_id.get(action_id)
        if current is None or durable is None or durable.kind != "completion":
            return None
        try:
            recorded = RustLLMCallAction.model_validate(durable.request)
        except ValueError:
            return None
        if _llm_action_replay_identity(current) != _llm_action_replay_identity(
            recorded
        ):
            return None
        directive["action"] = cast(
            JsonValue, recorded.model_dump(mode="json", by_alias=True)
        )
        replaced = True
    return sha256_json(value) if replaced else None


def _llm_action_replay_identity(action: RustLLMCallAction) -> dict[str, JsonValue]:
    value = cast(dict[str, JsonValue], action.model_dump(mode="json", by_alias=True))
    _normalize_llm_action_for_replay(value)
    return value


def _normalize_llm_action_for_replay(action: dict[str, JsonValue]) -> None:
    # The model input says what the Core still owes the Runtime's model cache, which a
    # replayed Core recomputes against an empty one. ADR 0002 lets it send a full
    # replacement where the live Core sent an increment, so replay cannot compare it.
    action.pop("max_iterations", None)
    action.pop("model_input", None)


def _normalize_llm_action_for_replay_with_model_input(
    action: dict[str, JsonValue],
) -> None:
    action.pop("max_iterations", None)
    model_input = action.get("model_input")
    if not isinstance(model_input, dict):
        return
    messages = model_input.get("messages")
    if isinstance(messages, dict) and messages.get("type") == "replace":
        values = messages.get("messages")
        if (
            isinstance(values, list)
            and values
            and isinstance(values[0], dict)
            and values[0].get("role") == "system"
        ):
            values[0].pop("content", None)
    tool_catalog = model_input.get("tool_catalog")
    if isinstance(tool_catalog, dict) and tool_catalog.get("type") == "replace":
        tool_catalog.pop("tools", None)


def _prune_settled_completions(
    runtime: RuntimeStateV3, core_action_ids: frozenset[str]
) -> RuntimeStateV3:
    """Drop the request and result bodies of settled completions Core no longer holds.

    A completion request embeds the whole transcript, so retaining one per turn
    grows the ledger with the square of the session's length: on a real session
    six of them were 2.16 MB of a 2.27 MB snapshot, and that snapshot is what
    every load reparses. Results are smaller individually but there is one per
    turn for the life of the session — 2.18 MB across 90 turns of 24 KB replies.

    Recovery rehydrates a request whenever Core retains an Action across a
    restart: ``_start_transition_actions`` calls ``action_from_recovery_state``
    for every ``RustKeepActionDirective`` before it filters by pending id, so a
    pruned request there strands the session for good. Only Core can name such a
    directive, and after this generation the journal restarts empty, so the only
    Actions a replayed Core can keep are the ones it holds right now
    (``core_action_ids``) plus ones dispatched later, whose intents are journalled
    afresh. Excluding the ids Core holds is therefore what makes this safe.

    A result is read back in one place, ``_resolve_action``'s redelivery
    short-circuit, and that has the same shape: Core only asks again for an Action
    it still holds, so ``core_action_ids`` covers it. The exception used to be
    rewind, which re-mints an ``action_id`` because Core derives identity from
    content — the settled entry from the discarded timeline was still in the ledger
    and was replayed. ``_runtime_state_for_replaced_context`` now empties the
    ledgers at the rewind itself, so every settled Action here belongs to the live
    timeline, where ids do not repeat. ``result_pruned`` records the distinction
    anyway so a mistake surfaces as a diagnosable error rather than a validation
    failure on ``None``.

    Ledger settlement is *not* sufficient on its own, and quiescence does not
    imply Core is idle. ``_record_action_result`` journals a result and releases
    the lock before ``_drive`` hands it to Core, and on the notification path
    (``deliver_notification``) no command receipt is reserved across the turn, so
    in that gap the ledger is quiescent while Core still calls the Action live. A
    capacity-triggered ``_compact_store_sync(allow_recoverable=True)`` publishes a
    generation in exactly that window. Core still holds the Action throughout, so
    it is named in ``core_action_ids`` and neither body is touched.

    Quiescence is not part of the predicate either. One receipt left reserved
    holds a Session away from it for good, and every publication until then adds
    another transcript, so waiting for rest is how a ledger reaches the size
    where nothing can load it. Settlement, which quiescence used to imply, is
    now checked per Action: the stored model rejects a pruned body on an Action
    still pending or running, and ``model_copy`` does not revalidate, so pruning
    one publishes a generation that only fails on the way back in.

    Keep the predicate completion-only. ``record_action_result`` treats a repeat
    call as a no-op when the stored state and result already match, and the one
    caller that can re-enter it across a generation boundary is
    ``_retry_process_action_result``, gated on ``_process_action_reservations``.
    A process Action therefore does read its own result back, and pruning that
    kind would turn the idempotent retry into a spurious mismatch.
    """
    actions = [
        _prune_settled_completion(action, core_action_ids) for action in runtime.actions
    ]
    if all(
        pruned is original
        for pruned, original in zip(actions, runtime.actions, strict=True)
    ):
        return runtime
    return runtime.model_copy(update={"actions": actions})


def _prune_settled_completion(
    action: RuntimeActionV1, core_action_ids: frozenset[str]
) -> RuntimeActionV1:
    if (
        action.kind != "completion"
        or action.state in {"pending", "running"}
        or action.action_id in core_action_ids
    ):
        return action
    update: dict[str, JsonValue | bool] = {}
    if not action.request_pruned and action.request is not None:
        update |= {"request": None, "request_pruned": True}
    if not action.result_pruned and action.result is not None:
        update |= {"result": None, "result_pruned": True}
    if not update:
        return action
    return action.model_copy(update=update)


def _discard_provider_operation_results(runtime: RuntimeStateV3) -> RuntimeStateV3:
    """Drop provider operation results, which duplicate the Action's byte for byte.

    ``_apply_journal`` folds one ``ActionResultRecordV1`` into both the Action and
    its provider operation, so the two hold the same payload; at 24 KB replies that
    second copy was 2.15 MB of a 4.44 MB runtime state, and every ``load`` reparses
    all of it. Nothing reads it back — ``quiescent`` consults ``state`` and no other
    caller in the workspace touches a provider operation — so unlike the request
    prune this needs no reachability guard.

    The field stays on the model rather than being removed: ``_StoredModel`` forbids
    extras, so generations already carrying a result must still validate.
    """
    operations = [
        operation.model_copy(update={"result": None})
        if operation.result is not None
        else operation
        for operation in runtime.provider_operations
    ]
    if all(
        stripped is original
        for stripped, original in zip(
            operations, runtime.provider_operations, strict=True
        )
    ):
        return runtime
    return runtime.model_copy(update={"provider_operations": operations})


def _apply_journal(  # noqa: PLR0912, PLR0914, PLR0915 - one branch per journal record
    runtime: RuntimeStateV3,
    projection: ProjectionStateV1,
    records: tuple[JournalRecordV1, ...],
) -> tuple[RuntimeStateV3, ProjectionStateV1]:
    receipts = {item.client_command_id: item for item in runtime.command_receipts}
    actions = {item.action_id: item for item in runtime.actions}
    callbacks = {item.callback_id: item for item in runtime.callbacks}
    provider_operations = {
        item.operation_id: item for item in runtime.provider_operations
    }
    processes = {item.process_id: item for item in runtime.processes}
    submitted_process_notifications = {
        item.process_id: item for item in runtime.submitted_process_notifications
    }
    children = {item.session_id: item for item in runtime.children}
    current_projection = projection
    for record in records:
        match record:
            case CommandReservedRecordV1(payload=payload):
                if payload.client_command_id in receipts:
                    raise ValueError("duplicate client command identity")
                receipts[payload.client_command_id] = CommandReceiptV1(
                    client_command_id=payload.client_command_id,
                    method=payload.method,
                    params_sha256=payload.params_sha256,
                    state="reserved",
                    params=dict(payload.params),
                )
            case ReceiptSucceededRecordV1(payload=payload):
                receipt = receipts.get(payload.client_command_id)
                if receipt is None or receipt.state != "reserved":
                    raise ValueError("successful receipt has no unique reservation")
                receipts[payload.client_command_id] = receipt.model_copy(
                    update={
                        "state": "succeeded",
                        "response": payload.response,
                        "params": None,
                    }
                )
            case ReceiptFailedRecordV1(payload=payload):
                receipt = receipts.get(payload.client_command_id)
                if receipt is None or receipt.state != "reserved":
                    raise ValueError("failed receipt has no unique reservation")
                receipts[payload.client_command_id] = receipt.model_copy(
                    update={"state": "failed", "params": None}
                )
            case ActionIntentRecordV1(payload=payload):
                if payload.action_id in actions:
                    raise ValueError("duplicate action identity")
                actions[payload.action_id] = RuntimeActionV1(
                    action_id=payload.action_id,
                    kind=payload.kind,
                    state="pending",
                    request_sha256=payload.request_sha256,
                    request=payload.request,
                    lease_id=payload.lease_id,
                    recovery_mode=payload.recovery_mode,
                )
                if payload.kind == "completion":
                    provider_operations[payload.action_id] = ProviderOperationV1(
                        operation_id=payload.action_id,
                        provider="configured",
                        state="running",
                        request_sha256=payload.request_sha256,
                        recovery_mode=cast(
                            Literal["reconnect_or_fail", "idempotent_retry", "fail"],
                            payload.recovery_mode,
                        ),
                        idempotency_key=payload.lease_id,
                    )
                action_name = _action_name(payload.request)
                if payload.kind == "child" and action_name == "subagent.spawn":
                    children[payload.action_id] = ChildDependencyV1(
                        session_id=payload.action_id, stateful=True, state="running"
                    )
            case ActionResultRecordV1(payload=payload):
                action = actions.get(payload.action_id)
                if action is None or action.state not in {"pending", "running"}:
                    raise ValueError("action result has no pending intent")
                actions[payload.action_id] = action.model_copy(
                    update={"state": payload.state, "result": payload.result}
                )
                if provider := provider_operations.get(payload.action_id):
                    # Only the state is worth keeping. The result here was a verbatim
                    # second copy of the Action's, and nothing reads it: `quiescent`
                    # consults `state`, and no other caller in the workspace touches a
                    # provider operation at all. At 24 KB replies the duplicate was
                    # half of a 4.44 MB runtime state that every load reparses.
                    provider_operations[payload.action_id] = provider.model_copy(
                        update={"state": payload.state}
                    )
                if child := children.get(payload.action_id):
                    children[payload.action_id] = child.model_copy(
                        update={
                            "state": (
                                "failed" if payload.state == "failed" else "completed"
                            )
                        }
                    )
            case ProcessOperationDispatchedRecordV1(payload=payload):
                action = actions.get(payload.action_id)
                if (
                    action is None
                    or action.kind != "process"
                    or action.state != "pending"
                ):
                    raise ValueError("process dispatch has no pending process Action")
                operation = _action_name(action.request)
                if operation not in {"process.start", "process.write", "process.stop"}:
                    raise ValueError(
                        "read-only process Action cannot have a dispatch marker"
                    )
                if (operation == "process.start") != (
                    payload.prepared_start is not None
                ):
                    raise ValueError(
                        "process start dispatch descriptor is missing or unexpected"
                    )
                if payload.prepared_start is not None:
                    prepared = payload.prepared_start
                    if (
                        prepared.start_action_id != action.action_id
                        or prepared.start_call_id != _action_call_id(action.request)
                        or prepared.manager_instance_id != payload.manager_instance_id
                    ):
                        raise ValueError(
                            "prepared process start disagrees with its Action"
                        )
                actions[payload.action_id] = action.model_copy(
                    update={
                        "state": "running",
                        "process_manager_instance_id": payload.manager_instance_id,
                        "process_request_sha256": payload.request_sha256,
                        "prepared_process_start": payload.prepared_start,
                    }
                )
            case ProcessStateChangedRecordV1(payload=payload):
                _apply_process_state(processes, payload.process)
            case ProcessNotificationSubmittedRecordV1(payload=payload):
                process = processes.get(payload.process_id)
                if process is None or process.status == "running":
                    raise ValueError("process notification has no terminal process")
                expected_id = _process_notification_id(payload.process_id)
                if payload.notification_id != expected_id:
                    raise ValueError("process notification has an invalid identity")
                existing = submitted_process_notifications.get(payload.process_id)
                if existing is not None and existing != payload:
                    raise ValueError("conflicting process notification acknowledgement")
                submitted_process_notifications[payload.process_id] = (
                    SubmittedProcessNotificationV1(
                        process_id=payload.process_id,
                        notification_id=payload.notification_id,
                    )
                )
            case CallbackRegisteredRecordV1(payload=payload):
                if payload.callback_id in callbacks:
                    raise ValueError("duplicate callback identity")
                callbacks[payload.callback_id] = RuntimeCallbackV1(
                    callback_id=payload.callback_id,
                    kind=payload.kind,
                    state="pending",
                    routing=payload.routing,
                )
            case CallbackResolvedRecordV1(payload=payload):
                callback = callbacks.get(payload.callback_id)
                if callback is None or callback.state != "pending":
                    raise ValueError("callback result has no pending registration")
                callbacks[payload.callback_id] = callback.model_copy(
                    update={"state": payload.state, "result": payload.result}
                )
            case ProjectionAdvancedRecordV1(payload=payload):
                if payload.watermark < current_projection.watermark:
                    raise ValueError("projection watermark moved backwards")
                current_projection = ProjectionStateV1(
                    session_id=current_projection.session_id,
                    snapshot_sequence=record.sequence,
                    watermark=payload.watermark,
                    snapshot=payload.snapshot,
                )
            case ProjectionDeltaRecordV1(payload=payload):
                if payload.watermark < current_projection.watermark:
                    raise ValueError("projection watermark moved backwards")
                current_projection = ProjectionStateV1(
                    session_id=current_projection.session_id,
                    snapshot_sequence=record.sequence,
                    watermark=payload.watermark,
                    snapshot=apply_projection_delta(
                        current_projection.snapshot, payload.delta
                    ),
                )
            case CoreInputRecordV1():
                pass
    sequence = records[-1].sequence if records else runtime.snapshot_sequence
    effective_actions = sorted(actions.values(), key=lambda item: item.action_id)
    effective_processes = sorted(processes.values(), key=lambda item: item.process_id)
    _validate_process_relationships(
        runtime.session_id, effective_actions, effective_processes
    )
    return (
        runtime.model_copy(
            update={
                "snapshot_sequence": sequence,
                "command_receipts": sorted(
                    receipts.values(), key=lambda item: item.client_command_id
                ),
                "actions": effective_actions,
                "callbacks": sorted(
                    callbacks.values(), key=lambda item: item.callback_id
                ),
                "provider_operations": sorted(
                    provider_operations.values(), key=lambda item: item.operation_id
                ),
                "processes": effective_processes,
                "submitted_process_notifications": sorted(
                    submitted_process_notifications.values(),
                    key=lambda item: item.process_id,
                ),
                "children": sorted(children.values(), key=lambda item: item.session_id),
            }
        ),
        current_projection,
    )


def _action_name(request: JsonValue) -> str | None:
    if not isinstance(request, dict):
        return None
    call = request.get("call")
    if not isinstance(call, dict):
        return None
    name = call.get("name")
    return name if isinstance(name, str) else None


def _action_call_id(request: JsonValue) -> str | None:
    if not isinstance(request, dict):
        return None
    call_id = request.get("call_id")
    return call_id if isinstance(call_id, str) else None


def _validate_process_relationships(
    session_id: str,
    actions: Iterable[RuntimeActionV1],
    processes: Iterable[ManagedProcessV1],
) -> None:
    actions_by_id = {action.action_id: action for action in actions}
    for action in actions_by_id.values():
        prepared = action.prepared_process_start
        if prepared is None:
            continue
        if prepared.process_id != expected_process_id(
            session_id, prepared.start_action_id, prepared.start_call_id
        ):
            raise ValueError("prepared process start has an invalid process ID")

    for process in processes:
        if process.process_id != expected_process_id(
            session_id, process.start_action_id, process.start_call_id
        ):
            raise ValueError("process has an invalid process ID")
        action = actions_by_id.get(process.start_action_id)
        if action is None:
            continue
        prepared = action.prepared_process_start
        if action.kind != "process" or _action_name(action.request) != "process.start":
            raise ValueError("process start Action has an invalid operation")
        if prepared is None:
            raise ValueError("process has no prepared start descriptor")
        if (
            process.process_id,
            process.start_action_id,
            process.start_call_id,
            process.manager_instance_id,
            process.command,
            process.cwd,
            process.command_environment,
            process.created_at,
        ) != (
            prepared.process_id,
            prepared.start_action_id,
            prepared.start_call_id,
            prepared.manager_instance_id,
            prepared.command,
            prepared.cwd,
            prepared.command_environment,
            prepared.created_at,
        ):
            raise ValueError("process disagrees with its prepared start descriptor")


def _apply_process_state(
    processes: dict[str, ManagedProcessV1], process: ManagedProcessV1
) -> None:
    existing = processes.get(process.process_id)
    if existing is None:
        if any(
            candidate.start_action_id == process.start_action_id
            for candidate in processes.values()
        ):
            raise ValueError("process start Action belongs to another process")
        processes[process.process_id] = process
        return
    if existing == process:
        return
    if existing.status != "running" or process.status == "running":
        raise ValueError("process terminal state cannot change")
    immutable_fields = {
        "process_id",
        "start_action_id",
        "start_call_id",
        "manager_instance_id",
        "command",
        "cwd",
        "command_environment",
        "pty_backend",
        "start_outcome",
        "start_failure_stage",
        "created_at",
        "started_at",
    }
    if any(
        getattr(existing, name) != getattr(process, name) for name in immutable_fields
    ):
        raise ValueError("process terminal state changes launch identity")
    processes[process.process_id] = process


def _process_notification_id(process_id: str) -> str:
    return f"background-process:{process_id}:terminal"


def _read_journal(path: Path, first_sequence: int) -> tuple[JournalRecordV1, ...]:
    _reject_symlink(path)
    data = path.read_bytes()
    if len(data) > _MAX_DOCUMENT_BYTES:
        raise ValueError("recovery journal is too large")
    lines = data.splitlines(keepends=True)
    records: list[JournalRecordV1] = []
    expected_sequence = first_sequence
    previous_digest: str | None = None
    for index, line in enumerate(lines):
        if not line.endswith(b"\n"):
            if index == len(lines) - 1:
                break
            raise ValueError("recovery journal has an unterminated interior record")
        raw = line[:-1]
        try:
            value = json.loads(raw)
            record = _JOURNAL_RECORD_ADAPTER.validate_python(value)
        except Exception as exc:
            raise ValueError(
                f"invalid recovery journal record {expected_sequence}"
            ) from exc
        canonical_without_digest = dict(value)
        stored_digest = canonical_without_digest.pop("record_sha256", None)
        if stored_digest != sha256_json(canonical_without_digest):
            raise ValueError("recovery journal record digest mismatch")
        if raw != canonical_json(value):
            raise ValueError("recovery journal record is not canonical JSON")
        if record.sequence != expected_sequence:
            raise ValueError("recovery journal sequence gap")
        if record.previous_record_sha256 != previous_digest:
            raise ValueError("recovery journal digest chain mismatch")
        records.append(record)
        expected_sequence += 1
        previous_digest = record.record_sha256
    return tuple(records)


def _append_journal_record(
    path: Path, record: JournalRecordV1, *, protected_bytes: int = 0
) -> int:
    _reject_symlink(path)
    data = canonical_json(record.model_dump(mode="json", by_alias=True)) + b"\n"
    with path.open("r+b", buffering=0) as file:
        file.seek(0, os.SEEK_END)
        size = file.tell()
        if size:
            file.seek(-1, os.SEEK_END)
            if file.read(1) != b"\n":
                file.seek(0)
                valid_size = file.read().rfind(b"\n") + 1
                file.truncate(valid_size)
        file.seek(0, os.SEEK_END)
        if file.tell() + len(data) + protected_bytes > _MAX_DOCUMENT_BYTES:
            raise HarnessStoreCapacityError("recovery journal is at capacity")
        file.write(data)
        os.fsync(file.fileno())
    return len(data)


def _write_generation_record(
    directory: Path, name: str, value: JsonValue, chunks: tuple[str, ...] | None = None
) -> StoredFileV1:
    data = _write_document(directory / name, value)
    return StoredFileV1(path=name, sha256=_sha256(data), chunks=chunks)


def _detach_transcript(
    document: dict[str, JsonValue], path: tuple[str, ...]
) -> tuple[dict[str, JsonValue], list[JsonValue] | None]:
    """Split a document into its envelope and the transcript list at ``path``.

    Only the nodes along ``path`` are copied, so the caller's document keeps its
    own transcript and nothing else is duplicated. A document that does not
    present the path is returned whole and stored monolithically: an unfamiliar
    shape should cost the pool's savings, not the ability to publish.
    """
    nodes: list[dict[str, JsonValue]] = [document]
    for key in path[:-1]:
        node = nodes[-1].get(key)
        if not isinstance(node, dict):
            return document, None
        nodes.append(node)
    items = nodes[-1].get(path[-1])
    if not isinstance(items, list):
        return document, None
    envelope = dict(nodes[-1])
    envelope[path[-1]] = []
    for node, key in zip(reversed(nodes[:-1]), reversed(path[:-1]), strict=True):
        parent = dict(node)
        parent[key] = envelope
        envelope = parent
    return envelope, items


def _detach_projection_history(
    projection_state: ProjectionStateV1,
) -> tuple[dict[str, JsonValue], list[JsonValue]]:
    """Split the projection into its envelope and its history, without dumping the history.

    Dumping the whole model would re-serialise every entry on every publication, which
    costs the length of the session per turn and is redundant twice over: the entries are
    ``JsonValue`` that the projector already built in wire form, and the chunk pool is
    about to discover that all but the last of them are unchanged.

    Handing the live entries to the pool is safe because ``SessionProjector`` replaces
    entries instead of mutating them -- the same invariant its snapshot diffing relies on
    (see its class docstring). A changed entry is therefore a new object, which
    ``_plan_chunks`` sees as unequal, and an unchanged one compares equal by identity.
    """
    envelope_state = projection_state.model_copy(
        update={"snapshot": _projection_envelope(projection_state.snapshot)}
    )
    envelope = cast(
        dict[str, JsonValue], envelope_state.model_dump(mode="json", by_alias=True)
    )
    return envelope, cast(
        list[JsonValue], list(projection_state.snapshot.history.entries)
    )


def _attach_transcript(
    document: JsonValue, path: tuple[str, ...], items: list[JsonValue]
) -> None:
    node = document
    for key in path[:-1]:
        if not isinstance(node, dict):
            raise ValueError("chunked document is missing its transcript container")
        node = node.get(key)
    if not isinstance(node, dict) or node.get(path[-1]) != []:
        raise ValueError("chunked document envelope must hold an empty transcript")
    node[path[-1]] = items


def _plan_chunks(items: list[JsonValue], previous: _ChunkPlan | None) -> _ChunkPlan:
    """Reuse the previous publication's sealed chunks that still match ``items``.

    Reuse is verified rather than assumed: comparing the items costs a small
    fraction of re-encoding them, which is the whole point of the pool, and it
    keeps a rewritten history from silently publishing a stale chunk.
    """
    reused: list[_Chunk] = []
    start = 0
    if previous is not None:
        for chunk in previous.chunks:
            if not chunk.sealed:
                break
            end = start + len(chunk.items)
            if end > len(items) or items[start:end] != chunk.items:
                break
            reused.append(chunk)
            start = end
    return _ChunkPlan(chunks=tuple(reused) + tuple(_split_chunks(items[start:])))


def _split_chunks(items: list[JsonValue]) -> Iterator[_Chunk]:
    pending: list[JsonValue] = []
    encoded: list[bytes] = []
    size = 0
    for item in items:
        body = canonical_json(item)
        pending.append(item)
        encoded.append(body)
        size += len(body) + 1
        if size >= _CHUNK_TARGET_BYTES:
            yield _seal_chunk(pending, encoded, sealed=True)
            pending, encoded, size = [], [], 0
    if pending:
        yield _seal_chunk(pending, encoded, sealed=False)


def _seal_chunk(
    items: list[JsonValue], encoded: list[bytes], *, sealed: bool
) -> _Chunk:
    body = _encode_chunk_body(encoded)
    return _Chunk(items=list(items), sha256=_sha256(body), sealed=sealed, body=body)


def _encode_chunk_body(encoded: list[bytes]) -> bytes:
    # Canonical JSON serializes an array positionally with no whitespace, so
    # joining canonically encoded elements is the canonical encoding of the array
    # and the elements never have to be re-encoded together.
    return b"[" + b",".join(encoded) + b"]"


def _write_chunks(chunk_root: Path, plan: _ChunkPlan | None) -> ChunkPublicationStats:
    """Publish the plan's chunks that the pool does not already hold."""
    if plan is None:
        return ChunkPublicationStats()
    stats = ChunkPublicationStats()
    for chunk in plan.chunks:
        path = chunk_root / f"{chunk.sha256}.json"
        if path.exists():
            stats += ChunkPublicationStats(reused=1)
            continue
        body = chunk.body
        if body is None:
            # A reused chunk whose file a concurrent collection removed. Rebuild
            # it rather than publish a manifest naming a chunk that is not there.
            body = _encode_chunk_body([canonical_json(item) for item in chunk.items])
            if _sha256(body) != chunk.sha256:
                raise ValueError("rebuilt chunk does not match its digest")
        _write_pooled_chunk(path, body)
        stats += ChunkPublicationStats(
            written=1, bytes_written=len(body), sealed=1 if chunk.sealed else 0
        )
    return stats


def _write_pooled_chunk(path: Path, body: bytes) -> None:
    # Content-addressed, so a concurrent publication writing the same name writes
    # the same bytes; replacing through a temporary keeps a reader from seeing a
    # partial one either way.
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.touch(mode=0o600, exist_ok=False)
        with temporary.open("wb") as file:
            file.write(body + b"\n")
            file.flush()
            os.fsync(file.fileno())
        _durable_replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_chunked_transcript(
    chunk_root: Path, digests: tuple[str, ...], cache: _ChunkCache
) -> list[JsonValue]:
    items: list[JsonValue] = []
    for digest in digests:
        # Parsed per read rather than cached: the items land in a document the
        # caller owns, and sharing them would let one load mutate another's.
        chunk = json.loads(cache.read(chunk_root, digest))
        if not isinstance(chunk, list):
            raise ValueError(f"stored chunk is not a transcript run: {digest}")
        items.extend(cast(list[JsonValue], chunk))
    return items


def _collect_chunks(session_root: Path, generation_root: Path) -> None:
    """Remove pooled chunks no retained manifest names.

    Chunks outlive the generation that introduced them, so discarding a
    generation directory does not collect them.
    """
    chunk_root = session_root / _CHUNKS_DIRNAME
    try:
        pooled = [
            path
            for path in chunk_root.iterdir()
            if _CHUNK_FILE_PATTERN.fullmatch(path.name) is not None
        ]
        generations = [
            path
            for path in generation_root.iterdir()
            if _GENERATION_PATTERN.fullmatch(path.name) is not None
        ]
    except OSError:
        return
    referenced: set[str] = set()
    for generation in generations:
        try:
            manifest = GenerationManifestV1.model_validate(
                json.loads(_read_document_body(generation / "manifest.json"))
            )
        except (OSError, ValueError):
            # A manifest that cannot be read cannot license collecting anything:
            # its chunks are unknown, so every one of them has to be kept.
            return
        referenced.update(manifest.checkpoint.chunks or ())
        referenced.update(manifest.projection_state.chunks or ())
    for path in pooled:
        # The generation is already published, so nothing here may fail the
        # commit. What survives stays unreachable and the next sweep collects it.
        if path.stem in referenced or path.is_symlink():
            continue
        try:
            path.unlink()
        except OSError:
            continue


def _write_document(path: Path, value: JsonValue) -> bytes:
    """Emit one canonical document, refusing any that would outgrow the cap.

    A Session that reaches this is in trouble either way: the refusal is not one
    operation but every publication from here on, and nothing in the Session can
    shrink the document, because the prune has already run by the time the size
    is known. What it keeps is the last generation, so the Session still reads,
    exports and deletes -- permanently read-only rather than permanently lost,
    which is what refusing the same bytes on the way back in would make it.
    """
    canonical = canonical_json(value)
    if len(canonical) + 1 > _MAX_DOCUMENT_BYTES:
        raise HarnessStoreCapacityError(
            f"stored JSON document is at capacity: {path.name}"
        )
    path.touch(mode=0o600, exist_ok=False)
    with path.open("wb") as file:
        file.write(canonical + b"\n")
        file.flush()
        os.fsync(file.fileno())
    return canonical


def _replace_document(path: Path, value: JsonValue) -> bytes:
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        body = _write_document(temporary, value) + b"\n"
        _durable_replace(temporary, path)
        _fsync_directory(path.parent)
        return body
    finally:
        if temporary.exists():
            temporary.unlink()


def _verify_published_documents(
    generation_dir: Path, manifest: GenerationManifestV1, manifest_sha256: str
) -> None:
    descriptors: list[StoredFileV1] = [
        StoredFileV1(path="manifest.json", sha256=manifest_sha256),
        manifest.checkpoint,
        manifest.runtime_state,
        manifest.projection_state,
    ]
    for descriptor in descriptors:
        body = _read_document_body(generation_dir / descriptor.path)
        if _sha256(body) != descriptor.sha256:
            raise ValueError(
                f"published document does not match its digest: {descriptor.path}"
            )


def _read_referenced_document(
    generation_dir: Path, descriptor: StoredFileV1
) -> JsonValue:
    path = generation_dir / descriptor.path
    body = _read_document_body(path)
    # Digesting the bytes as they sit on disk pins them to exactly what
    # `_write_document` emitted, and what it emitted was canonical. Parsing the
    # document and re-deriving its canonical form to digest that instead only
    # re-proves the same thing, and these are the documents that carry the whole
    # conversation, so re-deriving them dominates the cost of a load.
    if _sha256(body) != descriptor.sha256:
        raise ValueError(f"stored record digest mismatch: {descriptor.path}")
    return cast(JsonValue, json.loads(body))


def _read_document_bytes(path: Path) -> tuple[JsonValue, bytes]:
    body = _read_document_body(path)
    value = cast(JsonValue, json.loads(body))
    canonical = canonical_json(value)
    if body != canonical:
        raise ValueError(f"stored JSON document is not canonical: {path.name}")
    return value, canonical


def _read_document_body(path: Path) -> bytes:
    # No size limit: publication enforces the cap, and a generation an earlier
    # build grew past it is recovered or deleted only by reading it.
    _reject_symlink(path)
    data = path.read_bytes()
    if not data.endswith(b"\n"):
        raise ValueError(f"stored JSON document is not newline terminated: {path.name}")
    return data[:-1]


def _create_empty_file(path: Path) -> None:
    _reject_symlink(path)
    if path.exists():
        return
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.touch(mode=0o600, exist_ok=False)
        with temporary.open("r+b") as file:
            os.fsync(file.fileno())
        _durable_replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _record_recovery_in_history(
    projection: ProjectionStateV1, *, discarded: int
) -> ProjectionStateV1:
    """Leave a mark in the conversation where a repair cut it short.

    A journal can hold many turns, and a repair drops every entry after the
    record that no longer replays. Without an entry saying so, the only account
    of the missing work is a log line the person reading the conversation will
    never see.
    """
    if discarded <= 0:
        return projection
    snapshot = projection.snapshot
    now = int(datetime.now(UTC).timestamp() * 1000)
    entry: JsonObject = {
        "type": "checkpoint",
        "id": f"checkpoint-recovered-{projection.snapshot_sequence}",
        "sessionId": projection.session_id,
        "turnId": None,
        "createdAt": now,
        "updatedAt": now,
        "generationStatus": "completed",
        "relatedEntryId": None,
        "kind": "recovered",
        "message": "Recovered after an interrupted session",
        "details": {"discardedEntries": discarded},
    }
    history = snapshot.history.model_copy(
        update={"entries": [*snapshot.history.entries, entry]}
    )
    return projection.model_copy(
        update={"snapshot": snapshot.model_copy(update={"history": history})}
    )


def _empty_journal_segment(path: Path) -> None:
    """Replace a segment with an empty one, atomically.

    A truncation in place would be visible half-done to a reader that opened the
    file first; the replacement is never observed as anything but the old
    contents or none.
    """
    _reject_symlink(path)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.touch(mode=0o600, exist_ok=False)
        with temporary.open("r+b") as file:
            os.fsync(file.fileno())
        _durable_replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _fsync_directory(path: Path) -> None:
    if _is_windows():
        # Windows directory handles do not support the POSIX fsync protocol.
        # Every metadata publication in this store uses MoveFileExW with
        # MOVEFILE_WRITE_THROUGH instead.
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"stored path cannot be a symbolic link: {path}")


def _reject_symlink_components(root: Path, path: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"stored path escapes its configured root: {path}") from exc
    current = root
    _reject_symlink(current)
    for part in relative.parts:
        current /= part
        _reject_symlink(current)


def _discard_superseded(generation_root: Path, journal_root: Path) -> None:
    _discard_all_but_newest(generation_root, _GENERATION_PATTERN, shutil.rmtree)
    _discard_all_but_newest(journal_root, _JOURNAL_SEGMENT_PATTERN, Path.unlink)


def _discard_all_but_newest(
    directory: Path, pattern: re.Pattern[str], discard: Callable[[Path], object]
) -> None:
    # The generation is already published, so nothing here may fail the commit.
    # What survives an error stays unreachable and the next publication collects it.
    try:
        entries = sorted(
            (
                path
                for path in directory.iterdir()
                if pattern.fullmatch(path.name) is not None
            ),
            key=lambda path: path.name,
        )
    except OSError:
        return
    for path in entries[:-_RETAINED_GENERATIONS]:
        # Never collect through a link out of the store; `load` rejects one anyway.
        if path.is_symlink():
            continue
        try:
            discard(path)
        except OSError:
            continue


def _next_generation(generation_root: Path, snapshot_sequence: int) -> str:
    existing = (
        [
            int(path.name)
            for path in generation_root.iterdir()
            if _GENERATION_PATTERN.fullmatch(path.name) is not None
        ]
        if generation_root.exists()
        else []
    )
    generation = max(snapshot_sequence, max(existing, default=-1) + 1)
    if generation > _MAX_GENERATION:
        raise ValueError("Unified session generation is exhausted")
    return f"{generation:016d}"


@contextmanager
def _lease_directory_lock(directory: Path) -> Iterator[None]:
    registry = directory / ".registry"
    registry.touch(mode=0o600, exist_ok=True)
    file = registry.open("a+b")
    try:
        _acquire_file_lock(file, blocking=True)
    except BaseException:
        file.close()
        raise
    try:
        yield
    finally:
        _release_file_lock(file)
        file.close()


def _acquire_file_lock(file: Any, *, blocking: bool = False) -> None:
    if _is_windows():
        msvcrt = cast(Any, __import__("msvcrt"))

        # Check the size, not a read, and write through the raw descriptor:
        # a read or buffered write of the locked byte raises a raw
        # PermissionError that must not escape this except.
        descriptor = file.fileno()
        try:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
        except PermissionError:
            # Byte 0 is locked by another handle; skip the seed and let the
            # lock call below report the contention.
            pass
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            msvcrt.locking(file.fileno(), mode, 1)
        except OSError as exc:
            raise BlockingIOError from exc
        return
    import fcntl

    try:
        operation = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(file.fileno(), operation)
    except OSError as exc:
        raise BlockingIOError from exc


def _release_file_lock(file: Any) -> None:
    if _is_windows():
        msvcrt = cast(Any, __import__("msvcrt"))

        # Raw lseek, matching _acquire_file_lock.
        os.lseek(file.fileno(), 0, os.SEEK_SET)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(file.fileno(), fcntl.LOCK_UN)


def _durable_replace(source: Path, target: Path) -> None:
    if not _is_windows():
        os.replace(source, target)
        return
    _windows_replace(source, target)


def _windows_replace(source: Path, target: Path) -> None:
    import ctypes
    from ctypes import wintypes

    windows_ctypes = cast(Any, ctypes)
    move_file_ex = windows_ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move_file_ex.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move_file_ex.restype = wintypes.BOOL
    movefile_replace_existing = 0x1
    movefile_write_through = 0x8
    if not move_file_ex(
        str(source), str(target), movefile_replace_existing | movefile_write_through
    ):
        raise windows_ctypes.WinError(windows_ctypes.get_last_error())


def _is_windows() -> bool:
    return os.name == "nt"


def _validate_session_id(session_id: str) -> None:
    if _SESSION_ID_PATTERN.fullmatch(session_id) is None:
        raise ValueError(f"invalid session ID: {session_id!r}")


def _require_sorted_unique[ItemT](
    items: Iterable[ItemT], key: Any, description: str
) -> None:
    values = [key(item) for item in items]
    if values != sorted(values) or len(values) != len(set(values)):
        raise ValueError(f"{description} values must be sorted and unique")


def _require_unique[ItemT](items: Iterable[ItemT], key: Any, description: str) -> None:
    values = [key(item) for item in items]
    if len(values) != len(set(values)):
        raise ValueError(f"{description} values must be unique")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


__all__ = [
    "ActionIntentPayloadV1",
    "ActionResultPayloadV1",
    "AppendEntryOp",
    "CallbackRegisteredPayloadV1",
    "CallbackResolvedPayloadV1",
    "CommandReservation",
    "CommittedHistoryV1",
    "GenerationManifestV1",
    "HarnessStoreCapacityError",
    "ImportProvenanceV1",
    "InteropAssistantContentPartV1",
    "InteropAssistantMessageV1",
    "InteropContentBlockV1",
    "InteropFileImageFallbackV1",
    "InteropHistoryMessageV1",
    "InteropImageContentBlockV1",
    "InteropSystemMessageV1",
    "InteropToolMessageV1",
    "InteropUserMessageV1",
    "LegacyInteropSourceV1",
    "ManagedProcessV1",
    "PendingInternalCommand",
    "PluginLockEntryV1",
    "PluginLockV1",
    "PreparedProcessStartV1",
    "ProcessNotificationSubmittedPayloadV1",
    "ProcessOperationDispatchedPayloadV1",
    "ProcessStateChangedPayloadV1",
    "ProjectionAdvancedPayloadV1",
    "ProjectionDelta",
    "ProjectionDeltaPayloadV1",
    "ProjectionOp",
    "ProjectionStateV1",
    "RemoveEntryOp",
    "ReplaceEntryOp",
    "RuntimeActionV1",
    "RuntimeStateV3",
    "SessionLease",
    "SessionMetadataV1",
    "SessionPin",
    "SetEnvelopeOp",
    "SetHistoryEntriesOp",
    "StoredSession",
    "SubmittedProcessNotificationV1",
    "UnifiedInteropSourceV1",
    "UnifiedSessionStore",
    "apply_projection_delta",
    "canonical_json",
    "committed_history",
    "compute_projection_delta",
    "empty_runtime_state",
    "sha256_json",
    "transition_sha256",
]
