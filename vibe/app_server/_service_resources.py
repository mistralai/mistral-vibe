from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Literal

from vibe.app_server._model import validate_wire
from vibe.app_server.client_state import ClientSessionState
from vibe.app_server.connection import AppServerResourceConnection
from vibe.app_server.protocol import (
    EmptyResponse,
    FeedbackRecordParams,
    FeedbackShouldShowParams,
    FeedbackShouldShowResponse,
    NarrationSummarizeParams,
    NarrationSummarizeResponse,
    TelemetryRecordParams,
)
from vibe.app_server.telemetry_port import ClientTelemetryEvent


class NarrationResource:
    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state

    async def summarize(
        self,
        *,
        user_message: str,
        assistant_text: str,
        error: str | None,
        message_id: str | None,
    ) -> str | None:
        client = await self._connection.connect()
        response = validate_wire(
            NarrationSummarizeResponse,
            await client.request(
                "narration/summarize",
                NarrationSummarizeParams(
                    session_id=self._state.session_id,
                    user_message=user_message,
                    assistant_text=assistant_text,
                    error=error,
                    message_id=message_id,
                ),
            ),
        )
        return response.summary


class TelemetryResource:
    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state
        self._tasks: set[asyncio.Task[None]] = set()

    def record(
        self,
        name: str,
        properties: Mapping[str, object] | None = None,
        *,
        correlate_last_request: bool = False,
    ) -> None:
        task = asyncio.create_task(
            self._record(name, properties, correlate_last_request)
        )
        self._tasks.add(task)
        task.add_done_callback(self._complete)

    async def flush(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    def log(self, event: ClientTelemetryEvent) -> None:
        self.record(
            event.name,
            event.properties,
            correlate_last_request=event.correlation_id is not None,
        )

    async def _record(
        self,
        name: str,
        properties: Mapping[str, object] | None,
        correlate_last_request: bool,
    ) -> None:
        client = await self._connection.connect()
        validate_wire(
            EmptyResponse,
            await client.request(
                "telemetry/record",
                TelemetryRecordParams.model_validate({
                    "session_id": self._state.session_id,
                    "name": name,
                    "properties": dict(properties or {}),
                    "correlate_last_request": correlate_last_request,
                }),
            ),
        )

    def _complete(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        if not task.cancelled():
            task.exception()


class FeedbackResource:
    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state

    async def should_show(self, *, pending_user_messages: int = 0) -> bool:
        client = await self._connection.connect()
        response = validate_wire(
            FeedbackShouldShowResponse,
            await client.request(
                "feedback/shouldShow",
                FeedbackShouldShowParams(
                    session_id=self._state.session_id,
                    pending_user_messages=pending_user_messages,
                ),
            ),
        )
        return response.show

    async def record(self, action: Literal["asked", "given", "snoozed"]) -> None:
        client = await self._connection.connect()
        validate_wire(
            EmptyResponse,
            await client.request(
                "feedback/record",
                FeedbackRecordParams(session_id=self._state.session_id, action=action),
            ),
        )
