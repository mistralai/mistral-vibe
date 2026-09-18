from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING

from vibe.app_server.models import (
    AccountView,
    FileImageSource,
    InlineImageSource,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicReasoningEntry,
)
from vibe.core.compaction.context import (
    extract_summary,
    render_teleport_summary_request,
)
from vibe.core.config import VibeConfigSchema
from vibe.core.llm.backend.factory import create_backend
from vibe.core.telemetry.build_metadata import build_request_metadata
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import LaunchContext, ProjectPickerTelemetryPayload
from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.orchestrator import TeleportContextSummarizer
from vibe.core.teleport.types import (
    TELEPORT_MESSAGE_CONTEXT_MAX_LENGTH,
    TeleportPushResponseEvent,
    TeleportYieldEvent,
)
from vibe.core.types import (
    Backend,
    FileImageSource as CoreFileImageSource,
    ImageAttachment,
    InlineImageSource as CoreInlineImageSource,
    LLMMessage,
    Role,
)
from vibe.utils.api_keys import resolve_api_key
from vibe.utils.http import get_user_agent

if TYPE_CHECKING:
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
    )


def _convert_image_source(
    source: FileImageSource | InlineImageSource,
) -> CoreFileImageSource | CoreInlineImageSource:
    if isinstance(source, FileImageSource):
        return CoreFileImageSource(path=Path(source.path))
    return CoreInlineImageSource(data=source.data)


def projections_to_messages(entries: list[PublicHistoryEntry]) -> list[LLMMessage]:
    """Convert projected history entries to text-centric LLMMessage objects.

    Tool-call structure is stripped (no orphan tool_call_id), and effects,
    callbacks, notices, and checkpoints are skipped entirely. Reasoning
    entries are folded into the following assistant message's content to
    avoid consecutive assistant roles that would fail provider validation.
    """
    messages: list[LLMMessage] = []
    pending_reasoning: str | None = None
    for entry in entries:
        if isinstance(entry, PublicReasoningEntry):
            if entry.text:
                pending_reasoning = (
                    f"{pending_reasoning}\n\n{entry.text}"
                    if pending_reasoning
                    else entry.text
                )
            continue
        if not isinstance(entry, PublicMessageEntry):
            continue
        text = entry.text
        if entry.role == "system":
            if text:
                messages.append(LLMMessage(role=Role.system, content=text))
        elif entry.role == "user":
            images = [
                ImageAttachment(
                    source=_convert_image_source(img.source),
                    alias=img.alias,
                    mime_type=img.mime_type,
                )
                for img in entry.images
            ] or None
            messages.append(
                LLMMessage(role=Role.user, content=text or "", images=images)
            )
        elif entry.role == "assistant":
            if pending_reasoning and text:
                text = f"{pending_reasoning}\n\n{text}"
            elif pending_reasoning and not text:
                text = pending_reasoning
            pending_reasoning = None
            if text:
                messages.append(LLMMessage(role=Role.assistant, content=text))
    if pending_reasoning:
        messages.append(LLMMessage(role=Role.assistant, content=pending_reasoning))
    return messages


def resolve_last_user_prompt(entries: list[PublicHistoryEntry]) -> str:
    """Resolve the prompt from the last real user message.

    ``turn_start`` and ``turn_steer`` are both user-authored messages
    (steered messages are sent mid-turn but are still real user input).
    Only ``harness`` messages are system-injected and excluded, matching
    the legacy ``not m.injected`` filter.
    """
    for entry in reversed(entries):
        if not isinstance(entry, PublicMessageEntry):
            continue
        if entry.role != "user":
            continue
        if entry.source == "harness":
            continue
        return entry.text or ""
    return ""


class UnifiedTeleportContextSummarizer:
    """TeleportContextSummarizer backed by projected history + create_backend."""

    def __init__(self, adapter: UnifiedHarnessBackendAdapter) -> None:
        self._adapter = adapter
        self._cached_entries: list[PublicHistoryEntry] | None = None

    def set_cached_entries(self, entries: list[PublicHistoryEntry]) -> None:
        self._cached_entries = entries

    def _entries(self) -> list[PublicHistoryEntry]:
        if self._cached_entries is None:
            return []
        return self._cached_entries

    def resolve_prompt(self, prompt: str | None) -> str:
        if prompt:
            return prompt
        return resolve_last_user_prompt(self._entries())

    def should_summarize(self, prompt: str | None) -> bool:
        messages = self.context_messages(prompt)
        return any(_is_teleport_context_message(m) for m in messages)

    def context_messages(self, prompt: str | None) -> list[LLMMessage]:
        messages = projections_to_messages(self._entries())
        if not prompt:
            excluded = _last_user_message_from(messages)
            if excluded is not None:
                messages = [m for m in messages if m is not excluded]
        return messages

    async def summarize(self, messages: list[LLMMessage], prompt: str | None) -> str:
        config = self._adapter.config
        compaction_model = config.get_compaction_model()
        provider = next(
            (p for p in config.providers if p.name == compaction_model.provider), None
        )
        if provider is None:
            raise ServiceTeleportError(
                "Failed to summarize context for teleport.",
                telemetry_details={"failure_kind": "provider_not_found"},
            )
        if provider.api_key_env_var and not resolve_api_key(provider.api_key_env_var):
            raise ServiceTeleportError(
                "Failed to summarize context for teleport.",
                telemetry_details={"failure_kind": "no_api_key"},
            )
        source_messages = [m.model_copy(deep=True) for m in messages]
        resolved_prompt = prompt or resolve_last_user_prompt(self._entries())
        summary_request = render_teleport_summary_request(
            config.compaction_prompt,
            resolved_prompt,
            max_summary_chars=TELEPORT_MESSAGE_CONTEXT_MAX_LENGTH,
        )
        summary_messages = [
            *source_messages,
            LLMMessage(role=Role.user, content=summary_request),
        ]
        metadata = build_request_metadata(
            launch_context=self._adapter.launch_context,
            session_id=self._adapter.session_id,
            call_type="secondary_call",
        ).model_dump(exclude_none=True)
        backend = create_backend(
            provider=provider,
            timeout=config.api_timeout,
            retry_max_elapsed_time=config.api_retry_max_elapsed_time,
            connect_timeout=config.api_connect_timeout,
            write_timeout=config.api_write_timeout,
            pool_timeout=config.api_pool_timeout,
        )
        async with backend:
            result = await backend.complete(
                model=compaction_model,
                messages=summary_messages,
                temperature=0.0,
                tools=None,
                tool_choice=None,
                max_tokens=512,
                extra_headers={"user-agent": get_user_agent(Backend.MISTRAL)},
                metadata=metadata,
            )
        raw_content = (result.message.content or "").strip()
        if result.message.tool_calls or not raw_content:
            raise ServiceTeleportError(
                "Failed to summarize context for teleport.",
                telemetry_details={"failure_kind": "context_summary_failed"},
            )
        return extract_summary(raw_content) or raw_content


def _is_teleport_context_message(message: LLMMessage) -> bool:
    if message.role == Role.system:
        return False
    return bool(
        message.content
        or message.reasoning_content
        or message.tool_calls
        or message.tool_call_id
        or message.images
    )


def _last_user_message_from(messages: list[LLMMessage]) -> LLMMessage | None:
    return next(
        (m for m in reversed(messages) if m.role == Role.user and not m.injected), None
    )


class UnifiedVibeCodeSession:
    """VibeCodeSession backed by the Unified Harness adapter."""

    def __init__(self, adapter: UnifiedHarnessBackendAdapter) -> None:
        self._adapter = adapter
        self._summarizer = UnifiedTeleportContextSummarizer(adapter)
        self._active_teleport: str | None = None

    @property
    def config(self) -> VibeConfigSchema:
        return self._adapter.config

    @property
    def cwd(self) -> Path:
        return self._adapter._cwd_path()

    @property
    def session_id(self) -> str:
        return self._adapter.session_id

    @property
    def telemetry_client(self) -> TelemetryClient:
        return self._adapter._telemetry

    @property
    def launch_context(self) -> LaunchContext | None:
        return self._adapter._launch_context

    @property
    def summarizer(self) -> TeleportContextSummarizer:
        return self._summarizer

    async def read_account(self) -> AccountView:
        return await self._adapter._read_account()

    def _cached_entries(self) -> list[PublicHistoryEntry]:
        if self._summarizer._cached_entries is not None:
            return self._summarizer._cached_entries
        state = self._adapter._translated_state
        if state is not None:
            return state.history or []
        return []

    def session_message_count(self) -> int:
        entries = self._cached_entries()
        return len(entries)

    def has_conversation_history(self) -> bool:
        entries = self._cached_entries()
        return any(
            isinstance(e, PublicMessageEntry) and e.role != "system" for e in entries
        )

    def require_idle(self) -> None:
        self._adapter._require_idle()

    def begin_teleport(self, operation_id: str) -> None:
        self._adapter._begin_teleport(operation_id)

    def finish_teleport(self, operation_id: str) -> None:
        self._adapter._finish_teleport(operation_id)

    def teleport_events(
        self,
        prompt: str | None,
        *,
        project_id: str | None = None,
        project_picker: ProjectPickerTelemetryPayload | None = None,
    ) -> AsyncGenerator[TeleportYieldEvent, TeleportPushResponseEvent | None]:
        return self._adapter._run_teleport_orchestration(
            prompt, project_id=project_id, project_picker=project_picker
        )
