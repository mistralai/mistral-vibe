from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urljoin

import httpx

from vibe import __version__
from vibe.core.config import AnyVibeConfig, ProviderConfig, resolve_api_key
from vibe.core.llm.format import ResolvedToolCall
from vibe.core.logger import logger
from vibe.core.telemetry.build_metadata import build_base_metadata
from vibe.core.telemetry.types import (
    AttachmentKind,
    LaunchContext,
    ProjectPickerTelemetryPayload,
    RemoteProjectOutcome,
    TelemetryCallType,
    TeleportContextSummaryStatus,
    TeleportFailureDetails,
    TeleportFailureStage,
)
from vibe.core.utils import get_server_url_from_api_base, get_user_agent
from vibe.core.utils.http import VibeAsyncHTTPClient, build_ssl_context

if TYPE_CHECKING:
    from vibe.core.agent_loop import ToolDecision

_DEFAULT_TELEMETRY_BASE_URL = "https://api.mistral.ai"
_DATALAKE_EVENTS_PATH = "/v1/datalake/events"


def get_mistral_provider_and_api_key(
    config: AnyVibeConfig,
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


def _extract_file_extension(path: object) -> str | None:
    if not isinstance(path, (str, Path)):
        return None
    suffix = Path(path).suffix.lower()
    return suffix or None


class TelemetryClient:
    def __init__(
        self,
        config_getter: Callable[[], AnyVibeConfig],
        session_id_getter: Callable[[], str | None] | None = None,
        parent_session_id_getter: Callable[[], str | None] | None = None,
        launch_context: LaunchContext | None = None,
        experiments_getter: Callable[[], dict[str, str]] | None = None,
        user_plan_getter: Callable[[], str | None] | None = None,
    ) -> None:
        self._config_getter = config_getter
        self._session_id_getter = session_id_getter
        self._parent_session_id_getter = parent_session_id_getter
        self._launch_context = launch_context
        self._experiments_getter = experiments_getter
        self._user_plan_getter = user_plan_getter
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
        experiments = (
            self._experiments_getter() if self._experiments_getter is not None else None
        )
        return build_base_metadata(
            launch_context=self._launch_context,
            session_id=self.session_id,
            parent_session_id=self.parent_session_id,
            experiments=experiments,
            user_plan=self.user_plan,
        )

    def _is_experimental_bash_tool_enabled(self) -> bool:
        try:
            return self._config_getter().experimental_bash_tool
        except Exception:
            return False

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

    def _calculate_file_metrics(
        self,
        tool_call: ResolvedToolCall,
        status: Literal["success", "failure", "skipped"],
        result: dict[str, Any] | None = None,
    ) -> tuple[int, int, str | None]:
        nb_files_created = 0
        nb_files_modified = 0
        file_extension: str | None = None
        if status == "success" and result is not None:
            match tool_call.tool_name:
                case "write_file":
                    nb_files_created = 1
                    file_extension = _extract_file_extension(
                        tool_call.args_dict.get("file_path")
                    )
                case "edit":
                    nb_files_modified = 1
                    file_extension = _extract_file_extension(
                        tool_call.args_dict.get("file_path")
                    )
                case "read_file":
                    file_extension = _extract_file_extension(
                        tool_call.args_dict.get("file_path")
                    )
        return nb_files_created, nb_files_modified, file_extension

    def send_tool_call_finished(
        self,
        *,
        tool_call: ResolvedToolCall,
        status: Literal["success", "failure", "skipped"],
        decision: ToolDecision | None,
        agent_profile_name: str,
        model: str,
        result: dict[str, Any] | None = None,
        message_id: str | None = None,
    ) -> None:
        verdict_value = decision.verdict.value if decision else None
        approval_type_value = decision.approval_type.value if decision else None

        nb_files_created, nb_files_modified, file_extension = (
            self._calculate_file_metrics(tool_call, status, result)
        )

        payload = {
            "tool_name": tool_call.tool_name,
            "status": status,
            "decision": verdict_value,
            "approval_type": approval_type_value,
            "agent_profile_name": agent_profile_name,
            "model": model,
            "nb_files_created": nb_files_created,
            "nb_files_modified": nb_files_modified,
            "file_extension": file_extension,
            "message_id": message_id,
        }
        self.send_telemetry_event("vibe.tool_call_finished", payload)

    def send_user_copied_text(self, text: str) -> None:
        payload = {"text_length": len(text)}
        self.send_telemetry_event("vibe.user_copied_text", payload)

    def send_user_cancelled_action(self, action: str) -> None:
        payload = {"action": action}
        self.send_telemetry_event("vibe.user_cancelled_action", payload)

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

    def send_slash_command_used(
        self, command: str, command_type: Literal["builtin", "skill"]
    ) -> None:
        payload = {"command": command.lstrip("/"), "command_type": command_type}
        self.send_telemetry_event("vibe.slash_command_used", payload)

    def send_new_session(
        self, has_agents_md: bool, nb_skills: int, nb_mcp_servers: int, nb_models: int
    ) -> None:
        lc = self._launch_context
        payload = {
            "has_agents_md": has_agents_md,
            "nb_skills": nb_skills,
            "nb_mcp_servers": nb_mcp_servers,
            "nb_models": nb_models,
            "entrypoint": lc.agent_entrypoint if lc else "unknown",
            "version": __version__,
            "client_name": lc.client_name if lc else None,
            "client_version": lc.client_version if lc else None,
            "terminal_emulator": (
                lc.terminal_emulator.value
                if lc and lc.terminal_emulator is not None
                else None
            ),
            "experimental_bash_tool": self._is_experimental_bash_tool_enabled(),
        }
        self.send_telemetry_event("vibe.new_session", payload)

    def send_session_closed(self) -> None:
        self.send_telemetry_event("vibe.session_closed", {})

    def send_onboarding_api_key_added(self) -> None:
        self.send_telemetry_event(
            "vibe.onboarding_api_key_added", {"version": __version__}
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

    def send_ready(self, *, init_duration_ms: int) -> None:
        payload = {"init_duration_ms": init_duration_ms}
        self.send_telemetry_event("vibe.ready", payload)

    def send_at_mention_inserted(
        self,
        *,
        nb_mentions: int,
        context_types: dict[str, int],
        file_extensions: dict[str, int] | None,
        message_id: str | None,
    ) -> None:
        payload: dict[str, Any] = {
            "nb_mentions": nb_mentions,
            "context_types": context_types,
            "file_extensions": file_extensions,
            "message_id": message_id,
        }
        self.send_telemetry_event("vibe.at_mention_inserted", payload)

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
