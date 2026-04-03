from __future__ import annotations

import base64

import httpx
import pytest

from vibe.core.config import TTSModelConfig, TTSProviderConfig
from vibe.core.tts import MistralTTSClient, TTSResult


def _make_provider() -> TTSProviderConfig:
    return TTSProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai",
        api_key_env_var="MISTRAL_API_KEY",
    )


def _make_model() -> TTSModelConfig:
    return TTSModelConfig(
        name="voxtral-mini-tts-latest",
        alias="voxtral-tts",
        provider="mistral",
        voice="gb_jane_neutral",
    )


class TestMistralTTSClientInit:
    def test_client_configured_with_base_url_and_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
        client = MistralTTSClient(_make_provider(), _make_model())
        assert str(client._client.base_url) == "https://api.mistral.ai/v1/"
        assert client._client.headers["authorization"] == "Bearer test-key"


class TestMistralTTSClient:
    @pytest.mark.asyncio
    async def test_speak_returns_decoded_audio(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

        raw_audio = b"fake-audio-data-for-testing"
        encoded_audio = base64.b64encode(raw_audio).decode()

        async def mock_post(self_client, url, **kwargs):
            assert url == "/audio/speech"
            body = kwargs["json"]
            assert body["model"] == "voxtral-mini-tts-latest"
            assert body["input"] == "Hello"
            assert body["voice_id"] == "gb_jane_neutral"
            assert body["stream"] is False
            assert body["response_format"] == "wav"
            return httpx.Response(
                status_code=200,
                json={"audio_data": encoded_audio},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

        client = MistralTTSClient(_make_provider(), _make_model())
        result = await client.speak("Hello")

        assert isinstance(result, TTSResult)
        assert result.audio_data == raw_audio
        await client.close()

    @pytest.mark.asyncio
    async def test_speak_raises_on_http_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

        async def mock_post(self_client, url, **kwargs):
            return httpx.Response(
                status_code=500,
                json={"error": "Internal Server Error"},
                request=httpx.Request("POST", url),
            )

        monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

        client = MistralTTSClient(_make_provider(), _make_model())
        with pytest.raises(httpx.HTTPStatusError):
            await client.speak("Hello")
        await client.close()

    @pytest.mark.asyncio
    async def test_close_closes_underlying_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

        client = MistralTTSClient(_make_provider(), _make_model())
        await client.close()
        assert client._client.is_closed
