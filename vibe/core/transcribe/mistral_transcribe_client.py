from __future__ import annotations

from collections.abc import AsyncIterator
import os

from mistralai.client import Mistral
from mistralai.client.models import (
    AudioFormat,
    RealtimeTranscriptionError,
    RealtimeTranscriptionSessionCreated,
    TranscriptionStreamDone,
    TranscriptionStreamTextDelta,
)
from mistralai.extra.realtime import UnknownRealtimeEvent

from vibe.core.config import TranscribeModelConfig, TranscribeProviderConfig
from vibe.core.transcribe.transcribe_client_port import (
    TranscribeDone,
    TranscribeError,
    TranscribeEvent,
    TranscribeSessionCreated,
    TranscribeTextDelta,
)


class MistralTranscribeClient:
    def __init__(
        self, provider: TranscribeProviderConfig, model: TranscribeModelConfig
    ) -> None:
        self._api_key = os.getenv(provider.api_key_env_var, "")
        self._server_url = provider.api_base
        self._model_name = model.name
        self._audio_format = AudioFormat(
            encoding=model.encoding, sample_rate=model.sample_rate
        )
        self._target_streaming_delay_ms = model.target_streaming_delay_ms
        self._client: Mistral | None = None

    def _get_client(self) -> Mistral:
        if self._client is None:
            self._client = Mistral(api_key=self._api_key, server_url=self._server_url)
        return self._client

    async def transcribe(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscribeEvent]:
        client = self._get_client()
        async for event in client.audio.realtime.transcribe_stream(
            audio_stream=audio_stream,
            model=self._model_name,
            audio_format=self._audio_format,
            target_streaming_delay_ms=self._target_streaming_delay_ms,
        ):
            if isinstance(event, RealtimeTranscriptionSessionCreated):
                yield TranscribeSessionCreated(request_id=event.session.request_id)
            elif isinstance(event, TranscriptionStreamTextDelta):
                yield TranscribeTextDelta(text=event.text)
            elif isinstance(event, TranscriptionStreamDone):
                yield TranscribeDone()
            elif isinstance(event, RealtimeTranscriptionError):
                yield TranscribeError(message=str(event.error.message))
            elif isinstance(event, UnknownRealtimeEvent):
                continue
