from __future__ import annotations

import asyncio
from collections.abc import Callable
import os
from typing import TYPE_CHECKING, Any, Literal

import httpx

from vibe import __version__
from vibe.core.config import Backend, VibeConfig
from vibe.core.llm.format import ResolvedToolCall
from vibe.core.utils import get_user_agent

if TYPE_CHECKING:
    from vibe.core.agent_loop import ToolDecision

DATALAKE_EVENTS_URL = "https://codestral.mistral.ai/v1/datalake/events"


class TelemetryClient:
    def __init__(self, config_getter: Callable[[], VibeConfig]) -> None:
        self._config_getter = config_getter
        self._client: httpx.AsyncClient | None = None
        self._pending_tasks: set[asyncio.Task[Any]] = set()

    def _get_telemetry_user_agent(self) -> str:
        try:
            config = self._config_getter()
            active_model = config.get_active_model()
            provider = config.get_provider_for_model(active_model)
            return get_user_agent(provider.backend)
        except ValueError:
            return get_user_agent(None)

    def _get_mistral_api_key(self) -> str | None:
        """Get the current API key from the active provider.

        Only returns an API key if the provider is a Mistral provider
        to avoid leaking third-party credentials to the telemetry endpoint.
        """
        try:
            config = self._config_getter()
            model = config.get_active_model()
            provider = config.get_provider_for_model(model)
            if provider.backend != Backend.MISTRAL:
                return None
            env_var = provider.api_key_env_var
            return os.getenv(env_var) if env_var else None
        except ValueError:
            return None

    def _is_enabled(self) -> bool:
        """Check if telemetry is enabled in the current config."""
        try:
            return self._config_getter().enable_telemetry
        except ValueError:
            return False

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(5.0),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            )
        return self._client

    def send_telemetry_event(self, event_name: str, properties: dict[str, Any]) -> None:
        mistral_api_key = self._get_mistral_api_key()
        if mistral_api_key is None or not self._is_enabled():
            return
        user_agent = self._get_telemetry_user_agent()

        async def _send() -> None:
            try:
                await self.client.post(
                    DATALAKE_EVENTS_URL,
                    json={"event": event_name, "properties": properties},
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
    ) -> tuple[int, int]:
        nb_files_created = 0
        nb_files_modified = 0
        if status == "success" and result is not None:
            if tool_call.tool_name == "write_file":
                file_existed = result.get("file_existed", False)
                if file_existed:
                    nb_files_modified = 1
                else:
                    nb_files_created = 1
            elif tool_call.tool_name == "search_replace":
                nb_files_modified = 1 if result.get("blocks_applied", 0) > 0 else 0
        return nb_files_created, nb_files_modified

    def send_tool_call_finished(
        self,
        *,
        tool_call: ResolvedToolCall,
        status: Literal["success", "failure", "skipped"],
        decision: ToolDecision | None,
        agent_profile_name: str,
        result: dict[str, Any] | None = None,
    ) -> None:
        verdict_value = decision.verdict.value if decision else None
        approval_type_value = decision.approval_type.value if decision else None

        nb_files_created, nb_files_modified = self._calculate_file_metrics(
            tool_call, status, result
        )

        payload = {
            "tool_name": tool_call.tool_name,
            "status": status,
            "decision": verdict_value,
            "approval_type": approval_type_value,
            "agent_profile_name": agent_profile_name,
            "nb_files_created": nb_files_created,
            "nb_files_modified": nb_files_modified,
        }
        self.send_telemetry_event("vibe.tool_call_finished", payload)

    def send_user_copied_text(self, text: str) -> None:
        payload = {"text_length": len(text)}
        self.send_telemetry_event("vibe.user_copied_text", payload)

    def send_user_cancelled_action(self, action: str) -> None:
        payload = {"action": action}
        self.send_telemetry_event("vibe.user_cancelled_action", payload)

    def send_auto_compact_triggered(self) -> None:
        payload = {}
        self.send_telemetry_event("vibe.auto_compact_triggered", payload)

    def send_slash_command_used(
        self, command: str, command_type: Literal["builtin", "skill"]
    ) -> None:
        payload = {"command": command.lstrip("/"), "command_type": command_type}
        self.send_telemetry_event("vibe.slash_command_used", payload)

    def send_new_session(
        self,
        has_agents_md: bool,
        nb_skills: int,
        nb_mcp_servers: int,
        nb_models: int,
        entrypoint: Literal["cli", "acp", "programmatic", "unknown"],
        terminal_emulator: str | None = None,
    ) -> None:
        payload = {
            "has_agents_md": has_agents_md,
            "nb_skills": nb_skills,
            "nb_mcp_servers": nb_mcp_servers,
            "nb_models": nb_models,
            "entrypoint": entrypoint,
            "version": __version__,
            "terminal_emulator": terminal_emulator,
        }
        self.send_telemetry_event("vibe.new_session", payload)

    def send_onboarding_api_key_added(self) -> None:
        self.send_telemetry_event(
            "vibe.onboarding_api_key_added", {"version": __version__}
        )
