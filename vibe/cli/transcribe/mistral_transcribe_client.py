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

from vibe.app_server.config import AudioProviderView, TranscribeModelConfigView
from vibe.cli.audio_request_metadata import RequestMetadataGetter
from vibe.cli.transcribe.transcribe_client_port import (
    TranscribeDone,
    TranscribeError,
    TranscribeEvent,
    TranscribeSessionCreated,
    TranscribeTextDelta,
)
from vibe.utils.api_keys import resolve_api_key
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context, get_user_agent

# The server rejects an end-of-stream flush when no audio was captured (the user
# released before speaking). This is a benign empty recording, not a failure.
_EMPTY_RECORDING_MARKER = "before sending any audio bytes"


class MistralTranscribeClient:
    def __init__(
        self,
        provider: AudioProviderView,
        model: TranscribeModelConfigView,
        metadata_getter: RequestMetadataGetter | None = None,
    ) -> None:
        self._api_key = resolve_api_key(provider.api_key_env_var) or ""
        self._server_url = provider.api_base
        self._model_name = model.name
        self._audio_format = AudioFormat(
            encoding=model.encoding, sample_rate=model.sample_rate
        )
        self._target_streaming_delay_ms = model.target_streaming_delay_ms
        self._metadata_getter = metadata_getter or dict
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
        metadata = self._metadata_getter()
        completed = False
        stream_ended = False

        async def _tracked_audio() -> AsyncIterator[bytes]:
            nonlocal stream_ended
            async for chunk in audio_stream:
                yield chunk
            stream_ended = True

        try:
            async for event in client.audio.realtime.transcribe_stream(
                audio_stream=_tracked_audio(),
                model=self._model_name,
                audio_format=self._audio_format,
                target_streaming_delay_ms=self._target_streaming_delay_ms,
                http_headers={
                    "user-agent": get_user_agent("mistral"),
                    "x-metadata": json.dumps(metadata),
                },
            ):
                if isinstance(event, RealtimeTranscriptionSessionCreated):
                    yield TranscribeSessionCreated(request_id=event.session.request_id)
                elif isinstance(event, TranscriptionStreamTextDelta):
                    yield TranscribeTextDelta(text=event.text)
                elif isinstance(event, TranscriptionStreamDone):
                    completed = True
                    yield TranscribeDone()
                elif isinstance(event, RealtimeTranscriptionError):
                    message = str(event.error.message)
                    if _EMPTY_RECORDING_MARKER in message:
                        completed = True
                        yield TranscribeDone()
                    else:
                        yield TranscribeError(message=message)
                elif isinstance(event, UnknownRealtimeEvent):
                    continue
        except RuntimeError as exc:
            # The realtime SDK's send task flushes audio without re-checking
            # whether the connection was torn down first, so a normal end of
            # stream races into RuntimeError("Connection is closed"). It's the
            # SDK teardown race, not a failure — genuine errors arrive above as
            # RealtimeTranscriptionError events.
            if "Connection is closed" not in str(exc):
                raise
            # Only benign once the local audio stream has been fully sent. If it
            # drops mid-recording, surface an error so the caller stops the
            # recorder instead of going idle with the mic still open.
            if not stream_ended:
                yield TranscribeError(
                    message="Connection closed before the recording finished"
                )
                return
            if not completed:
                yield TranscribeDone()

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
