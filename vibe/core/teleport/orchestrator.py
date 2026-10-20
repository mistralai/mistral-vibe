from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import ValidationError

from vibe.core.telemetry.send import TelemetryClient
from vibe.core.telemetry.types import LaunchContext, ProjectPickerTelemetryPayload
from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.telemetry import TeleportTelemetryTracker
from vibe.core.teleport.types import (
    TeleportCompleteEvent,
    TeleportMessageContext,
    TeleportMessageContextSource,
    TeleportPushResponseEvent,
    TeleportSummarizingContextEvent,
    TeleportYieldEvent,
)
from vibe.core.types import LLMMessage

if TYPE_CHECKING:
    from vibe.core.teleport.teleport import TeleportService


@runtime_checkable
class TeleportContextSummarizer(Protocol):
    def resolve_prompt(self, prompt: str | None) -> str: ...
    def should_summarize(self, prompt: str | None) -> bool: ...
    def context_messages(self, prompt: str | None) -> list[LLMMessage]: ...
    async def summarize(
        self, messages: list[LLMMessage], prompt: str | None
    ) -> str: ...


class TeleportOrchestrator:
    def __init__(
        self,
        *,
        summarizer: TeleportContextSummarizer,
        teleport_service: TeleportService,
        telemetry_client: TelemetryClient,
        session_id: str,
        nb_session_messages: int,
        project_picker: ProjectPickerTelemetryPayload | None = None,
        launch_context: LaunchContext | None = None,
    ) -> None:
        self._summarizer = summarizer
        self._teleport_service = teleport_service
        self._telemetry_client = telemetry_client
        self._session_id = session_id
        self._nb_session_messages = nb_session_messages
        self._project_picker = project_picker
        self._launch_context = launch_context

    async def execute(
        self, prompt: str | None, *, project_id: str | None = None
    ) -> AsyncGenerator[TeleportYieldEvent, TeleportPushResponseEvent | None]:
        resolved_prompt = self._summarizer.resolve_prompt(prompt)
        telemetry_tracker = TeleportTelemetryTracker(
            telemetry_client=self._telemetry_client,
            nb_session_messages=self._nb_session_messages,
            stage="no_history" if not resolved_prompt else "git_check",
            project_picker=self._project_picker,
        )
        try:
            teleport_message_context: TeleportMessageContext | None = None
            if resolved_prompt and self._summarizer.should_summarize(prompt):
                summary_event = TeleportSummarizingContextEvent()
                telemetry_tracker.record_event(summary_event)
                yield summary_event
                try:
                    messages = self._summarizer.context_messages(prompt)
                    message_context = await self._summarizer.summarize(messages, prompt)
                except ServiceTeleportError:
                    telemetry_tracker.record_context_summary_failed()
                    raise
                except Exception as e:
                    telemetry_tracker.record_context_summary_failed()
                    raise ServiceTeleportError(
                        "Failed to summarize context for teleport.",
                        telemetry_details={"failure_kind": "context_summary_failed"},
                    ) from e

                teleport_message_context = self._build_message_context(
                    message_context, telemetry_tracker
                )
            async with self._teleport_service:
                gen = self._teleport_service.execute(
                    prompt=resolved_prompt,
                    project_id=project_id,
                    message_context=teleport_message_context,
                    conversation_id=self._session_id,
                )
                response: TeleportPushResponseEvent | None = None
                while True:
                    try:
                        event = await gen.asend(response)
                        telemetry_tracker.record_event(event)
                        if isinstance(event, TeleportCompleteEvent):
                            telemetry_tracker.send_success()
                        response = yield event
                    except StopAsyncIteration:
                        break
        except ServiceTeleportError as e:
            telemetry_tracker.record_service_error(e)
            raise
        except (asyncio.CancelledError, GeneratorExit):
            telemetry_tracker.record_cancelled()
            raise
        except Exception as e:
            telemetry_tracker.record_unexpected_error(e)
            raise
        finally:
            telemetry_tracker.send_failure_if_needed()

    def _build_message_context(
        self, summary: str, telemetry_tracker: TeleportTelemetryTracker
    ) -> TeleportMessageContext | None:
        try:
            message_context = TeleportMessageContext(
                summary=summary, source=self._message_context_source()
            )
        except ValidationError:
            telemetry_tracker.record_context_summary_failed()
            return None

        telemetry_tracker.record_context_summary_generated(summary)
        return message_context

    def _message_context_source(self) -> TeleportMessageContextSource:
        if self._launch_context is None:
            return TeleportMessageContextSource()
        return TeleportMessageContextSource(
            entrypoint=self._launch_context.agent_entrypoint,
            client_name=self._launch_context.client_name,
        )
