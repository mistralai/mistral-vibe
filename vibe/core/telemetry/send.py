from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urljoin

import httpx

from vibe import __version__
from vibe.core.config import ProviderConfig, VibeConfigSchema
from vibe.core.config.admin_config import AdminConfigOutcome
from vibe.core.telemetry.build_metadata import build_base_metadata
from vibe.core.telemetry.types import (
    AttachmentKind,
    ExperimentAssignment,
    LaunchContext,
    ProjectPickerTelemetryPayload,
    RemoteProjectOutcome,
    TelemetryCallType,
    TeleportContextSummaryStatus,
    TeleportFailureDetails,
    TeleportFailureStage,
)
from vibe.observability.logging import logger
from vibe.utils.api_keys import resolve_api_key
from vibe.utils.http import (
    VibeAsyncHTTPClient,
    build_ssl_context,
    get_server_url_from_api_base,
    get_user_agent,
)

if TYPE_CHECKING:
    from vibe.core.experiments.active import ExperimentSurface
    from vibe.core.experiments.models import ExperimentAttributes

_DEFAULT_TELEMETRY_BASE_URL = "https://api.mistral.ai"
_DATALAKE_EVENTS_PATH = "/v1/datalake/events"

type SubagentOperation = Literal[
    "list", "list_configs", "spawn", "wait", "send_message", "interrupt", "close"
]
type SubagentOutcome = Literal["success", "failure", "timeout"]
type SubagentProfileSource = Literal["generic", "vibe_profile"]


def get_mistral_provider_and_api_key(
    config: VibeConfigSchema,
) -> tuple[ProviderConfig, str] | None:
    """Resolve a Mistral provider and its API key, or None.

    Prefers the active provider when it is a Mistral provider; otherwise
    falls back to the first configured Mistral provider. Returns a key
    only when a Mistral provider is found, to avoid leaking third-party
    credentials to Mistral-controlled endpoints (telemetry, A/B test
    evaluation, ...).
    """
    try:
        provider = config.get_mistral_provider()
    except Exception:
        return None
    if provider is None:
        return None
    env_var = provider.api_key_env_var
    api_key = resolve_api_key(env_var)
    if api_key is None:
        return None
    return provider, api_key


def mistral_credential_cache_key(config: VibeConfigSchema) -> str | None:
    """Stable, anonymous per-user cache key derived from the Mistral API key.

    The single resolution for on-disk caches keyed by user (experiment eval,
    model catalog): None means no Mistral credential, so the caller has no
    entry to read or write.
    """
    from vibe.core.experiments.manager import hash_api_key

    provider_and_key = get_mistral_provider_and_api_key(config)
    if provider_and_key is None:
        return None
    _provider, api_key = provider_and_key
    return hash_api_key(api_key)


def _subagent_depth(caller_agent_path: str) -> int:
    """Depth of the subagent level an operation acts on: the caller's depth + 1.

    Agent paths are absolute (``/root``, ``/root/research``), so the number of
    segments is the caller's depth (root = 0) plus one.
    """
    segments = [segment for segment in caller_agent_path.split("/") if segment]
    if not caller_agent_path.startswith("/") or not segments:
        raise ValueError(f"not an absolute agent path: {caller_agent_path!r}")
    return len(segments)


def send_unified_subagent_tool_call_finished(
    client: TelemetryClient,
    *,
    operation: SubagentOperation,
    outcome: SubagentOutcome,
    model: str,
    caller_agent_path: str,
    profile_source: SubagentProfileSource | None = None,
    message_id: str | None = None,
) -> None:
    """Record one terminal Unified subagent operation without user content.

    Stateful subagent effects are produced by Harness Core rather than the
    legacy ``ResolvedToolCall`` path. Keep their analytics on the existing
    tool-finished event while exposing only the approved low-cardinality
    dimensions. In particular, never add agent names, child IDs, prompts,
    paths, or error messages here: ``caller_agent_path`` is reduced to the
    ``subagent_depth`` integer and never emitted.
    """
    core_operation = "stop" if operation == "close" else operation
    payload: dict[str, Any] = {
        # An analytics key, not a wire name: Core's tools are now `agents.*`,
        # but this keeps the pre-rename `subagent.<op>` spelling on purpose so
        # dashboards built on it stay continuous. Do not rename it.
        "tool_name": f"subagent.{core_operation}",
        "status": "success" if outcome == "success" else "failure",
        "decision": None,
        "approval_type": None,
        "agent_profile_name": None,
        "model": model,
        "nb_files_created": 0,
        "nb_files_modified": 0,
        "file_extension": None,
        "message_id": message_id,
        "subagent_operation": operation,
        "subagent_outcome": outcome,
        "subagent_depth": _subagent_depth(caller_agent_path),
    }
    if profile_source is not None:
        payload["subagent_profile_source"] = profile_source
    client.send_telemetry_event("vibe.tool_call_finished", payload)


def send_unified_tool_call_finished(  # noqa: PLR0913
    client: TelemetryClient,
    *,
    tool_name: str,
    status: Literal["success", "failure", "skipped"],
    model: str,
    agent_profile_name: str | None,
    nb_files_created: int = 0,
    nb_files_modified: int = 0,
    file_extension: str | None = None,
    message_id: str | None = None,
    decision: Literal["execute", "skip"] | None = None,
    approval_type: Literal["always", "never", "ask"] | None = None,
    approval_source: Literal["config", "smart", "user", "bypass", "never"]
    | None = None,
) -> None:
    """Record one terminal ordinary (non-subagent) Unified tool call.

    The harness runs tool calls in Core, so they never pass through the legacy
    ``ResolvedToolCall`` path. Hand-build the same ``vibe.tool_call_finished``
    payload the legacy loop emits, sourcing name/status/file counts from the
    reconciled effect and ``decision``/``approval_type``/``approval_source``
    from the effect state.
    """
    payload: dict[str, Any] = {
        "tool_name": tool_name,
        "status": status,
        "decision": decision,
        "approval_type": approval_type,
        "approval_source": approval_source,
        "agent_profile_name": agent_profile_name,
        "model": model,
        "nb_files_created": nb_files_created,
        "nb_files_modified": nb_files_modified,
        "file_extension": file_extension,
        "message_id": message_id,
    }
    client.send_telemetry_event("vibe.tool_call_finished", payload)


class TelemetryClient:
    def __init__(
        self,
        config_getter: Callable[[], VibeConfigSchema],
        session_id_getter: Callable[[], str | None] | None = None,
        parent_session_id_getter: Callable[[], str | None] | None = None,
        launch_context: LaunchContext | None = None,
        experiments_getter: Callable[[], list[ExperimentAssignment]] | None = None,
        user_plan_getter: Callable[[], str | None] | None = None,
        experiment_attributes_getter: (
            Callable[[], ExperimentAttributes | None] | None
        ) = None,
        harness_backend: ExperimentSurface | None = None,
    ) -> None:
        self._config_getter = config_getter
        self._session_id_getter = session_id_getter
        self._parent_session_id_getter = parent_session_id_getter
        self._launch_context = launch_context
        self._experiments_getter = experiments_getter
        self._user_plan_getter = user_plan_getter
        self._experiment_attributes_getter = experiment_attributes_getter
        self._harness_backend = harness_backend
        self._client: VibeAsyncHTTPClient | None = None
        self._pending_tasks: set[asyncio.Task[Any]] = set()
        self.last_correlation_id: str | None = None

    def _get_telemetry_url(self, api_base: str) -> str:
        base = get_server_url_from_api_base(api_base) or _DEFAULT_TELEMETRY_BASE_URL
        return urljoin(base.rstrip("/"), _DATALAKE_EVENTS_PATH)

    def _get_mistral_api_key(self) -> str | None:
        provider_and_api_key = get_mistral_provider_and_api_key(self._config_getter())
        if provider_and_api_key is None:
            return None
        _, api_key = provider_and_api_key
        return api_key

    def _is_enabled(self) -> bool:
        try:
            return self._config_getter().enable_telemetry
        except Exception:
            return False

    def is_active(self) -> bool:
        return self._is_enabled() and self._get_mistral_api_key() is not None

    @property
    def client(self) -> VibeAsyncHTTPClient:
        if self._client is None:
            self._client = VibeAsyncHTTPClient(
                timeout=httpx.Timeout(5.0),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
                verify=build_ssl_context(),
            )
        return self._client

    @property
    def session_id(self) -> str | None:
        if self._session_id_getter is None:
            return None
        return self._session_id_getter()

    @property
    def parent_session_id(self) -> str | None:
        if self._parent_session_id_getter is None:
            return None
        return self._parent_session_id_getter()

    @property
    def user_plan(self) -> str | None:
        if self._user_plan_getter is None:
            return None
        return self._user_plan_getter()

    def build_client_event_metadata(self) -> dict[str, Any]:
        experiment_assignments = (
            self._experiments_getter() if self._experiments_getter is not None else None
        )
        attributes = (
            self._experiment_attributes_getter()
            if self._experiment_attributes_getter is not None
            else None
        )
        return build_base_metadata(
            launch_context=self._launch_context,
            session_id=self.session_id,
            parent_session_id=self.parent_session_id,
            experiment_assignments=experiment_assignments,
            user_plan=self.user_plan,
            experiment_attributes=(
                # mode="json" so nested enums (harness, terminal_emulator)
                # serialize to their string values, matching the top-level fields
                # and keeping the payload/logs free of Python enum reprs.
                attributes.model_dump(mode="json", exclude_none=True)
                if attributes
                else None
            ),
            harness_backend=self._harness_backend,
        )

    def send_telemetry_event(
        self,
        event_name: str,
        properties: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> None:
        if not self._is_enabled():
            return
        provider_and_api_key = get_mistral_provider_and_api_key(self._config_getter())
        if provider_and_api_key is None:
            return
        provider, mistral_api_key = provider_and_api_key
        telemetry_url = self._get_telemetry_url(provider.api_base)
        user_agent = get_user_agent(provider.backend)
        properties = self.build_client_event_metadata() | properties
        logger.debug(
            "telemetry event=%s properties=%s correlation_id=%s",
            event_name,
            properties,
            correlation_id,
        )

        payload: dict[str, Any] = {"event": event_name, "properties": properties}
        if correlation_id:
            payload["correlation_id"] = correlation_id

        async def _send() -> None:
            try:
                await self.client.post(
                    telemetry_url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {mistral_api_key}",
                        "User-Agent": user_agent,
                    },
                )
            except Exception:
                pass  # Silently swallow all exceptions for fire-and-forget telemetry

        task = asyncio.create_task(_send())
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def aclose(self) -> None:
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def send_auto_compact_triggered(
        self,
        *,
        nb_context_tokens_before: int,
        auto_compact_threshold: int,
        status: Literal["success", "failure", "cancelled"],
        session_id: str | None = None,
        parent_session_id: str | None = None,
    ) -> None:
        payload = {
            "nb_context_tokens_before": nb_context_tokens_before,
            "auto_compact_threshold": auto_compact_threshold,
            "status": status,
        }
        if session_id is not None:
            payload["session_id"] = session_id
            payload["parent_session_id"] = parent_session_id
        self.send_telemetry_event("vibe.auto_compact_triggered", payload)

    def send_compaction_failed(
        self,
        *,
        reason: Literal["tool_call", "empty_summary"],
        session_id: str | None = None,
        parent_session_id: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"reason": reason}
        if session_id is not None:
            payload["session_id"] = session_id
            payload["parent_session_id"] = parent_session_id
        self.send_telemetry_event("vibe.compaction_failed", payload)

    def send_new_session(
        self,
        has_agents_md: bool,
        nb_skills: int,
        nb_mcp_servers: int,
        nb_models: int,
        *,
        has_outside_workdir_deny: bool,
        nb_post_agent_scoped_hooks: int,
        nb_post_agent_main_hooks: int,
    ) -> None:
        lc = self._launch_context
        payload = {
            "has_agents_md": has_agents_md,
            "nb_skills": nb_skills,
            "nb_mcp_servers": nb_mcp_servers,
            "nb_models": nb_models,
            "has_outside_workdir_deny": has_outside_workdir_deny,
            "nb_post_agent_scoped_hooks": nb_post_agent_scoped_hooks,
            "nb_post_agent_main_hooks": nb_post_agent_main_hooks,
            "entrypoint": lc.agent_entrypoint if lc else "unknown",
            "host_kind": "local",
            "version": __version__,
            "client_name": lc.client_name if lc else None,
            "client_version": lc.client_version if lc else None,
            "terminal_emulator": (
                lc.terminal_emulator.value
                if lc and lc.terminal_emulator is not None
                else None
            ),
        }
        self.send_telemetry_event("vibe.new_session", payload)

    def send_session_closed(self) -> None:
        self.send_telemetry_event("vibe.session_closed", {})

    def send_admin_config_applied(
        self,
        *,
        outcome: AdminConfigOutcome,
        enforced_keys: list[str] | None = None,
        error: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"outcome": outcome.value}
        if enforced_keys is not None:
            payload["nb_enforced_fields"] = len(enforced_keys)
        if error is not None:
            payload["has_error"] = True
        self.send_telemetry_event("vibe.admin_config_applied", payload)

    def send_onboarding_api_key_added(self, *, custom_domain: bool = False) -> None:
        self.send_telemetry_event(
            "vibe.onboarding_api_key_added",
            {"version": __version__, "custom_domain": custom_domain},
        )

    def send_request_sent(
        self,
        *,
        model: str,
        nb_context_chars: int,
        nb_context_messages: int,
        nb_prompt_chars: int,
        call_type: TelemetryCallType,
        message_id: str | None = None,
        attachment_counts: dict[AttachmentKind, int] | None = None,
    ) -> None:
        payload = {
            "model": model,
            "nb_context_chars": nb_context_chars,
            "nb_context_messages": nb_context_messages,
            "nb_prompt_chars": nb_prompt_chars,
            "call_source": "vibe_code",
            "call_type": call_type,
            "message_id": message_id,
            "attachment_counts": {
                kind.value: count
                for kind, count in (attachment_counts or {}).items()
                if count > 0
            },
        }
        self.send_telemetry_event("vibe.request_sent", payload)

    def send_tool_call_detected(
        self,
        *,
        model: str,
        backend: str,
        api_style: str,
        call_type: TelemetryCallType,
        reasons: list[str],
        nb_unusable_attempts: int,
        nb_dropped_tool_calls: int,
        status: Literal["success", "error"],
        message_id: str | None = None,
    ) -> None:
        """Record one model request whose responses broke the tool call contract.

        Sent once per request, when it ends, and only when the runtime had to
        repair at least one response. Carries counts and closed values only:
        never tool names, arguments, or response text.
        """
        payload = {
            "model": model,
            "backend": backend,
            "api_style": api_style,
            "call_source": "vibe_code",
            "call_type": call_type,
            "message_id": message_id,
            "reasons": reasons,
            "nb_unusable_attempts": nb_unusable_attempts,
            "nb_dropped_tool_calls": nb_dropped_tool_calls,
            "status": status,
        }
        self.send_telemetry_event("vibe.tool_call.detected", payload)

    def send_ready(self, *, init_duration_ms: int) -> None:
        payload = {"init_duration_ms": init_duration_ms}
        self.send_telemetry_event("vibe.ready", payload)

    def send_user_rating_feedback(self, rating: int, model: str) -> None:
        self.send_telemetry_event(
            "vibe.user_rating_feedback",
            {"rating": rating, "version": __version__, "model": model},
            correlation_id=self.last_correlation_id,
        )

    def send_teleport_completed(
        self,
        *,
        push_required: bool,
        nb_session_messages: int,
        context_summary: TeleportContextSummaryStatus = "skipped",
        context_summary_chars: int | None = None,
        project_picker: ProjectPickerTelemetryPayload | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "push_required": push_required,
            "nb_session_messages": nb_session_messages,
            "context_summary": context_summary,
            "context_summary_chars": context_summary_chars,
        }
        if project_picker is not None:
            payload.update(project_picker)
        self.send_telemetry_event("vibe.teleport_completed", dict(payload))

    def send_teleport_failed(
        self,
        *,
        stage: TeleportFailureStage,
        error_class: str,
        push_required: bool,
        nb_session_messages: int,
        context_summary: TeleportContextSummaryStatus = "skipped",
        context_summary_chars: int | None = None,
        error_details: TeleportFailureDetails | None = None,
        project_picker: ProjectPickerTelemetryPayload | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "stage": stage,
            "error_class": error_class,
            "push_required": push_required,
            "nb_session_messages": nb_session_messages,
            "context_summary": context_summary,
            "context_summary_chars": context_summary_chars,
            **(error_details or {}),
        }
        if project_picker is not None:
            payload.update(project_picker)
        self.send_telemetry_event("vibe.teleport_failed", dict(payload))

    def send_remote_project_configured(
        self,
        *,
        outcome: RemoteProjectOutcome,
        project_picker: ProjectPickerTelemetryPayload,
    ) -> None:
        payload: dict[str, object] = {"outcome": outcome, **project_picker}
        self.send_telemetry_event("vibe.remote_project_configured", dict(payload))
