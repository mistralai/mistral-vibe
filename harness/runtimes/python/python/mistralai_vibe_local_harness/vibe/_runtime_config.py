from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Literal

from mistralai_vibe_local_harness.protocol import (
    RustHarnessConfig,
    RustRuntimeBuiltinToolName,
)
from mistralai_vibe_local_harness.session_protocol import PublicRetryCategory
from mistralai_vibe_local_harness.vibe._credentials import (
    ProviderCredentialProvider,
    StaticProviderCredentials,
)
from mistralai_vibe_local_harness.vibe._permissions import PermissionResolver

type ThinkingLevel = Literal["off", "low", "medium", "high", "max"]

# "classify" defers to the smart-approve risk classifier at dispatch time; the
# other three are frozen decisions.
type ToolApprovalMode = Literal["allow", "ask", "deny", "classify"]

# ``classify`` and ``title`` name Runtime-issued completions outside Core's LLM
# call actions (those stay ``agent`` or ``compaction``): the smart-approve risk
# classifier's gate call and the background session title. They ride the same
# host seams so a Host attributes and counts them like any other completion.
type CompletionPurpose = Literal["agent", "compaction", "classify", "title"]


@dataclass(frozen=True, slots=True)
class ProviderRetry:
    category: PublicRetryCategory
    detail: str
    delay_s: float
    retry_attempt: int


type ProviderRetryObserver = Callable[[ProviderRetry | None], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ProviderStreamDelta:
    """One streamed chunk's visible fragments, straight off the provider stream.

    A chunk carries text, reasoning, or both; empty fragments mean the chunk
    had neither and the observer is not called for it.
    """

    text: str = ""
    reasoning: str = ""


# Per-chunk observer for a provider stream in progress. Emission is
# best-effort side traffic: the completion's terminal result is unaffected.
type ProviderDeltaObserver = Callable[[ProviderStreamDelta], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class CompletionDelta:
    """Provisional assistant output for one completion action.

    ``text`` and ``reasoning`` are fragments to append to the action's
    provisional history entries. ``restart`` discards everything projected so
    far for the action: a retried attempt must not leave the failed attempt's
    partial output in the public history.

    ``turn_id`` is required, unlike on the action it comes from: a provisional
    entry is settled by its turn's terminal observation, which matches on turn
    id, so one stamped ``None`` would stay in progress with no boundary able to
    close it.
    """

    action_id: str
    turn_id: str
    text: str = ""
    reasoning: str = ""
    restart: bool = False


# Host sink for provisional completion content. Called from the completion
# executor while the provider stream is live, so a slow sink delays the
# stream itself: keep projection work bounded.
type CompletionDeltaSink = Callable[[CompletionDelta], Awaitable[None]]

# Host sink for the provider request-correlation id, called once per completion.
type CorrelationIdSink = Callable[[str | None], None]

# Host source of provider request metadata, called once per completion with the
# call's purpose and iteration. Backend-neutral for the same reason as
# ``RequestSentTelemetry``: the runtime reports the call's shape and the Host
# owns the call-type taxonomy. A callable rather than a value because a Host may
# only learn part of its attribution -- its session id, say -- after this config
# is built.
type CompletionMetadataSource = Callable[[CompletionPurpose, int], Mapping[str, str]]
type AffinityIdSource = Callable[[], str | None]


@dataclass(frozen=True, slots=True)
class RequestSentTelemetry:
    """One completion's request shape, reported before the provider call.

    Only transport-neutral primitives cross to the Host. The Host maps
    ``purpose`` and ``iteration`` onto its call-type taxonomy and forwards the
    remaining request metrics once per provider call.
    """

    model: str
    purpose: CompletionPurpose
    iteration: int
    nb_context_chars: int
    nb_context_messages: int
    nb_prompt_chars: int


# Host sink for per-completion request telemetry, called once per LLM call.
type RequestSentSink = Callable[[RequestSentTelemetry], None]


@dataclass(frozen=True, slots=True)
class LocalModelRoute:
    model: str = "mistral-vibe-cli-latest"
    temperature: float = 1.0
    thinking: ThinkingLevel = "off"
    supports_images: bool = True


_DEFAULT_LOCAL_MODEL_ROUTE = LocalModelRoute()


@dataclass(frozen=True, slots=True)
class LocalProviderRoute:
    """A provider destination for a completion: where to send it and how to auth.

    Lets a background utility completion (e.g. title generation) target a
    provider other than the session's active one — for example the cheap fast
    Mistral model while the coding session runs on Anthropic — without threading
    a second config through the completion adapters. The adapters still take one
    ``LocalRuntimeAdapterConfig``; the utility path overlays this route onto it.
    """

    provider: str
    backend: str
    api_style: str
    base_url: str
    credentials: ProviderCredentialProvider
    reasoning_field_name: str = "reasoning_content"
    emits_finish_reason: bool = True
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    project_id: str = ""
    region: str = ""


type CommandEnvironment = Literal[
    "disabled", "unix", "git_bash", "powershell", "in_memory_bash"
]
type ProcessAuthority = Literal["disabled", "host_shell"]


@dataclass(frozen=True, slots=True)
class SessionWorkspace:
    """The canonical local workspace owned by one Harness Session."""

    cwd: Path = field(default_factory=Path.cwd)
    roots: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        cwd = self.cwd.expanduser().resolve()
        roots = tuple(dict.fromkeys(root.expanduser().resolve() for root in self.roots))
        if not roots:
            roots = (cwd,)
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "roots", roots)

    def moved_to(self, cwd: str | Path) -> SessionWorkspace:
        """Move the working directory without widening explicit access roots."""
        destination = Path(cwd).expanduser().resolve()
        if destination == self.cwd:
            return self
        roots = tuple(destination if root == self.cwd else root for root in self.roots)
        return SessionWorkspace(cwd=destination, roots=roots)


@dataclass(frozen=True, slots=True)
class LocalRuntimeAdapterPorts:
    """Host-owned callbacks and live providers that must be rebound on fork."""

    credentials: ProviderCredentialProvider
    title_provider: LocalProviderRoute | None
    classifier_provider: LocalProviderRoute | None
    affinity_id: AffinityIdSource | None
    completion_metadata: CompletionMetadataSource | None
    permission_resolver: PermissionResolver | None
    correlation_id_sink: CorrelationIdSink | None
    request_sent_sink: RequestSentSink | None


@dataclass(frozen=True, slots=True)
class LocalRuntimeAdapterConfig:
    provider: str = "mistral"
    # Selects the completion adapter family: "mistral" (Mistral SDK) or
    # "generic" (OpenAI-compatible / anthropic / openai-responses / vertex).
    backend: str = "mistral"
    # For the generic backend, the wire format of the endpoint. One of
    # "openai", "reasoning", "anthropic", "openai-responses", "vertex-anthropic".
    api_style: str = "openai"
    base_url: str = "https://api.mistral.ai/v1"
    # Replaces a frozen ``api_key``: a credential that can expire mid-session
    # cannot be a value resolved once at session open. The Host supplies a live
    # resolver; a standalone Harness or a test supplies a static token.
    credentials: ProviderCredentialProvider = field(
        default_factory=StaticProviderCredentials
    )
    # The active route's fields remain accepted for direct generic-adapter
    # callers. Runtime dispatch always selects an explicit route instead.
    model: str = "mistral-vibe-cli-latest"
    temperature: float = 1.0
    # Reasoning effort passed to reasoning-capable providers: "off" or a level.
    thinking: ThinkingLevel = "off"
    active_model: LocalModelRoute = field(default_factory=LocalModelRoute)
    # Only used to record profile changes in the transcript; None disables it.
    active_agent: str | None = None
    compaction_model: LocalModelRoute = field(default_factory=LocalModelRoute)
    title_model: LocalModelRoute | None = None
    # Optional destination for the title completion when it must run on a
    # different provider than the session's active one (e.g. the fast Mistral
    # model while the session runs on Anthropic). ``None`` keeps the title on the
    # session provider, using the fields above.
    title_provider: LocalProviderRoute | None = None
    # Optional destination for the smart-approve classifier completion. The
    # classifier runs a fast Mistral model regardless of the session's active
    # model, so when the session runs on another provider (e.g. Anthropic) this
    # routes the classify call to Mistral with Mistral credentials. ``None``
    # keeps the classifier on the session provider (already Mistral).
    classifier_provider: LocalProviderRoute | None = None
    # Model for the classify gate; empty keeps the Runtime default. A name, not a
    # route: the gate pins its own sampling settings.
    classifier_model: str = ""
    max_tokens: int | None = None
    # OpenAI-compatible providers expose reasoning under different keys.
    reasoning_field_name: str = "reasoning_content"
    # Some OpenAI-compatible endpoints never emit a finish reason.
    emits_finish_reason: bool = True
    # Extra static headers merged into every provider request.
    extra_headers: dict[str, str] = field(default_factory=dict)
    affinity_id: AffinityIdSource | None = None
    # Host attribution attached only to Mistral completion requests because it
    # is a Mistral-specific request field.
    completion_metadata: CompletionMetadataSource | None = None
    # Vertex AI routing.
    project_id: str = ""
    region: str = ""
    timeout_s: float = 60.0
    retry_max_elapsed_time_s: float = 300.0
    workspace: SessionWorkspace = field(default_factory=SessionWorkspace)
    env: dict[str, str] = field(default_factory=dict)
    command_environment: CommandEnvironment = "unix"
    shell: str | None = None
    process_authority: ProcessAuthority = "disabled"
    bypass_approval: bool = False
    # "classify" routes the call through the smart-approve risk classifier at
    # dispatch time; unlike the frozen ask/allow/deny gates it can be flipped on
    # or off mid-session via apply_adapter_config, so switching modes works live.
    tool_modes: dict[RustRuntimeBuiltinToolName, ToolApprovalMode] = field(
        default_factory=dict
    )
    # Provided/MCP tools have no per-name entry in tool_modes, so a single mode gates
    # them all. Per-tool decisions come from ``permission_resolver`` instead, and a
    # Host can override the mode per group via ``configure_provided_tool_executor``.
    provided_tool_mode: ToolApprovalMode = "allow"
    # Host-bound per-call permission resolver: refines an ``ask`` call into allow/ask/deny
    # (main's Vibe rules — .env prompt, denylists, allowlist grants), named by a builtin's
    # name or a provided tool's full route. Under smart approve it runs first, and only
    # its ``ask`` residue is classified.
    permission_resolver: PermissionResolver | None = None
    correlation_id_sink: CorrelationIdSink | None = None
    request_sent_sink: RequestSentSink | None = None
    skills: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        direct_route = LocalModelRoute(
            model=self.model, temperature=self.temperature, thinking=self.thinking
        )
        active_model = self.active_model
        if (
            active_model == _DEFAULT_LOCAL_MODEL_ROUTE
            and direct_route != _DEFAULT_LOCAL_MODEL_ROUTE
        ):
            active_model = direct_route
        compaction_model = self.compaction_model
        if compaction_model == _DEFAULT_LOCAL_MODEL_ROUTE:
            compaction_model = active_model
        object.__setattr__(self, "active_model", active_model)
        object.__setattr__(self, "compaction_model", compaction_model)
        object.__setattr__(self, "model", active_model.model)
        object.__setattr__(self, "temperature", active_model.temperature)
        object.__setattr__(self, "thinking", active_model.thinking)

    @classmethod
    def at(
        cls,
        cwd: str | Path,
        *additional_roots: str | Path,
        roots: tuple[str | Path, ...] = (),
        **config: object,
    ) -> LocalRuntimeAdapterConfig:
        """Build a config rooted at ``cwd`` without duplicating workspace fields."""
        if additional_roots and roots:
            raise ValueError(
                "pass workspace roots positionally or by keyword, not both"
            )
        configured_roots = additional_roots or roots
        return replace(
            cls(),
            workspace=SessionWorkspace(
                cwd=Path(cwd), roots=tuple(Path(root) for root in configured_roots)
            ),
            **config,
        )

    def request_headers(self) -> dict[str, str]:
        headers = dict(self.extra_headers)
        affinity_id = self.affinity_id() if self.affinity_id is not None else None
        if affinity_id:
            headers = {
                name: value
                for name, value in headers.items()
                if name.lower() != "x-affinity"
            }
            headers["x-affinity"] = affinity_id
        return headers

    def with_workspace(self, workspace: SessionWorkspace) -> LocalRuntimeAdapterConfig:
        """Return this config projected onto the Session's canonical workspace."""
        if self.workspace == workspace:
            return self
        return replace(self, workspace=workspace)

    def fork_ports(self) -> LocalRuntimeAdapterPorts:
        """Project the Host-owned values that a child Session must receive anew."""
        return LocalRuntimeAdapterPorts(
            credentials=self.credentials,
            title_provider=self.title_provider,
            classifier_provider=self.classifier_provider,
            affinity_id=self.affinity_id,
            completion_metadata=self.completion_metadata,
            permission_resolver=self.permission_resolver,
            correlation_id_sink=self.correlation_id_sink,
            request_sent_sink=self.request_sent_sink,
        )

    def forked_with(
        self, *, workspace: SessionWorkspace, ports: LocalRuntimeAdapterPorts
    ) -> LocalRuntimeAdapterConfig:
        """Keep this Session's behavior while assigning the child's ownership."""
        _validate_fork_field_partition()
        return replace(
            self,
            workspace=workspace,
            credentials=ports.credentials,
            title_provider=ports.title_provider,
            classifier_provider=ports.classifier_provider,
            affinity_id=ports.affinity_id,
            completion_metadata=ports.completion_metadata,
            permission_resolver=ports.permission_resolver,
            correlation_id_sink=ports.correlation_id_sink,
            request_sent_sink=ports.request_sent_sink,
        )


_LOCAL_RUNTIME_ADAPTER_BEHAVIOR_FIELDS = frozenset({
    "provider",
    "backend",
    "api_style",
    "base_url",
    "model",
    "temperature",
    "thinking",
    "active_model",
    "active_agent",
    "compaction_model",
    "title_model",
    "classifier_model",
    "max_tokens",
    "reasoning_field_name",
    "emits_finish_reason",
    "extra_headers",
    "project_id",
    "region",
    "timeout_s",
    "retry_max_elapsed_time_s",
    "env",
    "command_environment",
    "shell",
    "process_authority",
    "bypass_approval",
    "tool_modes",
    "provided_tool_mode",
    "skills",
})


def _validate_fork_field_partition() -> None:
    actual = {field.name for field in fields(LocalRuntimeAdapterConfig)}
    ports = {field.name for field in fields(LocalRuntimeAdapterPorts)}
    classified = _LOCAL_RUNTIME_ADAPTER_BEHAVIOR_FIELDS | ports | {"workspace"}
    if actual != classified:
        missing = sorted(actual - classified)
        stale = sorted(classified - actual)
        raise RuntimeError(
            "LocalRuntimeAdapterConfig fork ownership is incomplete: "
            f"unclassified={missing}, unknown={stale}"
        )


@dataclass(frozen=True, slots=True)
class SessionConfig:
    """The paired Core and local execution configuration for one Session."""

    core: RustHarnessConfig
    local: LocalRuntimeAdapterConfig


__all__ = [
    "AffinityIdSource",
    "CommandEnvironment",
    "CompletionDelta",
    "CompletionDeltaSink",
    "CompletionMetadataSource",
    "CompletionPurpose",
    "CorrelationIdSink",
    "LocalModelRoute",
    "LocalProviderRoute",
    "LocalRuntimeAdapterConfig",
    "LocalRuntimeAdapterPorts",
    "ProcessAuthority",
    "ProviderDeltaObserver",
    "ProviderStreamDelta",
    "RequestSentSink",
    "RequestSentTelemetry",
    "SessionConfig",
    "SessionWorkspace",
    "ThinkingLevel",
    "ToolApprovalMode",
]
