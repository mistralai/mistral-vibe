from __future__ import annotations

import base64

from mistralai.client import Mistral
from mistralai.client.models import SpeechOutputFormat

from vibe.core.config import TTSModelConfig, TTSProviderConfig, resolve_api_key
from vibe.core.config.models import Backend
from vibe.core.telemetry.build_metadata import build_request_metadata
from vibe.core.tts.tts_client_port import TTSResult
from vibe.core.utils.http import VibeAsyncHTTPClient, build_ssl_context, get_user_agent


class MistralTTSClient:
    def __init__(self, provider: TTSProviderConfig, model: TTSModelConfig) -> None:
        self._api_key = resolve_api_key(provider.api_key_env_var) or ""
        self._server_url = provider.api_base
        self._model_name = model.name
        self._voice = model.voice
        self._response_format: SpeechOutputFormat = model.response_format
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

    async def speak(self, text: str) -> TTSResult:
        client = self._get_client()
        metadata = build_request_metadata(
            launch_context=None, session_id=None, call_type="secondary_call"
        ).model_dump(exclude_none=True)
        response = await client.audio.speech.complete_async(
            model=self._model_name,
            input=text,
            voice_id=self._voice,
            response_format=self._response_format,
            metadata=metadata,
            http_headers={"user-agent": get_user_agent(Backend.MISTRAL)},
        )
        audio_bytes = base64.b64decode(response.audio_data)
        return TTSResult(audio_data=audio_bytes)

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
