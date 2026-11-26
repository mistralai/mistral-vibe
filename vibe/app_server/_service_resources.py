from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Literal

from vibe.app_server._model import validate_wire
from vibe.app_server.client_state import ClientSessionState
from vibe.app_server.connection import AppServerResourceConnection
from vibe.app_server.protocol import (
    VOICE_NARRATION_DONE_METHOD,
    VOICE_NARRATION_ERROR_METHOD,
    VOICE_NARRATION_PREP_METHOD,
    VOICE_TRANSCRIPTION_DELTA_METHOD,
    EmptyResponse,
    FeedbackRecordParams,
    FeedbackShouldShowParams,
    FeedbackShouldShowResponse,
    NarrationSummarizeParams,
    NarrationSummarizeResponse,
    Notification,
    TelemetryRecordParams,
    VoiceNarrateCancelParams,
    VoiceNarrateCancelResponse,
    VoiceNarrateParams,
    VoiceNarrateResponse,
    VoiceNarrationDoneParams,
    VoiceNarrationErrorParams,
    VoiceNarrationPrepParams,
    VoiceTranscribeCancelParams,
    VoiceTranscribeCancelResponse,
    VoiceTranscribeStartParams,
    VoiceTranscribeStartResponse,
    VoiceTranscribeStopParams,
    VoiceTranscribeStopResponse,
    VoiceTranscriptionDeltaParams,
)
from vibe.app_server.telemetry_port import ClientTelemetryEvent


class VoiceNotificationListener:
    """The pushed ``voice/*`` lifecycle a client renders.

    Base class rather than Protocol: a host registers one object and cares
    about a subset of the pushes, so unimplemented hooks are no-ops.
    """

    def on_transcription_delta(self, params: VoiceTranscriptionDeltaParams) -> None:
        pass

    def on_narration_prep(self, params: VoiceNarrationPrepParams) -> None:
        pass

    def on_narration_done(self, params: VoiceNarrationDoneParams) -> None:
        pass

    def on_narration_error(self, params: VoiceNarrationErrorParams) -> None:
        pass


class VoiceResource:
    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state
        self._listeners: list[VoiceNotificationListener] = []

    def add_listener(self, listener: VoiceNotificationListener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: VoiceNotificationListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    async def consume_notification(self, notification: Notification) -> bool:
        """Route one pushed ``voice/*`` notification to the listeners."""
        # Guarded cases: a bare-name case would be a capture pattern, not a
        # comparison with the imported method name.
        match notification:
            case _ if notification.method == VOICE_TRANSCRIPTION_DELTA_METHOD:
                params = validate_wire(
                    VoiceTranscriptionDeltaParams, notification.params
                )
                for listener in tuple(self._listeners):
                    listener.on_transcription_delta(params)
                return True
            case _ if notification.method == VOICE_NARRATION_PREP_METHOD:
                params = validate_wire(VoiceNarrationPrepParams, notification.params)
                for listener in tuple(self._listeners):
                    listener.on_narration_prep(params)
                return True
            case _ if notification.method == VOICE_NARRATION_DONE_METHOD:
                params = validate_wire(VoiceNarrationDoneParams, notification.params)
                for listener in tuple(self._listeners):
                    listener.on_narration_done(params)
                return True
            case _ if notification.method == VOICE_NARRATION_ERROR_METHOD:
                params = validate_wire(VoiceNarrationErrorParams, notification.params)
                for listener in tuple(self._listeners):
                    listener.on_narration_error(params)
                return True
            case _:
                return False

    # -- Transcription ─────────────────────────────────────────────

    async def transcribe_start(
        self, language: str = "en"
    ) -> VoiceTranscribeStartResponse:
        client = await self._connection.connect()
        return validate_wire(
            VoiceTranscribeStartResponse,
            await client.request(
                "voice/transcribeStart",
                VoiceTranscribeStartParams(
                    session_id=self._state.session_id, language=language
                ),
            ),
        )

    async def transcribe_stop(self) -> VoiceTranscribeStopResponse:
        client = await self._connection.connect()
        return validate_wire(
            VoiceTranscribeStopResponse,
            await client.request(
                "voice/transcribeStop",
                VoiceTranscribeStopParams(session_id=self._state.session_id),
            ),
        )

    async def transcribe_cancel(self) -> VoiceTranscribeCancelResponse:
        client = await self._connection.connect()
        return validate_wire(
            VoiceTranscribeCancelResponse,
            await client.request(
                "voice/transcribeCancel",
                VoiceTranscribeCancelParams(session_id=self._state.session_id),
            ),
        )

    # -- Narration ─────────────────────────────────────────────────

    async def narrate(
        self, user_message: str = "", assistant_text: str = ""
    ) -> VoiceNarrateResponse:
        client = await self._connection.connect()
        return validate_wire(
            VoiceNarrateResponse,
            await client.request(
                "voice/narrate",
                VoiceNarrateParams(
                    session_id=self._state.session_id,
                    user_message=user_message,
                    assistant_text=assistant_text,
                ),
            ),
        )

    async def narrate_cancel(self) -> VoiceNarrateCancelResponse:
        client = await self._connection.connect()
        return validate_wire(
            VoiceNarrateCancelResponse,
            await client.request(
                "voice/narrateCancel",
                VoiceNarrateCancelParams(session_id=self._state.session_id),
            ),
        )


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
