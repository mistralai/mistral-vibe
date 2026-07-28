from __future__ import annotations

import base64
from collections.abc import Callable
from typing import Any

from mistralai.client import Mistral
from mistralai.client.models import SpeechOutputFormat

from vibe.app_server.config import AudioProviderView, TTSModelConfigView
from vibe.cli.tts.tts_client_port import TTSResult
from vibe.utils.api_keys import resolve_api_key
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context, get_user_agent


class MistralTTSClient:
    def __init__(
        self,
        provider: AudioProviderView,
        model: TTSModelConfigView,
        metadata_getter: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._api_key = resolve_api_key(provider.api_key_env_var) or ""
        self._server_url = provider.api_base
        self._model_name = model.name
        self._voice = model.voice
        self._response_format: SpeechOutputFormat = model.response_format
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

    async def speak(self, text: str) -> TTSResult:
        client = self._get_client()
        metadata = self._metadata_getter()
        response = await client.audio.speech.complete_async(
            model=self._model_name,
            input=text,
            voice_id=self._voice,
            response_format=self._response_format,
            metadata=metadata,
            http_headers={"user-agent": get_user_agent("mistral")},
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
