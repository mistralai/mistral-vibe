from __future__ import annotations

import base64
import os

import httpx

from vibe.core.config import TTSModelConfig, TTSProviderConfig
from vibe.core.tts.tts_client_port import TTSResult


class MistralTTSClient:
    def __init__(self, provider: TTSProviderConfig, model: TTSModelConfig) -> None:
        self._model_name = model.name
        self._voice = model.voice
        self._response_format = model.response_format
        self._client = httpx.AsyncClient(
            base_url=f"{provider.api_base}/v1",
            headers={
                "Authorization": f"Bearer {os.getenv(provider.api_key_env_var, '')}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

    async def speak(self, text: str) -> TTSResult:
        response = await self._client.post(
            "/audio/speech",
            json={
                "model": self._model_name,
                "input": text,
                "voice_id": self._voice,
                "stream": False,
                "response_format": self._response_format,
            },
        )
        response.raise_for_status()

        data = response.json()
        audio_bytes = base64.b64decode(data["audio_data"])
        return TTSResult(audio_data=audio_bytes)

    async def close(self) -> None:
        await self._client.aclose()
