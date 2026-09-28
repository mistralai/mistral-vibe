from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
import logging
from pathlib import Path
import secrets
import time

from mistralai_vibe_local_harness.protocol import (
    RustAcceptCandidate,
    RustAction,
    RustCompletionFailedEvent,
    RustCompletionHookAccept,
    RustCompletionHookInput,
    RustCompletionHookRetry,
    RustContentBlock,
    RustEvent,
    RustExternalToolCall,
    RustFilesystemAction,
    RustFilesystemFailedEvent,
    RustFilesystemSucceededEvent,
    RustFilesystemWriteResult,
    RustHarnessHookBinding,
    RustHookCallActionBase,
    RustHookCompletedEvent,
    RustHookFailedEvent,
    RustHookSkip,
    RustHookToolCall,
    RustLLMCallAction,
    RustMessage,
    RustModelMessageAppend,
    RustModelMessageReplace,
    RustModelToolCatalogKeep,
    RustModelToolCatalogReplace,
    RustPostAgentTurnHookAction,
    RustPostAgentTurnHookResult,
    RustPostToolCallHookAction,
    RustPostToolCallHookInput,
    RustPostToolCallHookResult,
    RustPostToolCallOutput,
    RustPreAgentTurnContinue,
    RustPreAgentTurnHookAction,
    RustPreAgentTurnHookInput,
    RustPreAgentTurnHookResult,
    RustPreToolCallContinue,
    RustPreToolCallHookAction,
    RustPreToolCallHookInput,
    RustPreToolCallHookResult,
    RustProtocolError,
    RustProvidedToolCall,
    RustProvidedToolCallAction,
    RustReplaceAssistantContent,
    RustRuntimeBuiltinToolCall,
    RustRuntimeBuiltinToolCallAction,
    RustToolDefinition,
    RustToolFailedEvent,
    RustToolFailureResult,
    RustToolSucceededEvent,
)
from mistralai_vibe_local_harness.session_protocol import JsonObject
from mistralai_vibe_local_harness.vibe._completion import execute_completion
from mistralai_vibe_local_harness.vibe._file_tools import execute_file_tool
from mistralai_vibe_local_harness.vibe._hook_matcher import qualified_tool_name
from mistralai_vibe_local_harness.vibe._permissions import ALWAYS_ASK, PermissionOutcome
from mistralai_vibe_local_harness.vibe._projection import (
    APPROVAL_META_KEY,
    APPROVAL_NOTE_META_KEY,
    public_notice_entry,
)
from mistralai_vibe_local_harness.vibe._protected_paths import protected_target
from mistralai_vibe_local_harness.vibe._runtime import ActionExecutor
from mistralai_vibe_local_harness.vibe._runtime_config import (
    CompletionDeltaSink,
    LocalRuntimeAdapterConfig,
    ProviderRetry,
    ToolApprovalMode,
)
from mistralai_vibe_local_harness.vibe._self_tools import execute_self_tool
from mistralai_vibe_local_harness.vibe._shell_tools import execute_shell_tool
from mistralai_vibe_local_harness.vibe._skill_tools import execute_skill_tool
from mistralai_vibe_local_harness.vibe._smart_approve import (
    CLASSIFIER_PROMPT_VERSION,
    DEFAULT_SMART_APPROVE_MODEL,
    ClassificationTier,
    ClassificationVerdict,
    ClassifierFactory,
    RiskClassificationResult,
    build_classification_request,
    classify_tool_call,
)

logger = logging.getLogger(__name__)


class ApprovalGrant(StrEnum):
    """How a human resolved an approval request.

    The values match the app-server decision types so the session layer hands a
    resolution straight through. ``SESSION``/``ALWAYS`` let the classifier gate
    remember the call so an identical one is not re-prompted this session.
    """

    ONCE = "approve"
    SESSION = "approve_for_session"
    ALWAYS = "approve_permanently"
    DENY = "deny"

    @property
    def approved(self) -> bool:
        return self is not ApprovalGrant.DENY

    @property
    def remembered(self) -> bool:
        return self in {ApprovalGrant.SESSION, ApprovalGrant.ALWAYS}


# A tool call the approval/classify gates act on: a Runtime builtin or a provided/MCP
# tool. Both carry the ids and a ``call`` the classifier and approval dialog read.
type GatedToolAction = RustRuntimeBuiltinToolCallAction | RustProvidedToolCallAction


def gated_tool_name(action: GatedToolAction) -> str:
    if isinstance(action, RustProvidedToolCallAction):
        return f"{action.call.group_name}.{action.call.tool_name}"
    return action.call.name


# The optional reason explains why approval is being asked (e.g. smart approve's risk
# reason); it is shown in the approval dialog and is None for the static per-tool gate.
type ApprovalRequester = Callable[
    [GatedToolAction, str | None, tuple[JsonObject, ...]], Awaitable[ApprovalGrant]
]

type HookApprovalCallback = Callable[[RustHookToolCall, str | None], Awaitable[bool]]

# Smart approve publishes one classification event per gated call onto the session
# event stream; the app-server intercepts it and forwards it to product analytics.
# It is a telemetry side-channel: it never changes the tool outcome.
type ClassificationSink = Callable[[JsonObject], None]

CLASSIFICATION_EVENT_TYPE = "tool_classification"


@dataclass(frozen=True, slots=True)
class HookNoticeData:
    """A user hook's activity, projected to a ``notice`` history entry."""

    kind: str
    scope: str
    tool_call_id: str | None = None
    tool_name: str | None = None
    hook_name: str | None = None
    status: str | None = None
    content: str | None = None


type HookNoticeEmitter = Callable[[HookNoticeData], Awaitable[None]]

# A pre_tool_call hook can annotate why it let a call through (e.g. smart approve's
# auto-approval reason); the note rides the tool's result _meta to the UI, keyed by the
# firing action's id. It never enters the model-visible result.
type ApprovalNoteRecorder = Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class HookContext:
    """The Runtime-owned handle passed to a hook body."""

    config: LocalRuntimeAdapterConfig
    messages: tuple[RustMessage, ...]
    session_id: str = ""
    request_approval: HookApprovalCallback | None = None
    emit_hook_notice: HookNoticeEmitter | None = None
    record_approval_note: ApprovalNoteRecorder | None = None


# On a ``pre_tool_call`` ``continue``, ``effective_arguments`` is the full replacement the
# tool runs with, so a handler that leaves the call unchanged echoes the incoming arguments.
type PreToolCallHookHandler = Callable[
    [RustPreToolCallHookInput, HookContext], Awaitable[RustPreToolCallHookResult]
]
type PreAgentTurnHookHandler = Callable[
    [RustPreAgentTurnHookInput, HookContext], Awaitable[RustPreAgentTurnHookResult]
]
# post_tool_call handlers chain: each sees the prior handler's (possibly rewritten) result.
type PostToolCallHookHandler = Callable[
    [RustPostToolCallHookInput, HookContext], Awaitable[RustPostToolCallHookResult]
]
type PostAgentTurnHookHandler = Callable[
    [RustCompletionHookInput, HookContext], Awaitable[RustPostAgentTurnHookResult]
]


@dataclass(frozen=True, slots=True)
class ToolScopedHook[I, R]:
    # Tool points bind an ``always`` selector, so the Core dispatches every one of them on
    # every tool call and ``selects`` decides here (see ``_select_for_tool``). A handler
    # registered as a plain callable carries no ``match`` and always fires.
    run: Callable[[I, HookContext], Awaitable[R]]
    selects: Callable[[str], bool]

    async def __call__(self, hook_input: I, context: HookContext) -> R:
        return await self.run(hook_input, context)


# Bound post_agent retries per turn so a hook that always retries cannot wedge the turn;
# past the cap we force-accept.
_MAX_POST_AGENT_RETRIES = 3

# Smart approve deny-and-continue policy: a risky verdict the user did not ask for
# returns the reason to the model to self-correct rather than interrupting the human.
# It escalates instead when the action is user-authorized, on a repeat of a call
# already denied this session, or once a denial streak or per-turn total is hit.
# Both counters are per turn (ADR 0017); what carries across turns is the denied-key
# set, which is scoped to the action rather than to unrelated later work.
_SMART_DENY_CONSECUTIVE_LIMIT = 3
_SMART_DENY_TOTAL_LIMIT = 20
_SMART_DENY_CONTINUE_HINT = (
    " If a genuinely safer alternative exists, try it. If none does, repeat this "
    "action -- a repeat asks the user to confirm it, rather than being blocked again."
)

# Terminal outcome of a classified call, carried on the telemetry event. The
# ``escalated_*`` values are the ones that reached a human (the approval-rejection
# metric); ``escalated`` in the payload derives from this prefix.
_AUTO_APPROVED = "auto_approved"
_DENY_AND_CONTINUE = "deny_and_continue"
_ESCALATED_APPROVED = "escalated_approved"
_ESCALATED_DENIED = "escalated_denied"
_ESCALATED_HEADLESS_DENIED = "escalated_headless_denied"


@dataclass(frozen=True, slots=True)
class HookHandlers:
    """Runtime hook bodies keyed by binding ID, grouped by hook point.

    Per-session isolation comes from which bindings the Core carries, not from which
    handlers the Runtime knows: a session whose Core has no matching binding never has its
    handlers consulted.
    """

    pre_tool_call: Mapping[str, PreToolCallHookHandler] = field(default_factory=dict)
    pre_agent_turn: Mapping[str, PreAgentTurnHookHandler] = field(default_factory=dict)
    post_tool_call: Mapping[str, PostToolCallHookHandler] = field(default_factory=dict)
    post_agent_turn: Mapping[str, PostAgentTurnHookHandler] = field(
        default_factory=dict
    )


def merge_hook_handlers(builtins: HookHandlers, foreign: HookHandlers) -> HookHandlers:
    """Merge Host-global builtin handlers with a session's foreign (user) handlers.

    Builtin ids win on the defensive off-chance of a clash, so a user hook cannot shadow a
    policy builtin.
    """
    return HookHandlers(
        pre_tool_call={**foreign.pre_tool_call, **builtins.pre_tool_call},
        pre_agent_turn={**foreign.pre_agent_turn, **builtins.pre_agent_turn},
        post_tool_call={**foreign.post_tool_call, **builtins.post_tool_call},
        post_agent_turn={**foreign.post_agent_turn, **builtins.post_agent_turn},
    )


type PublicHistoryEntrySink = Callable[[list[JsonObject]], Awaitable[None]]


def foreign_binding_ids(handlers: HookHandlers) -> frozenset[str]:
    """Binding ids of user (foreign) hooks. Run-boundary notices fire only for these, so a
    builtin-only hook point never adds an empty hook container to the public history.
    """
    return (
        frozenset(handlers.pre_tool_call)
        | frozenset(handlers.pre_agent_turn)
        | frozenset(handlers.post_tool_call)
        | frozenset(handlers.post_agent_turn)
    )


def has_hook_handlers(handlers: HookHandlers) -> bool:
    """Whether any hook point has at least one bound handler.

    Used to decide whether a session needs the local action executor even when it has no
    adapter config or provided-tool executor: a Host that only registers hook handlers
    still needs the executor installed for Core-selected hook actions to run.
    """
    return bool(
        handlers.pre_tool_call
        or handlers.pre_agent_turn
        or handlers.post_tool_call
        or handlers.post_agent_turn
    )


def streams_provisional_content(
    handlers: HookHandlers, hook_bindings: Sequence[RustHarnessHookBinding]
) -> bool:
    """Whether a session may project completion content before the Core commits it.

    A post-agent-turn hook can replace or reject the committed candidate, and the Step
    Protocol forbids projecting output a later stage can still replace -- showing text
    and then retracting it is worse than showing it late.

    Scoped to the bindings this session's Core actually carries, not to the registry:
    handlers are Host-global, so testing those alone would stop every session streaming
    as soon as one of them wants the hook.
    """
    return not any(
        binding.point == "post_agent_turn" and binding.id in handlers.post_agent_turn
        for binding in hook_bindings
    )


type ProvidedToolExecutor = Callable[[RustProvidedToolCallAction], Awaitable[RustEvent]]

# Called once per session with its id, so its executor can hold per-session state.
type ProvidedToolExecutorFactory = Callable[[str], ProvidedToolExecutor]

# One bar for a whole group, or one per tool name for a group whose tools do not
# share one. A name the mapping omits falls back to ``config.provided_tool_mode``.
type ProvidedToolApproval = ToolApprovalMode | Mapping[str, ToolApprovalMode]

# Never gated, whatever the configured mode: asking the user is the escalation
# channel itself, so gating it would mean an approval dialog just to ask. Per tool
# rather than per group, so it does not widen if ``ui`` gains a second tool.
_UNGATED_PROVIDED_TOOLS = frozenset({("ui", "ask_user_question")})


def build_local_action_executor(  # noqa: PLR0913 - explicit executor dependencies
    config: LocalRuntimeAdapterConfig,
    request_approval: ApprovalRequester | None = None,
    provided_tool_executor: ProvidedToolExecutor | None = None,
    *,
    filesystem_root: Path | None = None,
    hook_handlers: HookHandlers | None = None,
    session_id: str = "",
    notice_sink: PublicHistoryEntrySink | None = None,
    foreign_binding_ids: frozenset[str] = frozenset(),
    classifier_factory: ClassifierFactory | None = None,
    classify_model: str = DEFAULT_SMART_APPROVE_MODEL,
    provided_tool_modes: Mapping[str, ProvidedToolApproval] | None = None,
) -> ActionExecutor:
    state = _LocalActionState(
        config,
        request_approval,
        provided_tool_executor,
        hook_handlers or HookHandlers(),
        session_id,
        filesystem_root=filesystem_root,
        foreign_binding_ids=foreign_binding_ids,
        classifier_factory=classifier_factory,
        classify_model=classify_model,
        provided_tool_modes=provided_tool_modes,
    )
    if notice_sink is not None:
        state.bind_notice_sink(notice_sink)
    return state.execute


class _LocalActionState:
    def __init__(
        self,
        config: LocalRuntimeAdapterConfig,
        request_approval: ApprovalRequester | None,
        provided_tool_executor: ProvidedToolExecutor | None,
        hook_handlers: HookHandlers,
        session_id: str = "",
        *,
        filesystem_root: Path | None = None,
        foreign_binding_ids: frozenset[str] = frozenset(),
        classifier_factory: ClassifierFactory | None = None,
        classify_model: str = DEFAULT_SMART_APPROVE_MODEL,
        provided_tool_modes: Mapping[str, ProvidedToolApproval] | None = None,
    ) -> None:
        self._config = config
        self._request_approval = request_approval
        self._provided_tool_executor = provided_tool_executor
        # Frozen for the session: unlike ``tool_modes``, these do not follow an
        # ``apply_adapter_config`` mode switch.
        self._provided_tool_modes: Mapping[str, ProvidedToolApproval] = (
            provided_tool_modes or {}
        )
        self._hook_handlers = hook_handlers
        self._session_id = session_id
        self._filesystem_root = (
            filesystem_root.expanduser().resolve()
            if filesystem_root is not None
            else None
        )
        # The path a file call was cleared for by the permission resolver (main's
        # out-of-workspace grant handoff); set during gating, read by the file tool.
        self._resolved_authorized_path: Path | None = None
        self._foreign_binding_ids = foreign_binding_ids
        self._notice_sink: PublicHistoryEntrySink | None = None
        self._messages: list[RustMessage] = []
        self._tools: list[RustToolDefinition] = []
        self._message_revision: int | None = None
        self._tool_revision: int | None = None
        # post_agent retry counts keyed by turn_id; bounds a retrying hook per turn.
        self._post_agent_retries: dict[str, int] = {}
        # Approval notes recorded when a tool is auto-approved or human-approved,
        # keyed by the tool action_id; consumed when that builtin tool runs (rides
        # its result _meta to the UI).
        self._approval_notes: dict[str, str] = {}
        self._approval_meta: dict[str, tuple[str, str, str]] = {}
        # Smart-approve ("classify" tool mode) state, per session.
        self._classifier_factory = classifier_factory
        self._classify_model = classify_model
        # (tool_name, args_hash) the human approved for the session, so an identical
        # classified call is not re-prompted.
        self._session_approvals: set[tuple[str, str]] = set()
        # Provided/MCP tools have no per-call permission patterns to scope a grant,
        # so "approve for session" on one is remembered at the tool level: the human
        # authorized the tool, not one exact argument set. Builtins keep the
        # args-scoped grant above because their resolver patterns carry the scope.
        self._session_approved_tools: set[str] = set()
        # Both per turn; the streak also clears whenever a call proceeds.
        self._smart_turn_id: str | None = None
        self._smart_deny_consecutive = 0
        self._smart_deny_total = 0
        # Exact ``(tool_name, args_hash)`` keys denied earlier in this session. A
        # byte-identical re-ask escalates rather than being denied a second time, so
        # a model with no safer alternative reaches the human instead of looping.
        # Kept session-wide, not per turn, so a re-ask in a later turn still lands.
        self._smart_denied_keys: set[tuple[str, str]] = set()
        self._classification_sink: ClassificationSink | None = None
        self._retry_sink: (
            Callable[[str, ProviderRetry | None], Awaitable[None]] | None
        ) = None
        self._delta_sink: CompletionDeltaSink | None = None

    def configure(self, config: LocalRuntimeAdapterConfig) -> None:
        self._config = config

    def bind_approval_requester(self, request_approval: ApprovalRequester) -> None:
        self._request_approval = request_approval

    async def _resolve_permission(self, action: GatedToolAction) -> PermissionOutcome:
        resolver = self._config.permission_resolver
        if resolver is None:
            return ALWAYS_ASK
        try:
            return await resolver(gated_tool_name(action), action.call.arguments)
        except Exception:  # a broken resolver must still prompt
            logger.warning(
                "Permission resolver failed for %s; falling back to asking",
                gated_tool_name(action),
                exc_info=True,
            )
            return ALWAYS_ASK

    def bind_notice_sink(self, sink: PublicHistoryEntrySink) -> None:
        self._notice_sink = sink

    def bind_classification_sink(self, sink: ClassificationSink) -> None:
        self._classification_sink = sink

    def bind_retry_sink(
        self, sink: Callable[[str, ProviderRetry | None], Awaitable[None]]
    ) -> None:
        self._retry_sink = sink

    def bind_completion_delta_sink(self, sink: CompletionDeltaSink) -> None:
        self._delta_sink = sink

    async def _emit_notice(self, notice: HookNoticeData) -> None:
        # Notices are a UI side-channel: a sink failure must never change a hook's outcome
        # (allow/deny/rewrite) or the Core recovery path. Swallow and log any error.
        sink = self._notice_sink
        if sink is None:
            return
        try:
            entry = public_notice_entry(
                self._session_id,
                f"hook-notice-{secrets.token_hex(8)}",
                kind=notice.kind,
                scope=notice.scope,
                observed_at=time.time_ns() // 1_000_000,
                tool_call_id=notice.tool_call_id,
                tool_name=notice.tool_name,
                hook_name=notice.hook_name,
                status=notice.status,
                content=notice.content,
            )
            await sink([entry])
        except Exception:
            logger.warning("Failed to emit hook notice %s", notice.kind, exc_info=True)

    def _emits_run_notices(self, binding_ids: Sequence[str]) -> bool:
        """A run's container is emitted only when a foreign hook is in the batch."""
        return (
            self._notice_sink is not None
            and not self._foreign_binding_ids.isdisjoint(binding_ids)
        )

    def _provided_tool_mode(self, call: RustProvidedToolCall) -> ToolApprovalMode:
        if (call.group_name, call.tool_name) in _UNGATED_PROVIDED_TOOLS:
            return "allow"
        approval = self._provided_tool_modes.get(call.group_name)
        if isinstance(approval, Mapping):
            approval = approval.get(call.tool_name)
        return approval or self._config.provided_tool_mode

    async def execute(self, action: RustAction) -> RustEvent:  # noqa: PLR0911 - one return per action kind
        if isinstance(action, RustLLMCallAction):
            self._apply_model_input(action)
            return await execute_completion(
                action,
                self._messages,
                self._tools,
                self._config,
                retry_sink=self._retry_sink,
                delta_sink=self._delta_sink,
            )
        if (
            isinstance(action, RustProvidedToolCallAction)
            and self._provided_tool_executor is not None
        ):
            denial = await self._gate_tool_call(
                action, self._provided_tool_mode(action.call)
            )
            if denial is not None:
                return self._with_approval_note(denial, action.action_id)
            return self._with_approval_note(
                await self._provided_tool_executor(action), action.action_id
            )
        if isinstance(action, RustRuntimeBuiltinToolCallAction):
            denial = await self._gate_tool_call(
                action, self._config.tool_modes.get(action.call.name, "allow")
            )
            if denial is not None:
                return self._with_approval_note(denial, action.action_id)
            if action.call.name in {
                "file_system.read_file",
                "file_system.write_file",
                "file_system.search_replace",
            }:
                additional_read_roots = (
                    (
                        self._filesystem_root / "attachments",
                        self._filesystem_root / "tool-results",
                    )
                    if self._filesystem_root is not None
                    else ()
                )
                return self._with_approval_note(
                    await execute_file_tool(
                        action,
                        self._config,
                        additional_read_roots=additional_read_roots,
                        authorized_path=self._resolved_authorized_path,
                    ),
                    action.action_id,
                )
            if action.call.name == "file_system.bash":
                return self._with_approval_note(
                    await execute_shell_tool(action, self._config), action.action_id
                )
            if action.call.name == "skill.read":
                return self._with_approval_note(
                    await execute_skill_tool(action, self._config), action.action_id
                )
            if action.call.name == "self.sleep":
                return await execute_self_tool(action)
        if isinstance(action, RustFilesystemAction):
            return await self._write_file(action)
        if isinstance(action, RustPreToolCallHookAction):
            return await self._execute_pre_tool_call_hook(action)
        if isinstance(action, RustPreAgentTurnHookAction):
            return await self._execute_pre_agent_turn_hook(action)
        if isinstance(action, RustPostToolCallHookAction):
            return await self._execute_post_tool_call_hook(action)
        if isinstance(action, RustPostAgentTurnHookAction):
            return await self._execute_post_agent_turn_hook(action)
        return _unsupported_action(action)

    async def _write_file(self, action: RustFilesystemAction) -> RustEvent:
        try:
            relative_path = Path(action.operation.workspace_path)
            if relative_path.is_absolute():
                raise ValueError("filesystem write path must be relative")
            workspace = self._filesystem_root or self._config.cwd.expanduser().resolve()
            destination = (workspace / relative_path).resolve()
            if not destination.is_relative_to(workspace):
                raise ValueError("filesystem write path resolves outside the workspace")
            # Filesystem actions can be restored from durable state. Keep a
            # malformed action away from checkpoints and other session metadata.
            tool_results_root = (workspace / "tool-results").resolve()
            if not destination.is_relative_to(tool_results_root):
                raise ValueError(
                    "filesystem write path must resolve under tool-results"
                )
            await asyncio.to_thread(
                _replace_text_file, destination, action.operation.content
            )
            return RustFilesystemSucceededEvent(
                action_id=action.action_id,
                result=RustFilesystemWriteResult(model_path=str(destination)),
            )
        except Exception as error:
            return RustFilesystemFailedEvent(
                action_id=action.action_id,
                error=RustProtocolError(
                    code="filesystem_write_failed",
                    message=str(error),
                    retryable=False,
                    details=None,
                ),
            )

    async def _execute_pre_tool_call_hook(
        self, action: RustPreToolCallHookAction
    ) -> RustEvent:
        try:
            selected = _select_for_tool(
                _resolve_hooks(
                    action.hook_binding_ids, self._hook_handlers.pre_tool_call
                ),
                action.input.tool_call.call,
            )
        except Exception as error:
            return _hook_pipeline_failed(action, error)
        emit_run = self._emits_run_notices([bid for bid, _ in selected])
        call_id = action.input.tool_call.call_id
        if emit_run:
            await self._emit_notice(
                HookNoticeData(
                    kind="hook_run_started", scope="pre_tool", tool_call_id=call_id
                )
            )
        try:
            context = self._pre_tool_call_context(action.turn_id)
            tool_call = action.input.tool_call
            for _, handler in selected:
                result = await handler(
                    RustPreToolCallHookInput(tool_call=tool_call), context
                )
                output = result.output
                if isinstance(output, RustHookSkip):
                    # A deny short-circuits: no later hook can un-skip the call, and the
                    # skip reason is the model-visible tool result.
                    return RustHookCompletedEvent(
                        action_id=action.action_id,
                        result=RustPreToolCallHookResult(output=output),
                    )
                tool_call = _with_arguments(tool_call, output.effective_arguments)
        except Exception as error:
            return _hook_pipeline_failed(action, error)
        else:
            return RustHookCompletedEvent(
                action_id=action.action_id,
                result=RustPreToolCallHookResult(
                    output=RustPreToolCallContinue(
                        effective_arguments=tool_call.call.arguments
                    )
                ),
            )
        finally:
            if emit_run:
                await self._emit_notice(
                    HookNoticeData(
                        kind="hook_run_completed",
                        scope="pre_tool",
                        tool_call_id=call_id,
                    )
                )

    async def _execute_pre_agent_turn_hook(
        self, action: RustPreAgentTurnHookAction
    ) -> RustEvent:
        try:
            context = self._hook_context()
            handlers = _resolve_hooks(
                action.hook_binding_ids, self._hook_handlers.pre_agent_turn
            )
            user_content = action.input.user_content
            for _, handler in handlers:
                result = await handler(
                    RustPreAgentTurnHookInput(user_content=user_content), context
                )
                output = result.output
                if isinstance(output, RustHookSkip):
                    return RustHookCompletedEvent(
                        action_id=action.action_id,
                        result=RustPreAgentTurnHookResult(output=output),
                    )
                user_content = output.user_content
        except Exception as error:
            return _hook_pipeline_failed(action, error)
        return RustHookCompletedEvent(
            action_id=action.action_id,
            result=RustPreAgentTurnHookResult(
                output=RustPreAgentTurnContinue(user_content=user_content)
            ),
        )

    async def _execute_post_tool_call_hook(
        self, action: RustPostToolCallHookAction
    ) -> RustEvent:
        # post_tool hooks chain and cannot skip -- the tool already ran; each only rewrites
        # the model-visible result.
        try:
            selected = _select_for_tool(
                _resolve_hooks(
                    action.hook_binding_ids, self._hook_handlers.post_tool_call
                ),
                action.input.tool_call.call,
            )
        except Exception as error:
            return _hook_pipeline_failed(action, error)
        emit_run = self._emits_run_notices([bid for bid, _ in selected])
        call_id = action.input.tool_call.call_id
        if emit_run:
            await self._emit_notice(
                HookNoticeData(
                    kind="hook_run_started", scope="post_tool", tool_call_id=call_id
                )
            )
        try:
            context = self._hook_context()
            tool_call = action.input.tool_call
            tool_result = action.input.tool_result
            for _, handler in selected:
                result = await handler(
                    RustPostToolCallHookInput(
                        tool_call=tool_call, tool_result=tool_result
                    ),
                    context,
                )
                tool_result = result.output.tool_result
        except Exception as error:
            return _hook_pipeline_failed(action, error)
        else:
            return RustHookCompletedEvent(
                action_id=action.action_id,
                result=RustPostToolCallHookResult(
                    output=RustPostToolCallOutput(tool_result=tool_result)
                ),
            )
        finally:
            if emit_run:
                await self._emit_notice(
                    HookNoticeData(
                        kind="hook_run_completed",
                        scope="post_tool",
                        tool_call_id=call_id,
                    )
                )

    async def _execute_post_agent_turn_hook(
        self, action: RustPostAgentTurnHookAction
    ) -> RustEvent:
        # The first non-accept decision (retry or reject) is the turn's outcome and
        # short-circuits the rest; among accepts, a later content replacement wins.
        emit_run = self._emits_run_notices(action.hook_binding_ids)
        if emit_run:
            await self._emit_notice(
                HookNoticeData(kind="hook_run_started", scope="post_agent")
            )
        try:
            context = self._hook_context()
            handlers = _resolve_hooks(
                action.hook_binding_ids, self._hook_handlers.post_agent_turn
            )
            candidate = action.input.candidate
            replacement: list[RustContentBlock] | None = None
            # Each hook reviews the model's original completion, not a prior hook's
            # replacement: a candidate carries assistant parts while a replacement is
            # content blocks, so it cannot be fed back into the next hook's input without a
            # lossy, protocol-undefined conversion. Among accepts, the last replacement wins.
            for _, handler in handlers:
                result = await handler(
                    RustCompletionHookInput(candidate=candidate), context
                )
                output = result.output
                if isinstance(output, RustCompletionHookAccept):
                    if isinstance(output.acceptance, RustReplaceAssistantContent):
                        replacement = output.acceptance.content
                    continue
                if isinstance(output, RustCompletionHookRetry):
                    bounded = self._bounded_retry(action.turn_id, output)
                    if bounded is None:
                        # Retry cap hit: stop and force-accept below, honoring any
                        # replacement an earlier accepting hook already collected.
                        break
                    return RustHookCompletedEvent(
                        action_id=action.action_id,
                        result=RustPostAgentTurnHookResult(output=bounded),
                    )
                # A reject is terminal.
                self._post_agent_retries.pop(action.turn_id, None)
                return RustHookCompletedEvent(
                    action_id=action.action_id,
                    result=RustPostAgentTurnHookResult(output=output),
                )
        except asyncio.CancelledError:
            # The turn was abandoned mid-evaluation (shutdown/abort); drop its retry
            # counter so a cancelled retrying turn cannot leak one entry per turn_id.
            self._post_agent_retries.pop(action.turn_id, None)
            raise
        except Exception as error:
            self._post_agent_retries.pop(action.turn_id, None)
            return _hook_pipeline_failed(action, error)
        finally:
            if emit_run:
                await self._emit_notice(
                    HookNoticeData(kind="hook_run_completed", scope="post_agent")
                )
        # The turn is accepted (normal accept or retry-cap force-accept): drop its
        # retry counter so a long session cannot accumulate one entry per turn.
        self._post_agent_retries.pop(action.turn_id, None)
        acceptance: RustReplaceAssistantContent | RustAcceptCandidate = (
            RustReplaceAssistantContent(content=replacement)
            if replacement is not None
            else RustAcceptCandidate()
        )
        return RustHookCompletedEvent(
            action_id=action.action_id,
            result=RustPostAgentTurnHookResult(
                output=RustCompletionHookAccept(acceptance=acceptance)
            ),
        )

    def _bounded_retry(
        self, turn_id: str, retry: RustCompletionHookRetry
    ) -> RustCompletionHookRetry | None:
        count = self._post_agent_retries.get(turn_id, 0)
        if count >= _MAX_POST_AGENT_RETRIES:
            # The turn has retried too many times; signal the caller to force-accept
            # (which honors any collected replacement) so the turn can finish.
            return None
        self._post_agent_retries[turn_id] = count + 1
        return retry

    def _hook_context(self) -> HookContext:
        # Turn-boundary and post hooks gate a whole turn or a completed call, not a single
        # pending tool call, so they are not offered a per-call approval transport.
        return HookContext(
            config=self._config,
            messages=tuple(self._messages),
            session_id=self._session_id,
            emit_hook_notice=self._emit_notice,
        )

    def _pre_tool_call_context(self, turn_id: str) -> HookContext:
        # pre_tool_call is the one hook point that gates a single pending call, so it is the
        # only one given a per-call approval transport bridged onto the Runtime callback.
        request_approval = (
            self._hook_approver(turn_id) if self._request_approval is not None else None
        )
        return HookContext(
            config=self._config,
            messages=tuple(self._messages),
            session_id=self._session_id,
            request_approval=request_approval,
            emit_hook_notice=self._emit_notice,
            record_approval_note=self._record_approval_note,
        )

    def _record_approval_note(self, action_id: str, note: str) -> None:
        self._approval_notes[action_id] = note

    def _record_approval_meta(
        self, action_id: str, decision: str, approval_type: str, approval_source: str
    ) -> None:
        self._approval_meta[action_id] = (decision, approval_type, approval_source)

    def _writable_roots(self) -> tuple[Path, ...]:
        """The directories this session may write to, as the file tools resolve them.

        ``resolve()`` matters, not just ``expanduser()``: the file tools resolve their
        target the same way, and on a symlinked workspace the two spellings otherwise
        disagree. This string is the trusted evidence that makes a workspace delete
        auto-approvable, so it has to name the directory the write will actually land in.
        """
        roots = self._config.workspace_roots or (self._config.cwd,)
        return tuple(root.expanduser().resolve() for root in roots)

    def _with_approval_note(self, event: RustEvent, action_id: str) -> RustEvent:
        """Ride a recorded approval note out on the tool's result ``_meta``.

        UI-only: the projection lifts it into the effect's ``warnings`` for the user;
        it never enters ``content`` (the model-visible result).
        """
        note = self._approval_notes.pop(action_id, None)
        approval_meta = self._approval_meta.pop(action_id, None)
        if note is None and approval_meta is None:
            return event
        if not isinstance(event, (RustToolSucceededEvent, RustToolFailedEvent)):
            return event
        meta = dict(event.result.meta or {})
        if note is not None:
            meta[APPROVAL_NOTE_META_KEY] = note
        if approval_meta is not None:
            meta[APPROVAL_META_KEY] = {
                "decision": approval_meta[0],
                "approvalType": approval_meta[1],
                "approvalSource": approval_meta[2],
            }
        return event.model_copy(
            update={"result": event.result.model_copy(update={"meta": meta})}
        )

    def _hook_approver(self, turn_id: str) -> HookApprovalCallback:
        """Bridge a hook body's approval request onto the Runtime approval callback.

        The hook body sees a ``RustHookToolCall``; the Runtime callback expects a
        builtin tool-call action, so lift the two together with the firing turn ID. A
        hook body cannot approve a provided/MCP call through this bridge, so those deny
        under uncertainty rather than run unreviewed (the classify gate handles them).
        """
        requester = self._request_approval
        assert requester is not None

        async def approve(tool_call: RustHookToolCall, reason: str | None) -> bool:
            call = tool_call.call
            if not isinstance(call, RustRuntimeBuiltinToolCall):
                return False
            grant = await requester(
                RustRuntimeBuiltinToolCallAction(
                    action_id=tool_call.action_id,
                    turn_id=turn_id,
                    call_id=tool_call.call_id,
                    call=call,
                ),
                reason,
                (),
            )
            return grant.approved

        return approve

    async def _gate_tool_call(
        self, action: GatedToolAction, mode: str
    ) -> RustEvent | None:
        """Apply an approval mode to a pending builtin or provided call.

        Returns a failed tool event to block the call, or ``None`` to let it run.
        ``deny`` blocks, ``ask`` prompts the human, ``classify`` defers to smart
        approve, and ``allow`` (also ``ask`` under ``bypass_approval``) runs it.

        An ``ask`` call is first refined by the Host's per-call permission
        resolver (the Vibe rules: .env prompt, denylists, allowlist grants), which
        can lower it to allow/deny before the human is ever asked.
        """
        self._resolved_authorized_path = None
        if mode == "ask" and self._config.bypass_approval:
            mode = "allow"
        outcome: PermissionOutcome | None = None
        if mode == "ask":
            outcome = await self._resolve_permission(action)
            self._resolved_authorized_path = outcome.authorized_path
            mode = outcome.decision
        match mode:
            case "deny":
                self._record_approval_meta(action.action_id, "skip", "never", "never")
                return _failed_tool_action(
                    action,
                    code="tool_denied",
                    message=(outcome.reason if outcome and outcome.reason else None)
                    or "Tool execution denied by approval policy",
                )
            case "ask":
                if self._request_approval is None:
                    return _failed_tool_action(
                        action,
                        code="approval_required",
                        message="Approval callbacks are not implemented yet",
                    )
                required = outcome.required_permissions if outcome else ()
                approved = (
                    await self._request_approval(action, None, required)
                ).approved
                if not approved:
                    self._record_approval_meta(action.action_id, "skip", "ask", "user")
                    return _failed_tool_action(
                        action,
                        code="tool_denied",
                        message="Tool execution denied by approval callback",
                    )
                self._record_approval_meta(action.action_id, "execute", "ask", "user")
                return None
            case "classify":
                return await self._classify_gate(action)
            case _:
                source = "bypass" if self._config.bypass_approval else "config"
                self._record_approval_meta(
                    action.action_id, "execute", "always", source
                )
                return None

    async def _classify_gate(self, action: GatedToolAction) -> RustEvent | None:  # noqa: PLR0911 - one return per gate verdict
        """Gate a builtin or provided call through the smart-approve risk classifier.

        Returns ``None`` to let the tool run, or a failed tool event to deny it.

        A safe verdict (a session-remembered approval or an ``ALLOW`` from the
        classifier) runs the tool and records why it auto-ran.

        A risky (``PROMPT``) verdict on an action the user did not clearly ask for
        denies-and-continues: the call fails back to the model with the reason so it
        self-corrects, without interrupting the human. It escalates to the human when
        the classifier scores the action as user-authorized, on a semantic retry (the
        model found no safe alternative), or once the denial streak or per-turn total
        is hit. An ``ERROR`` verdict (classifier unavailable/unparsable) escalates
        immediately -- it is never silently retried.

        The Host's per-call permission rules run first: a hard ``deny`` or a positive
        ``allow`` (allowlist grant, tool set to ALWAYS) settles the call without a
        model call, and only the resolver's ``ask`` residue is classified. The
        resolver's required permissions ride any escalation so a session/permanent
        grant stays scoped to the call rather than the whole tool.
        """
        # The Host permission resolver settles first. A hard deny or a positive
        # allow (allowlist / ALWAYS grant) returns below WITHOUT emitting a
        # ``tool_classification`` event -- those calls were never classified, so the
        # approval-rejection metric's denominator is classified calls only, not every
        # gated call. Account for that when defining the metric.
        required: tuple[JsonObject, ...] = ()
        outcome = await self._resolve_permission(action)
        self._resolved_authorized_path = outcome.authorized_path
        if outcome.decision == "deny":
            self._record_approval_meta(action.action_id, "skip", "never", "never")
            return _failed_tool_action(
                action,
                code="tool_denied",
                message=outcome.reason or "Tool execution denied by approval policy",
            )
        if outcome.decision == "allow":
            self._record_approval_meta(action.action_id, "execute", "always", "smart")
            self._smart_proceed(
                action.action_id, "Auto-approved: allowed by your permission rules"
            )
            return None
        required = outcome.required_permissions

        self._reset_smart_counters_on_new_turn(action.turn_id)
        request = build_classification_request(
            action.call,
            tuple(self._messages),
            writable_roots=[str(root) for root in self._writable_roots()],
        )
        # Exact: it scopes what a human's session grant covers.
        key = (request.tool_name, request.args_hash())

        if (
            key in self._session_approvals
            or request.tool_name in self._session_approved_tools
        ):
            self._emit_classification(
                action,
                tool_name=request.tool_name,
                verdict="allow",
                tier=ClassificationTier.SESSION,
                reason="",
                outcome=_AUTO_APPROVED,
            )
            self._record_approval_meta(action.action_id, "execute", "always", "smart")
            self._smart_proceed(action.action_id, "Approved earlier this session")
            return None

        result = await classify_tool_call(
            request,
            config=self._config,
            classifier_factory=self._classifier_factory,
            classify_model=self._classify_model,
        )
        # A path that decides what runs later is never auto-approved, whatever the
        # classifier says: writing one turns a single approved call into arbitrary
        # future execution, which is how an agent would widen its own authority.
        protected = protected_target(request.tool_name, action.call.arguments)
        if protected is not None and result.verdict == ClassificationVerdict.ALLOW:
            result = result.model_copy(
                update={
                    "verdict": ClassificationVerdict.PROMPT,
                    "reason": f"This changes {protected}, which decides what runs later.",
                }
            )
        if result.verdict == ClassificationVerdict.ALLOW:
            self._emit_result(action, request.tool_name, result, _AUTO_APPROVED)
            note = f"Auto-approved: {result.reason}" if result.reason else None
            self._record_approval_meta(action.action_id, "execute", "always", "smart")
            self._smart_proceed(action.action_id, note)
            return None

        reason = result.reason or "Smart approve could not confirm this call is safe."
        if result.verdict == ClassificationVerdict.ERROR:
            # Unverifiable: never deny-and-continue silently -- ask the human now.
            return await self._smart_escalate(action, key, reason, result, required)

        self._smart_deny_consecutive += 1
        self._smart_deny_total += 1
        repeat_of_denied_call = key in self._smart_denied_keys
        self._smart_denied_keys.add(key)
        escalate = (
            protected is not None
            or result.user_authorized
            or repeat_of_denied_call
            or self._smart_deny_consecutive >= _SMART_DENY_CONSECUTIVE_LIMIT
            or self._smart_deny_total >= _SMART_DENY_TOTAL_LIMIT
        )
        if escalate:
            return await self._smart_escalate(action, key, reason, result, required)
        self._record_approval_meta(action.action_id, "skip", "never", "smart")
        self._emit_result(action, request.tool_name, result, _DENY_AND_CONTINUE)
        return _failed_tool_action(
            action,
            code="tool_denied",
            message=f"Smart approve blocked this action: {reason}.{_SMART_DENY_CONTINUE_HINT}",
        )

    def _emit_result(
        self,
        action: GatedToolAction,
        tool_name: str,
        result: RiskClassificationResult,
        outcome: str,
        grant: ApprovalGrant | None = None,
    ) -> None:
        self._emit_classification(
            action,
            tool_name=tool_name,
            verdict=result.verdict.value,
            tier=result.tier,
            reason=result.reason,
            outcome=outcome,
            latency_ms=result.latency_ms,
            model=result.model,
            risk_level=result.risk_level,
            user_authorization=result.user_authorization,
            approval_grant=grant.value if grant is not None else None,
        )

    def _emit_classification(  # noqa: PLR0913 - explicit classification fields
        self,
        action: GatedToolAction,
        *,
        tool_name: str,
        verdict: str,
        tier: ClassificationTier,
        reason: str,
        outcome: str,
        latency_ms: float = 0.0,
        model: str | None = None,
        risk_level: str | None = None,
        user_authorization: str | None = None,
        approval_grant: str | None = None,
    ) -> None:
        """Publish one classification to the telemetry side-channel (best-effort).

        ``outcome`` is the terminal decision for the call -- ``auto_approved``,
        ``deny_and_continue``, or an ``escalated_*`` value; ``escalated`` derives from
        it and drives the approval-rejection metric (share of calls reaching a human).
        A sink failure must never change the tool outcome, so any error is swallowed.

        ``approval_grant`` is the human's answer, kept orthogonal to ``outcome``
        because ``escalated_approved`` covers both a one-shot approve and a "remember
        for the session" -- answering the gate versus switching it off. It is absent
        when no human was asked.
        """
        sink = self._classification_sink
        if sink is None:
            return
        payload: JsonObject = {
            "type": CLASSIFICATION_EVENT_TYPE,
            "tool_name": tool_name,
            "verdict": verdict,
            "tier": tier.value,
            "reason": reason[:200],
            "outcome": outcome,
            "escalated": outcome.startswith("escalated"),
            "latency_ms": latency_ms,
            "classifier_model": model,
            "classifier_prompt_version": CLASSIFIER_PROMPT_VERSION,
            "risk_level": risk_level,
            "user_authorization": user_authorization,
            "approval_grant": approval_grant,
            "session_id": self._session_id,
            "turn_id": action.turn_id,
            "call_id": action.call_id,
        }
        try:
            sink(payload)
        except Exception:  # telemetry is best-effort
            logger.warning(
                "Failed to publish tool classification telemetry", exc_info=True
            )

    def _reset_smart_counters_on_new_turn(self, turn_id: str) -> None:
        if turn_id == self._smart_turn_id:
            return
        self._smart_turn_id = turn_id
        self._smart_deny_consecutive = 0
        self._smart_deny_total = 0

    def _smart_proceed(self, action_id: str, note: str | None) -> None:
        """Let a classified call run: reset the deny streak and record the UI note.

        Returns ``None``; callers proceed by returning ``None`` from the gate after
        calling this, so a bare call reads as "record the note and let the tool run".
        """
        self._smart_deny_consecutive = 0
        if note is not None:
            self._record_approval_note(action_id, note)

    async def _smart_escalate(
        self,
        action: GatedToolAction,
        key: tuple[str, str],
        reason: str,
        result: RiskClassificationResult,
        required: tuple[JsonObject, ...] = (),
    ) -> RustEvent | None:
        """Ask the human. Deny (or headless, with no responder) fails the call back to
        the model; approval runs it and, on a session/always grant, remembers it.

        ``required`` carries the resolver's per-call permissions so the callback the
        human answers scopes a session/permanent grant to the call, not the tool.
        """
        tool_name = key[0]
        if self._request_approval is None:
            self._record_approval_meta(action.action_id, "skip", "ask", "user")
            self._emit_result(action, tool_name, result, _ESCALATED_HEADLESS_DENIED)
            return _failed_tool_action(action, code="tool_denied", message=reason)
        grant = await self._request_approval(action, reason, required)
        if not grant.approved:
            self._record_approval_meta(action.action_id, "skip", "ask", "user")
            self._emit_result(action, tool_name, result, _ESCALATED_DENIED, grant)
            return _failed_tool_action(action, code="tool_denied", message=reason)
        if grant.remembered:
            if isinstance(action, RustRuntimeBuiltinToolCallAction):
                self._session_approvals.add(key)
            else:
                # Provided/MCP tools have no resolver patterns, so a session grant
                # covers the whole tool rather than one exact argument set.
                self._session_approved_tools.add(tool_name)
        self._record_approval_meta(action.action_id, "execute", "ask", "user")
        self._emit_result(action, tool_name, result, _ESCALATED_APPROVED, grant)
        self._smart_proceed(action.action_id, f"Approved: {reason}")
        return None

    def _apply_model_input(self, action: RustLLMCallAction) -> None:
        messages = action.model_input.messages
        if isinstance(messages, RustModelMessageReplace):
            self._messages = list(messages.messages)
            self._message_revision = messages.revision
        elif isinstance(messages, RustModelMessageAppend):
            if self._message_revision != messages.base_revision:
                raise RuntimeError("model message cache is out of date")
            self._messages = [*self._messages, *messages.messages]
            self._message_revision = messages.revision

        tools = action.model_input.tool_catalog
        if isinstance(tools, RustModelToolCatalogReplace):
            self._tools = list(tools.tools)
            self._tool_revision = tools.revision
        elif isinstance(tools, RustModelToolCatalogKeep):
            if self._tool_revision is None:
                if tools.revision != 0:
                    raise RuntimeError("model tool cache is not initialized")
                self._tool_revision = 0
            elif self._tool_revision != tools.revision:
                raise RuntimeError("model tool cache is out of date")


def _with_arguments(
    tool_call: RustHookToolCall, arguments: JsonObject
) -> RustHookToolCall:
    return tool_call.model_copy(
        update={"call": tool_call.call.model_copy(update={"arguments": arguments})}
    )


def _resolve_hooks[H](
    binding_ids: Sequence[str], registry: Mapping[str, H]
) -> list[tuple[str, H]]:
    """Resolve Core-selected binding IDs to registered hook bodies, in order.

    A binding whose handler is not registered is *skipped*, not fatal: on a crash-mid-turn
    resume the persisted bindings are kept for replay, but the user may have edited
    ``hooks.toml`` so a bound id no longer has a command. A duplicate id in one dispatch is
    still fatal -- that is a Core selection invariant violation, not config drift.
    """
    seen: set[str] = set()
    handlers: list[tuple[str, H]] = []
    for binding_id in binding_ids:
        if binding_id in seen:
            raise ValueError(f"Core selected duplicate hook binding {binding_id!r}")
        seen.add(binding_id)
        handler = registry.get(binding_id)
        if handler is None:
            logger.warning(
                "Skipping hook binding %r with no registered handler "
                "(hooks.toml changed since the session's bindings were persisted)",
                binding_id,
            )
            continue
        handlers.append((binding_id, handler))
    return handlers


def _select_for_tool[H](
    resolved: list[tuple[str, H]], call: RustExternalToolCall
) -> list[tuple[str, H]]:
    # Filtering here rather than inside the handler body is what keeps a non-matching
    # hook from emitting a run notice on every tool call.
    name = qualified_tool_name(call)
    return [
        (binding_id, handler)
        for binding_id, handler in resolved
        if not isinstance(handler, ToolScopedHook) or handler.selects(name)
    ]


def _hook_pipeline_failed(
    action: RustHookCallActionBase, error: object
) -> RustHookFailedEvent:
    return RustHookFailedEvent(
        action_id=action.action_id,
        error=RustProtocolError(
            code="hook_pipeline_failed",
            message=str(error),
            retryable=False,
            details=None,
        ),
    )


def _unsupported_action(action: RustAction) -> RustEvent:
    error = RustProtocolError(
        code="unsupported_action",
        message=f"No local adapter is configured for {type(action).__name__}",
        retryable=False,
        details=None,
    )
    if isinstance(action, RustLLMCallAction):
        return RustCompletionFailedEvent(action_id=action.action_id, error=error)
    if isinstance(action, RustHookCallActionBase):
        return RustHookFailedEvent(action_id=action.action_id, error=error)
    if isinstance(
        action, RustRuntimeBuiltinToolCallAction | RustProvidedToolCallAction
    ):
        return _failed_tool_action(action, code=error.code, message=error.message)
    if isinstance(action, RustFilesystemAction):
        return RustFilesystemFailedEvent(action_id=action.action_id, error=error)
    raise TypeError(f"Unsupported action type: {type(action).__name__}")


def _replace_text_file(path: Path, content: str) -> None:
    # Offloaded tool results hold raw model output, so the directory and the
    # file are created owner-only; existing paths keep their current mode.
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _failed_tool_action(
    action: RustRuntimeBuiltinToolCallAction | RustProvidedToolCallAction,
    *,
    code: str,
    message: str,
) -> RustToolFailedEvent:
    return RustToolFailedEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolFailureResult(
            error=RustProtocolError(
                code=code, message=message, retryable=False, details=None
            )
        ),
    )


__all__ = [
    "CLASSIFICATION_EVENT_TYPE",
    "ApprovalGrant",
    "ApprovalRequester",
    "ClassificationSink",
    "HookApprovalCallback",
    "HookContext",
    "HookHandlers",
    "HookNoticeData",
    "HookNoticeEmitter",
    "PostAgentTurnHookHandler",
    "PostToolCallHookHandler",
    "PreAgentTurnHookHandler",
    "PreToolCallHookHandler",
    "ProvidedToolExecutor",
    "ProvidedToolExecutorFactory",
    "PublicHistoryEntrySink",
    "ToolScopedHook",
    "build_local_action_executor",
    "foreign_binding_ids",
    "has_hook_handlers",
]
