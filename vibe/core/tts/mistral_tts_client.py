from __future__ import annotations

import base64
import os

import httpx
from mistralai.client import Mistral
from mistralai.client.models import SpeechOutputFormat

from vibe.core.config import TTSModelConfig, TTSProviderConfig
from vibe.core.tts.tts_client_port import TTSResult
from vibe.core.utils.http import build_ssl_context


class MistralTTSClient:
    def __init__(self, provider: TTSProviderConfig, model: TTSModelConfig) -> None:
        self._api_key = os.getenv(provider.api_key_env_var, "")
        self._server_url = provider.api_base
        self._model_name = model.name
        self._voice = model.voice
        self._response_format: SpeechOutputFormat = model.response_format
        self._client: Mistral | None = None
        self._http_client: httpx.AsyncClient | None = None

    def _get_client(self) -> Mistral:
        if self._client is None:
            self._http_client = httpx.AsyncClient(
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
        response = await client.audio.speech.complete_async(
            model=self._model_name,
            input=text,
            voice_id=self._voice,
            response_format=self._response_format,
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
