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
from mistralai_vibe_local_harness.vibe._sandbox import (
    HelperCrashCount,
    SandboxAdapter,
    sandbox_path,
)

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
    remaining request metrics once per provider call. ``turn_id`` is the turn
    an agent or compaction call serves, which tells a Session's own calls
    from its subagents' when they share one sink; utility completions have
    none.
    """

    model: str
    purpose: CompletionPurpose
    iteration: int
    nb_context_chars: int
    nb_context_messages: int
    nb_prompt_chars: int
    turn_id: str | None = None


# Host sink for per-completion request telemetry, called once per LLM call.
type RequestSentSink = Callable[[RequestSentTelemetry], None]

# The ways a provider response can break the tool call contract Core enforces,
# each named after the rejection it would cause.
type MalformedToolCallReason = Literal[
    "tool_call_finish_without_tool_call",
    "empty_tool_call_identity",
    "tool_call_with_non_tool_call_finish",
]


@dataclass(frozen=True, slots=True)
class MalformedToolCallsTelemetry:
    """One completion whose provider responses broke the tool call contract.

    Reported once, when the completion ends, and only when the adapters had to
    repair at least one response. ``reasons`` names each kind of repair made.
    ``nb_unusable_attempts`` counts the responses whose every tool call lacked a
    name, which the retry policy retries. ``nb_dropped_tool_calls`` counts the
    tool calls dropped for lacking a name. ``succeeded`` says whether the
    completion still produced a result. ``purpose``, ``iteration`` and
    ``turn_id`` mean what they mean on ``RequestSentTelemetry``.
    """

    model: str
    backend: str
    api_style: str
    purpose: CompletionPurpose
    iteration: int
    reasons: tuple[MalformedToolCallReason, ...]
    nb_unusable_attempts: int
    nb_dropped_tool_calls: int
    succeeded: bool
    turn_id: str | None = None


# Host sink for malformed tool call telemetry, called at most once per LLM call.
type MalformedToolCallsSink = Callable[[MalformedToolCallsTelemetry], None]


@dataclass(frozen=True, slots=True)
class LocalModelRoute:
    model: str = "mistral-vibe-cli-latest"
    temperature: float = 1.0
    thinking: ThinkingLevel = "off"
    supports_images: bool = True
    # Nucleus sampling; None leaves it to the provider's default.
    top_p: float | None = None
    # This model's cap on output tokens per request, used when the adapter
    # config sets no ``max_tokens`` of its own.
    max_output_tokens: int | None = None
    # The model's context window in tokens. When known, each request's output
    # cap is clipped to the room the input leaves, so input plus output never
    # asks for more than the window holds.
    context_window: int | None = None


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
type ProcessAuthority = Literal["disabled", "host_shell", "sandbox"]
"""Where background processes run: nowhere, in the host shell, or in the
Session's Sandbox Environment."""
type PathSpace = Literal["host", "sandbox"]
"""Whose file system a Session's workspace paths name."""


@dataclass(frozen=True, slots=True)
class SessionWorkspace:
    """The canonical local workspace owned by one Harness Session.

    ``path_space`` says whose file system the paths name. Host paths are
    resolved; sandbox paths are normalized with :func:`sandbox_path` instead,
    as this host's file system may not hold them or may link them elsewhere.
    """

    cwd: Path = field(default_factory=Path.cwd)
    roots: tuple[Path, ...] = ()
    path_space: PathSpace = "host"

    def __post_init__(self) -> None:
        cwd = self._canonical(self.cwd)
        roots = tuple(dict.fromkeys(self._canonical(root) for root in self.roots))
        if not roots:
            roots = (cwd,)
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "roots", roots)

    def moved_to(self, cwd: str | Path) -> SessionWorkspace:
        """Move the working directory without widening explicit access roots."""
        destination = self._canonical(Path(cwd))
        if destination == self.cwd:
            return self
        roots = tuple(destination if root == self.cwd else root for root in self.roots)
        return SessionWorkspace(
            cwd=destination, roots=roots, path_space=self.path_space
        )

    def _canonical(self, path: Path) -> Path:
        match self.path_space:
            case "host":
                return path.expanduser().resolve()
            case "sandbox":
                return sandbox_path(path)


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
    malformed_tool_calls_sink: MalformedToolCallsSink | None = None
    sandbox: SandboxAdapter | None = None
    helper_crashes: HelperCrashCount = field(
        default_factory=HelperCrashCount, compare=False
    )


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
    # System prompt of the title completion; None uses the built-in one.
    title_prompt: str | None = None
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
    # How the Runtime shapes compaction requests. The names match the Vibe
    # settings. The first, in-context request reuses the conversation's prompt
    # prefix and tool catalog; the standalone fallback always runs without
    # tools and without reasoning.
    # Output cap for every compaction request, applied below the general cap.
    # A summary is far shorter than this; the cap bounds how long a request
    # that reasons instead of summarizing can run. ``None`` applies no extra cap.
    compaction_max_output_tokens: int | None = 80_000
    # Reasoning of the in-context request; ``None`` keeps the compaction
    # route's own.
    compaction_thinking: ThinkingLevel | None = None
    # Reasoning, in estimated tokens, after which the in-context request is
    # abandoned so Core falls back to the standalone request. ``None`` lets it
    # reason up to the output cap.
    compaction_reasoning_limit_tokens: int | None = 32_000
    # Tokens kept free of the context window when an output cap is clipped to
    # the room the input leaves (see ``LocalModelRoute.context_window``). The
    # input size is an estimate, so the reserve absorbs its error.
    output_token_reserve: int = 4096
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
    # Silence allowed between output items once a response has started
    # streaming. ``timeout_s`` must also cover the wait for the first token, so
    # it is too long to notice a provider that stalls mid-response. ``None``
    # applies no limit: some providers, Mistral included, send a tool call in one
    # piece once it is fully generated, so a long tool call is a long silence
    # that no fixed limit can tell apart from a stall.
    stream_idle_timeout_s: float | None = None
    retry_max_elapsed_time_s: float = 300.0
    # Verify provider certificates with the operating system's trust store (the
    # macOS Keychain, for example) instead of OpenSSL's default roots plus
    # Certifi. That store also skips the strict chain checks of Python's
    # default context, which some private CAs fail.
    enable_system_trust_store: bool = False
    workspace: SessionWorkspace = field(default_factory=SessionWorkspace)
    # The environment of commands run on the host. Commands in a Sandbox
    # Environment run with the sandbox's own environment and never see it.
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
    # Subagent admission limits for the session's controller, resolved into
    # ``SubagentLimits`` when the subagent configuration is built. ``None``
    # keeps the Runtime default.
    subagent_max_children: int | None = None
    subagent_max_concurrent_turns: int | None = None
    # Host-bound per-call permission resolver: refines an ``ask`` call into allow/ask/deny
    # (main's Vibe rules — .env prompt, denylists, allowlist grants), named by a builtin's
    # name or a provided tool's full route. Under smart approve it runs first, and only
    # its ``ask`` residue is classified.
    permission_resolver: PermissionResolver | None = None
    correlation_id_sink: CorrelationIdSink | None = None
    request_sent_sink: RequestSentSink | None = None
    malformed_tool_calls_sink: MalformedToolCallsSink | None = None
    skills: Mapping[str, str] = field(default_factory=dict)
    # Where workspace file and shell tools run. None runs them on this host;
    # an adapter runs them in its Sandbox Environment, whose paths ``workspace``
    # then names. Session storage, attachments, provided tools, MCP servers
    # and hooks stay on the host either way. A saved tool output is recorded
    # in Session storage and copied to where the tools run.
    sandbox: SandboxAdapter | None = None
    # The tool helper's crashes in a row in ``sandbox``, shared by every
    # Session whose tools run there (see ``HelperCrashCount``).
    helper_crashes: HelperCrashCount = field(
        default_factory=HelperCrashCount, compare=False
    )

    def __post_init__(self) -> None:
        if (
            self.compaction_max_output_tokens is not None
            and self.compaction_max_output_tokens < 1
        ):
            raise ValueError("compaction_max_output_tokens must be positive")
        if (
            self.compaction_reasoning_limit_tokens is not None
            and self.compaction_reasoning_limit_tokens < 1
        ):
            raise ValueError("compaction_reasoning_limit_tokens must be positive")
        if self.subagent_max_children is not None and self.subagent_max_children < 1:
            raise ValueError("subagent_max_children must be positive")
        if (
            self.subagent_max_concurrent_turns is not None
            and self.subagent_max_concurrent_turns < 1
        ):
            raise ValueError("subagent_max_concurrent_turns must be positive")
        # ``sandbox`` decides where tools run; the workspace and the process
        # tools have to agree with it.
        if self.workspace.path_space != self.path_space:
            raise ValueError(
                f"a {self.path_space} session cannot use a workspace of "
                f"{self.workspace.path_space} paths"
            )
        if self.sandbox is not None and self.process_authority == "host_shell":
            raise ValueError(
                "a sandboxed session cannot run processes in the host shell"
            )
        if self.sandbox is None and self.process_authority == "sandbox":
            raise ValueError("sandbox processes need a sandboxed session")
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

    @property
    def path_space(self) -> PathSpace:
        """Whose file system the workspace paths name."""
        return "host" if self.sandbox is None else "sandbox"

    @classmethod
    def at(
        cls,
        cwd: str | Path,
        *additional_roots: str | Path,
        roots: tuple[str | Path, ...] = (),
        **config: object,
    ) -> LocalRuntimeAdapterConfig:
        """Build a config rooted at ``cwd`` without duplicating workspace fields.

        With a ``sandbox``, ``cwd`` and the roots are paths in its Sandbox
        Environment.
        """
        if additional_roots and roots:
            raise ValueError(
                "pass workspace roots positionally or by keyword, not both"
            )
        configured_roots = additional_roots or roots
        return replace(
            cls(),
            workspace=SessionWorkspace(
                cwd=Path(cwd),
                roots=tuple(Path(root) for root in configured_roots),
                path_space="host" if config.get("sandbox") is None else "sandbox",
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

    def output_token_cap(self, route: LocalModelRoute) -> int | None:
        """The output-token cap for one request on ``route``.

        ``max_tokens`` wins when set, so a caller that pins a short completion
        (a title, say) keeps its cap whatever the model allows.
        """
        if self.max_tokens is not None:
            return self.max_tokens
        return route.max_output_tokens

    def request_output_token_cap(
        self, route: LocalModelRoute, *, estimated_input_tokens: int, compaction: bool
    ) -> int | None:
        """The output-token cap for one Core model request.

        Starts from :meth:`output_token_cap`, lowers it to the compaction cap
        for a compaction request, then clips it to the room the estimated input
        leaves in the route's context window, keeping ``output_token_reserve``
        free. The result is at least 1 so the request stays well formed; a
        prompt that already fills the window fails at the provider either way.
        """
        cap = self.output_token_cap(route)
        compaction_cap = self.compaction_max_output_tokens
        if compaction and compaction_cap is not None:
            cap = compaction_cap if cap is None else min(cap, compaction_cap)
        if route.context_window is None:
            return cap
        room = route.context_window - estimated_input_tokens - self.output_token_reserve
        room = max(room, 1)
        return room if cap is None else min(cap, room)

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
            malformed_tool_calls_sink=self.malformed_tool_calls_sink,
            sandbox=self.sandbox,
            helper_crashes=self.helper_crashes,
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
            malformed_tool_calls_sink=ports.malformed_tool_calls_sink,
            sandbox=ports.sandbox,
            helper_crashes=ports.helper_crashes,
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
    "title_prompt",
    "classifier_model",
    "max_tokens",
    "compaction_max_output_tokens",
    "compaction_thinking",
    "compaction_reasoning_limit_tokens",
    "output_token_reserve",
    "reasoning_field_name",
    "emits_finish_reason",
    "extra_headers",
    "project_id",
    "region",
    "timeout_s",
    "stream_idle_timeout_s",
    "retry_max_elapsed_time_s",
    "enable_system_trust_store",
    "env",
    "command_environment",
    "shell",
    "process_authority",
    "bypass_approval",
    "tool_modes",
    "provided_tool_mode",
    "subagent_max_children",
    "subagent_max_concurrent_turns",
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
    "MalformedToolCallReason",
    "MalformedToolCallsSink",
    "MalformedToolCallsTelemetry",
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
