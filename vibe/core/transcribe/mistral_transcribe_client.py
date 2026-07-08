from __future__ import annotations

from collections.abc import AsyncIterator
import json

from mistralai.client import Mistral
from mistralai.client.models import (
    AudioFormat,
    RealtimeTranscriptionError,
    RealtimeTranscriptionSessionCreated,
    TranscriptionStreamDone,
    TranscriptionStreamTextDelta,
)
from mistralai.extra.realtime import UnknownRealtimeEvent

from vibe.core.config import (
    TranscribeModelConfig,
    TranscribeProviderConfig,
    resolve_api_key,
)
from vibe.core.config.models import Backend
from vibe.core.telemetry.build_metadata import build_request_metadata
from vibe.core.transcribe.transcribe_client_port import (
    TranscribeDone,
    TranscribeError,
    TranscribeEvent,
    TranscribeSessionCreated,
    TranscribeTextDelta,
)
from vibe.core.utils.http import VibeAsyncHTTPClient, build_ssl_context, get_user_agent


class MistralTranscribeClient:
    def __init__(
        self, provider: TranscribeProviderConfig, model: TranscribeModelConfig
    ) -> None:
        self._api_key = resolve_api_key(provider.api_key_env_var) or ""
        self._server_url = provider.api_base
        self._model_name = model.name
        self._audio_format = AudioFormat(
            encoding=model.encoding, sample_rate=model.sample_rate
        )
        self._target_streaming_delay_ms = model.target_streaming_delay_ms
        self._client: Mistral | None = None
        self._http_client: VibeAsyncHTTPClient | None = None

    def _get_client(self) -> Mistral:
        if self._client is None:
            self._http_client = VibeAsyncHTTPClient(
                verify=build_ssl_context(), follow_redirects=True
            )
            self._client = Mistral(
                api_key=self._api_key,
                server_url=self._server_url,
                async_client=self._http_client,
            )
        return self._client

    async def transcribe(
        self, audio_stream: AsyncIterator[bytes]
    ) -> AsyncIterator[TranscribeEvent]:
        client = self._get_client()
        metadata = build_request_metadata(
            launch_context=None, session_id=None, call_type="secondary_call"
        ).model_dump(exclude_none=True)
        async for event in client.audio.realtime.transcribe_stream(
            audio_stream=audio_stream,
            model=self._model_name,
            audio_format=self._audio_format,
            target_streaming_delay_ms=self._target_streaming_delay_ms,
            http_headers={
                "user-agent": get_user_agent(Backend.MISTRAL),
                "x-metadata": json.dumps(metadata),
            },
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

    async def close(self) -> None:
        client = self._client
        http_client = self._http_client
        self._client = None
        self._http_client = None
        try:
            if client is not None:
                await client.__aexit__(exc_type=None, exc_val=None, exc_tb=None)
        finally:
            if http_client is not None:
                await http_client.aclose()
